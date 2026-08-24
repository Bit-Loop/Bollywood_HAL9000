"""Map the installed Hermes TUI Gateway's structured events into four planes."""

from __future__ import annotations

import json
import hashlib
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from hal9000.sentience.capabilities.actions import ExactActionLedger
from hal9000.sentience.capabilities.registry import CapabilityRegistry, CapabilityTransition
from hal9000.sentience.capabilities.tasks import TaskLedger
from hal9000.sentience.degradation.engine import DegradationEngine
from hal9000.sentience.event_envelope import EventEnvelope, canonical_subject
from hal9000.sentience.events.coalescer import EventRunInput
from hal9000.sentience.events.redact import (
    bounded_redacted_record,
    redact_data,
    redact_text,
)
from hal9000.sentience.hermes.mcp_observer import McpCapabilityObserver
from hal9000.sentience.hermes.model_router_observer import ModelRouterObserver
from hal9000.sentience.interoception.streaming import OperationalMetricStore
from hal9000.sentience.models import (
    CapabilityLifecycle,
    EventOrigin,
    RetentionClass,
    Sensitivity,
    Severity,
    StoredEvent,
)
from hal9000.sentience.sketches.registry import SketchRegistry
from hal9000.sentience.storage.blob_store import BlobStore
from hal9000.sentience.storage.database import SentienceDatabase

_CAPABILITY_FOR_TOOL = {
    "terminal": "terminal",
    "shell": "terminal",
    "read_file": "filesystem_read",
    "search_files": "filesystem_read",
    "write_file": "filesystem_write",
    "patch": "filesystem_write",
    "apply_patch": "filesystem_write",
    "browser": "browser",
    "web_search": "network",
    "web_extract": "network",
    "codex": "codex",
    "delegate_task": "codex",
}
_CONSEQUENTIAL = (
    "terminal",
    "shell",
    "write",
    "patch",
    "delete",
    "remove",
    "send",
    "publish",
    "install",
    "deploy",
)


class HermesEventMapper:
    def __init__(
        self,
        database: SentienceDatabase,
        boot_id: str,
        registry: CapabilityRegistry,
        tasks: TaskLedger,
        actions: ExactActionLedger,
        degradation: DegradationEngine,
        event_bus,
        sketches: SketchRegistry,
        blobs: BlobStore,
        metrics: OperationalMetricStore,
        *,
        nominal_model: str,
        nominal_provider: str,
        integrity_degraded: bool = False,
    ) -> None:
        self.database = database
        self.boot_id = boot_id
        self.registry = registry
        self.tasks = tasks
        self.actions = actions
        self.degradation = degradation
        self.event_bus = event_bus
        self.sketches = sketches
        self.blobs = blobs
        self.metrics = metrics
        self.models = ModelRouterObserver(
            registry, nominal_model=nominal_model, nominal_provider=nominal_provider
        )
        blocked = (
            frozenset({"terminal", "filesystem_write", "approval_channel", "verification"})
            if integrity_degraded
            else frozenset()
        )
        self.mcp = McpCapabilityObserver(registry, blocked_capabilities=blocked)
        self.integrity_degraded = integrity_degraded
        self._task_by_session: OrderedDict[str, str] = OrderedDict()
        self._run_started_ns: OrderedDict[str, int] = OrderedDict()
        self._first_delta_seen: set[str] = set()
        self._tool_started_ns: OrderedDict[str, int] = OrderedDict()
        self._sketch_faults: OrderedDict[str, None] = OrderedDict()
        self._mapping_capacity = 1024
        self._lock = threading.RLock()

    def begin_task(self, session_id: str, prompt: str) -> str:
        with self._lock:
            lowered = prompt.lower()
            requirements: dict[str, tuple[CapabilityLifecycle, bool]] = {
                "primary_reasoning": (CapabilityLifecycle.READY, False),
                "persistent_memory": (CapabilityLifecycle.READY, False),
                "session_context": (CapabilityLifecycle.READY, False),
                "approval_channel": (CapabilityLifecycle.READY, True),
            }
            coding_markers = (
                "repo",
                "code",
                "implement",
                "debug",
                "test",
                "build",
                "file",
                "commit",
                "push",
                "install",
            )
            if any(marker in lowered for marker in coding_markers):
                requirements.update(
                    {
                        "codex": (CapabilityLifecycle.READY, False),
                        "terminal": (CapabilityLifecycle.READY, True),
                        "filesystem_read": (CapabilityLifecycle.READY, False),
                        "filesystem_write": (CapabilityLifecycle.READY, True),
                        "verification": (CapabilityLifecycle.READY, True),
                    }
                )
            if any(marker in lowered for marker in ("current", "latest", "web", "browse", "online")):
                requirements.update(
                    {
                        "network": (CapabilityLifecycle.READY, False),
                        "browser": (CapabilityLifecycle.READY, False),
                    }
                )
            task_id = self.registry.create_task(
                redact_text(prompt).strip()[:1000] or "Hermes interaction",
                risk_level="consequential" if any(
                    marker in lowered
                    for marker in ("modify", "write", "delete", "install", "deploy", "push")
                ) else "ordinary",
                requirements=requirements,
            )
            self._remember(self._task_by_session, session_id or "pending", task_id)
            return task_id

    def bind_pending_task(self, session_id: str) -> None:
        with self._lock:
            pending = self._task_by_session.pop("pending", None)
            if pending:
                self._remember(self._task_by_session, session_id, pending)

    def expect_model_selection(self, provider: str, model: str) -> None:
        self.models.expect_selection(provider, model)

    def approval_resolved(self, request_id: str, choice: str) -> bool:
        return self.actions.approval_resolved(request_id, choice=choice)

    def map(self, frame: dict[str, Any]) -> None:
        if not isinstance(frame, dict):
            self.database.record_dead_letter(
                "hermes.gateway", "structured frame is not an object", frame
            )
            return
        event_type = str(frame.get("type") or "")
        session_id = str(frame.get("session_id") or "")
        raw_payload = frame.get("payload")
        if (
            not event_type
            or len(event_type) > 255
            or len(session_id) > 512
            or (raw_payload is not None and not isinstance(raw_payload, dict))
        ):
            self.database.record_dead_letter(
                "hermes.gateway",
                "malformed structured frame",
                frame,
            )
            return
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        task_id = self._task_by_session.get(session_id) or self._task_by_session.get("pending")
        if event_type == "session.info":
            self.bind_pending_task(session_id)
            task_id = self._task_by_session.get(session_id)
            transitions = self.models.observe(payload, task_id=task_id)
            servers = payload.get("mcp_servers")
            transitions += self.mcp.observe_servers(
                [item for item in servers if isinstance(item, dict)]
                if isinstance(servers, list)
                else [],
                task_id=task_id,
            )
            transitions += self.mcp.observe(
                payload.get("tools") if isinstance(payload.get("tools"), dict) else {},
                task_id=task_id,
                lazy=bool(payload.get("lazy")),
            )
            if self._current_or_unknown("session_context") is not CapabilityLifecycle.READY:
                transitions += (
                    self.registry.transition(
                        "session_context",
                        CapabilityLifecycle.READY,
                        reason="Hermes session.info verified an active structured session",
                        evidence={"event": "session.info", "session_id": session_id},
                        task_id=task_id,
                        expected=not self.models.last_model,
                    ),
                )
            self._apply_degradation(transitions, payload)
            self._observe_usage(payload.get("usage"), session_id, task_id)
            self._append_exact(
                "hermes.session.info.observed",
                session_id or "session",
                {
                    "model": payload.get("model"),
                    "provider": payload.get("provider"),
                    "running": payload.get("running"),
                    "tool_count": len(payload.get("tools") or {}),
                    "desktop_contract": payload.get("desktop_contract"),
                },
                task_id=task_id,
                idempotency_key=f"hermes-session-info:{session_id}:{payload.get('model')}:"
                f"{payload.get('running')}:{len(payload.get('tools') or {})}",
            )
            return
        if event_type == "session.usage":
            self._observe_usage(payload, session_id, task_id)
            return
        if event_type in {"context.lost", "context.truncated", "session.context_lost"}:
            material = bool(
                payload.get("required")
                or payload.get("material")
                or payload.get("restoration_failed")
            )
            self._append_exact(
                "context.required.loss.observed" if material else "context.truncation.observed",
                session_id or "session",
                payload,
                task_id=task_id,
                severity=Severity.ERROR if material else Severity.WARNING,
                idempotency_key="context-event:"
                + hashlib.sha256(
                    json.dumps(redact_data(frame), sort_keys=True, default=str).encode()
                ).hexdigest(),
            )
            if material:
                transition = self.registry.transition(
                    "session_context",
                    CapabilityLifecycle.FAILED,
                    reason="Hermes reported loss of context required by the active task",
                    evidence={"event": event_type, "payload": redact_data(payload)},
                    task_id=task_id,
                    expected=False,
                    active_profile="hal-context-reduced",
                )
                self._apply_degradation((transition,), {"profile_name": "hal-context-reduced"})
            return
        if event_type in {"context.restored", "session.context_restored"}:
            transition = self.registry.transition(
                "session_context",
                CapabilityLifecycle.READY,
                reason="Hermes reported successful required-context restoration",
                evidence={"event": event_type},
                task_id=task_id,
                expected=False,
                active_profile="hal-full",
            )
            self._apply_degradation((transition,), {"profile_name": "hal-full"})
            return
        if event_type == "voice.transcript":
            transcript = redact_text(
                str(payload.get("text") or payload.get("transcript") or "")
            )[:16_000]
            if transcript:
                self._append_exact(
                    "audio.transcription.captured",
                    session_id or "session",
                    {"transcript": transcript, "raw_audio_retained": False},
                    task_id=task_id,
                    idempotency_key="voice-transcript:"
                    + hashlib.sha256(
                        (session_id + "\0" + transcript).encode()
                    ).hexdigest(),
                    retention_class=RetentionClass.EPISODIC,
                    sensitivity=Sensitivity.CONFIDENTIAL,
                )
            return
        if event_type in {"wake.detected", "voice.interrupted"}:
            self._append_exact(
                "audio.wake.detected" if event_type == "wake.detected" else "speech.output.interrupted",
                session_id or "audio",
                {**redact_data(payload), "raw_audio_retained": False},
                task_id=task_id,
                idempotency_key=f"{event_type}:"
                + hashlib.sha256(
                    json.dumps(redact_data(frame), sort_keys=True, default=str).encode()
                ).hexdigest(),
                retention_class=RetentionClass.EPISODIC,
            )
            return
        if event_type == "hal.prompt.undelivered":
            self._append_exact(
                "task.input.undelivered",
                task_id or session_id or "pending",
                payload,
                task_id=task_id,
                severity=Severity.WARNING,
                idempotency_key="undelivered-prompt:"
                + hashlib.sha256(
                    json.dumps(payload, sort_keys=True, default=str).encode()
                ).hexdigest(),
            )
            if task_id:
                self.tasks.interrupt(task_id, reason=str(payload.get("reason") or "input undelivered"))
            return
        if event_type == "message.start":
            message_id = str(payload.get("message_id") or payload.get("id") or task_id or session_id)
            self._remember(self._run_started_ns, message_id, time.monotonic_ns())
            self._first_delta_seen.discard(message_id)
            self._append_exact(
                "hermes.run.started",
                session_id or "session",
                {"message_id": payload.get("message_id") or payload.get("id")},
                task_id=task_id,
                idempotency_key=f"hermes-run-start:{session_id}:{payload.get('message_id') or payload.get('id') or task_id}",
            )
            return
        if event_type == "message.delta":
            message_id = str(payload.get("message_id") or payload.get("id") or task_id or session_id)
            started = self._run_started_ns.get(message_id)
            if started is not None and message_id not in self._first_delta_seen:
                self._first_delta_seen.add(message_id)
                self._observe_latency(
                    "time_to_first_token",
                    session_id or "session",
                    (time.monotonic_ns() - started) / 1_000_000,
                )
            return
        if event_type == "message.complete":
            status = str(payload.get("status") or "complete")
            message_id = str(payload.get("message_id") or payload.get("id") or task_id or session_id)
            started = self._run_started_ns.pop(message_id, None)
            self._first_delta_seen.discard(message_id)
            if started is not None:
                self._observe_latency(
                    "model_latency",
                    session_id or "session",
                    (time.monotonic_ns() - started) / 1_000_000,
                )
            self._observe_usage(payload.get("usage"), session_id, task_id)
            output_reference: str | None = None
            output_text = redact_text(str(payload.get("text") or "")).strip()
            if status == "complete" and output_text and self._is_currently_degraded():
                # Retain only the final, redacted conclusion. Streaming tokens
                # and private model reasoning remain transient.
                bounded_output = output_text[: min(self.blobs.maximum_blob_bytes, 64 * 1024)]
                output_blob = self.blobs.put_text(
                    bounded_output,
                    mime_type="text/plain; charset=utf-8",
                    sensitivity=Sensitivity.INTERNAL,
                    retention_class=RetentionClass.LONG,
                    owner_type="degraded_output",
                    owner_id=message_id,
                    relation="final_conclusion",
                    pin=True,
                )
                output_reference = output_blob.digest
            finished = self._append_exact(
                "hermes.run.finished",
                session_id or "session",
                {
                    "status": status,
                    "usage": payload.get("usage") or {},
                    "error": payload.get("error"),
                    "degraded_output_ref": output_reference,
                },
                task_id=task_id,
                severity=Severity.ERROR if status == "error" else Severity.INFO,
                idempotency_key=f"hermes-run-finish:{session_id}:{task_id}:{status}",
            )
            if output_reference:
                self._record_degraded_reference(
                    f"evidence:{output_reference}",
                    "final model conclusion was produced while required capabilities were degraded",
                )
            elif status == "complete" and self._is_currently_degraded():
                self._record_degraded_reference(
                    f"event:{finished.event_id}",
                    "model completion was produced while required capabilities were degraded",
                )
            if task_id:
                if status == "complete":
                    with self.database.read_connection() as connection:
                        uncertain = int(
                            connection.execute(
                                "SELECT count(*) FROM consequential_actions WHERE task_id=? "
                                "AND state NOT IN ('verified','failed')",
                                (task_id,),
                            ).fetchone()[0]
                        )
                    self.tasks.finish(task_id, outcome="Hermes run completed", verified=not uncertain)
                else:
                    self.tasks.interrupt(task_id, reason=status, cancelled=status == "interrupted")
                self._task_by_session.pop(session_id, None)
            return
        if event_type in {"approval.request", "sudo.request", "secret.request"}:
            request_id = str(payload.get("request_id") or "")
            if request_id:
                self.actions.approval_requested(
                    request_id,
                    description=str(payload.get("description") or payload.get("title") or event_type),
                    task_id=task_id,
                )
            return
        if event_type == "tool.start":
            self._tool_started(payload, session_id, task_id)
            return
        if event_type in {"tool.complete", "tool.error", "tool.failed"}:
            self._tool_finished(payload, session_id, task_id, event_type)
            return
        if event_type == "error":
            failure_text = redact_text(str(payload.get("message") or "backend error"))[:4000]
            self._append_exact(
                "hermes.backend.error",
                session_id or "gateway",
                {"message": payload.get("message"), "surface": payload.get("error_surface")},
                task_id=task_id,
                severity=Severity.ERROR,
                idempotency_key=f"hermes-error:{session_id}:{redact_text(str(payload.get('message') or ''))[:256]}",
            )
            self._record_failure(
                "hermes.backend",
                {
                    "message": failure_text,
                    "surface": payload.get("error_surface"),
                },
                session_id=session_id,
            )
            return
        # Token deltas and interim prose are deliberately transient. Other
        # structured progress is coalesced rather than appended one row at a time.
        if event_type not in {"message.delta", "message.interim"}:
            self.event_bus.publish_observation(
                EventRunInput(
                    source="hermes.gateway",
                    type=event_type or "unknown",
                    subject=session_id or "gateway",
                    severity=Severity.INFO,
                    observed_at=datetime.now(UTC),
                    normalized_template=f"Hermes {event_type or 'unknown'}",
                    redacted_payload=redact_data(payload),
                    task_id=task_id,
                    retention_class=RetentionClass.SHORT,
                    sensitivity=Sensitivity.INTERNAL,
                )
            )

    def backend_state(
        self, connected: bool, *, session_id: str = "", expected: bool = False
    ) -> None:
        task_id = self._task_by_session.get(session_id)
        current = self._current_or_unknown("primary_reasoning")
        if not connected and current is CapabilityLifecycle.READY:
            transition = self.registry.transition(
                "primary_reasoning",
                CapabilityLifecycle.DISCONNECTED,
                reason="Hermes Gateway structured transport disconnected",
                evidence={"transport": "websocket", "connected": False},
                task_id=task_id,
                active_profile="hal-offline",
                expected=expected,
            )
            self._apply_degradation((transition,), {"profile_name": "hal-offline"})
        elif connected and current is CapabilityLifecycle.DISCONNECTED:
            transition = self.registry.transition(
                "primary_reasoning",
                CapabilityLifecycle.RECOVERING,
                reason="Hermes Gateway transport reconnected; session revalidation pending",
                evidence={"transport": "websocket", "connected": True},
                task_id=task_id,
                expected=expected,
                active_profile="hal-reconnecting",
            )
            self._apply_degradation((transition,), {"profile_name": "hal-reconnecting"})

    def _tool_started(self, payload: dict, session_id: str, task_id: str | None) -> None:
        name = str(payload.get("name") or "tool")
        tool_id = str(payload.get("tool_id") or payload.get("tool_call_id") or "")
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        capability = self._capability_for_tool(name)
        consequential = self._is_consequential(name)
        if consequential and self.integrity_degraded:
            self._append_exact(
                "action.blocked.integrity",
                tool_id or name,
                {
                    "tool": name,
                    "reason": "exact continuity is not verified",
                    "Hermes_cancel_required": True,
                },
                task_id=task_id,
                severity=Severity.CRITICAL,
                idempotency_key=f"integrity-action-block:{session_id}:{tool_id or name}",
            )
        if task_id and capability:
            self.registry.set_task_requirement(
                task_id,
                capability,
                unsafe_if_lost=consequential,
                reason=f"Hermes began {name}",
            )
        if consequential and tool_id:
            approval_reference = str(payload.get("approval_id") or payload.get("request_id") or "")
            approval_id: str | None = None
            if approval_reference:
                with self.database.read_connection() as connection:
                    approval = connection.execute(
                        "SELECT approval_id FROM approvals WHERE approval_id=? OR hermes_request_id=?",
                        (approval_reference, approval_reference),
                    ).fetchone()
                approval_id = str(approval[0]) if approval else None
            self.actions.start_action(
                tool_id,
                action_type=name[:255],
                target=json.dumps(redact_data(args), separators=(",", ":"), sort_keys=True)[:4000],
                task_id=task_id,
                approval_id=approval_id,
            )
        if tool_id:
            self._remember(self._tool_started_ns, tool_id, time.monotonic_ns())
        self.event_bus.publish_observation(
            EventRunInput(
                source="hermes.gateway",
                type="hermes.tool.started",
                subject=name.replace(" ", "_")[:255],
                severity=Severity.NOTICE if consequential else Severity.INFO,
                observed_at=datetime.now(UTC),
                normalized_template=f"Hermes tool started: {name}",
                redacted_payload={"tool_id": tool_id, "args": redact_data(args)},
                task_id=task_id,
                retention_class=RetentionClass.SHORT,
                sensitivity=Sensitivity.INTERNAL,
            )
        )
        try:
            self.sketches.update_distinct(
                "unique_tools_invoked_per_session",
                session_id or "session",
                name,
                datetime.now(UTC),
            )
        except Exception as exc:
            self._record_sketch_fault("unique_tools_invoked_per_session", exc)

    def _tool_finished(
        self, payload: dict, session_id: str, task_id: str | None, event_type: str
    ) -> None:
        name = str(payload.get("name") or "tool")
        tool_id = str(payload.get("tool_id") or payload.get("tool_call_id") or "")
        result = payload.get("result")
        failure = event_type != "tool.complete" or bool(payload.get("error")) or (
            isinstance(result, dict) and bool(result.get("error"))
        )
        duration_s = payload.get("duration_s")
        if duration_s is None and tool_id:
            started = self._tool_started_ns.pop(tool_id, None)
            if started is not None:
                duration_s = (time.monotonic_ns() - started) / 1_000_000_000
        elif tool_id:
            self._tool_started_ns.pop(tool_id, None)
        if duration_s is not None:
            try:
                self._observe_latency(
                    "tool_latency", session_id or "session", float(duration_s) * 1000
                )
            except (TypeError, ValueError):
                pass
        with self.database.read_connection() as connection:
            action = connection.execute(
                "SELECT action_id FROM consequential_actions WHERE tool_call_id=?", (tool_id,)
            ).fetchone()
        if action:
            text = json.dumps(redact_data(result), ensure_ascii=False, default=str)
            blob = self.blobs.put_text(
                text[: min(self.blobs.maximum_blob_bytes, 256 * 1024)],
                mime_type="application/json",
                sensitivity=Sensitivity.INTERNAL,
                retention_class=RetentionClass.FOREVER,
                owner_type="consequential_action",
                owner_id=str(action["action_id"]),
                relation="tool_result",
                pin=True,
            )
            self.actions.finish_action(
                tool_id,
                succeeded=not failure,
                summary=str(payload.get("summary") or ("tool failed" if failure else "tool completed")),
                payload_ref=blob.digest,
            )
            self._record_degraded_reference(
                f"action:{action['action_id']}",
                "consequential action outcome was recorded while required capabilities were degraded",
            )
        verification_for = str(
            payload.get("verification_for")
            or payload.get("verifies_action_id")
            or ""
        )
        if verification_for and not failure:
            outcome = str(payload.get("verification_outcome") or "success").lower()
            if outcome in {"success", "failed", "uncertain"}:
                self.actions.verify_action(
                    verification_for,
                    outcome=outcome,
                    statement=str(
                        payload.get("summary")
                        or "Hermes structured tool event reported verification"
                    ),
                )
        if failure:
            self._append_exact(
                "hermes.tool.failed",
                name.replace(" ", "_")[:255],
                {"tool_id": tool_id, "summary": payload.get("summary"), "error": payload.get("error")},
                task_id=task_id,
                severity=Severity.ERROR,
                idempotency_key=f"hermes-tool-failed:{session_id}:{tool_id}",
            )
            self._record_failure(
                f"tool:{name}",
                {
                    "tool": name,
                    "error": payload.get("error"),
                    "summary": payload.get("summary"),
                    "result_error": result.get("error") if isinstance(result, dict) else None,
                },
                session_id=session_id,
                tool_failure=True,
            )
        else:
            # Persist the completion fact, not the untrusted/raw output. This
            # is the exact backing required before HAL may say "I checked".
            completion_key = tool_id or hashlib.sha256(
                json.dumps(
                    redact_data(
                        {
                            "name": name,
                            "summary": payload.get("summary"),
                            "session": session_id,
                            "task": task_id,
                        }
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            self._append_exact(
                "hermes.tool.completed",
                name.replace(" ", "_")[:255],
                {
                    "tool_id": tool_id or None,
                    "summary": redact_text(str(payload.get("summary") or "completed"))[:1000],
                    "result_payload_retained": bool(action),
                },
                task_id=task_id,
                idempotency_key=f"hermes-tool-completed:{session_id}:{completion_key}",
            )
            self.event_bus.publish_observation(
                EventRunInput(
                    source="hermes.gateway",
                    type="hermes.tool.completed",
                    subject=name.replace(" ", "_")[:255],
                    severity=Severity.INFO,
                    observed_at=datetime.now(UTC),
                    normalized_template=f"Hermes tool completed: {name}",
                    redacted_payload={"tool_id": tool_id, "summary": payload.get("summary")},
                    task_id=task_id,
                    retention_class=RetentionClass.SHORT,
                    sensitivity=Sensitivity.INTERNAL,
                )
            )

    def _apply_degradation(self, transitions: tuple[CapabilityTransition, ...], info: dict) -> None:
        for transition in transitions:
            self.degradation.on_transition(
                transition,
                active_profile=str(info.get("profile_name") or info.get("active_profile") or "hal-full"),
                fallback_model=str(info.get("model") or "") or None,
            )
            if transition.current in {
                CapabilityLifecycle.DEGRADED,
                CapabilityLifecycle.UNRELIABLE,
                CapabilityLifecycle.DENIED,
                CapabilityLifecycle.UNAVAILABLE,
                CapabilityLifecycle.DISCONNECTED,
                CapabilityLifecycle.FAILED,
                CapabilityLifecycle.STALE,
            }:
                try:
                    self.sketches.update_distinct(
                        "unique_failed_capabilities",
                        "host",
                        transition.capability_id,
                        datetime.now(UTC),
                    )
                except Exception as exc:
                    self._record_sketch_fault("unique_failed_capabilities", exc)

    def _observe_latency(self, metric: str, scope: str, milliseconds: float) -> None:
        if milliseconds < 0:
            return
        now = datetime.now(UTC)
        self.metrics.update(
            metric,
            scope,
            milliseconds,
            unit="milliseconds",
            exact=True,
            observed_at=now,
        )
        try:
            self.sketches.update_quantile(metric, scope, milliseconds, now)
        except Exception as exc:
            self._record_sketch_fault(metric, exc)

    def _observe_usage(
        self, raw: object, session_id: str, task_id: str | None
    ) -> None:
        if not isinstance(raw, dict):
            return
        now = datetime.now(UTC)
        scope = "hermes"
        numeric = {
            "conversation_tokens": raw.get("context_used"),
            "context_max_tokens": raw.get("context_max"),
            "context_truncations": raw.get("compressions"),
        }
        prior_compressions = self.metrics.get("context_truncations", scope)
        for metric, value in numeric.items():
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number < 0:
                continue
            self.metrics.update(
                metric,
                scope,
                number,
                unit="tokens" if metric != "context_truncations" else "count",
                exact=True,
                observed_at=now,
                metadata={"session_id": session_id, "task_id": task_id},
            )
        compression_value = numeric["context_truncations"]
        try:
            compression_count = int(float(compression_value))
        except (TypeError, ValueError):
            compression_count = -1
        prior_count = int(prior_compressions.value) if prior_compressions else 0
        if compression_count > prior_count:
            self._append_exact(
                "context.compaction.observed",
                session_id or "session",
                {
                    "previous_count": prior_count,
                    "current_count": compression_count,
                    "material_context_loss": False,
                },
                task_id=task_id,
                severity=Severity.NOTICE,
                idempotency_key=f"context-compaction:{session_id}:{compression_count}",
            )
        used = raw.get("context_used")
        maximum = raw.get("context_max")
        try:
            utilization = float(used) / float(maximum) if float(maximum) > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            utilization = None
        if utilization is not None:
            utilization = min(1.0, max(0.0, utilization))
            self.metrics.update(
                "context_utilization",
                scope,
                utilization,
                unit="ratio",
                exact=True,
                observed_at=now,
            )
            try:
                self.sketches.update_quantile(
                    "context_utilization", scope, utilization, now
                )
            except Exception as exc:
                self._record_sketch_fault("context_utilization", exc)

    def _record_failure(
        self,
        namespace: str,
        details: dict,
        *,
        session_id: str,
        tool_failure: bool = False,
    ) -> None:
        canonical = json.dumps(
            redact_data({"namespace": namespace, **details}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        fingerprint = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        now = datetime.now(UTC)
        operations = (
            (
                "distinct",
                "unique_error_fingerprints",
                lambda: self.sketches.update_distinct(
                    "unique_error_fingerprints", "host", fingerprint, now
                ),
            ),
            (
                "frequency",
                "repeating_error_fingerprints",
                lambda: self.sketches.update_frequency(
                    "repeating_error_fingerprints", "host", fingerprint, now
                ),
            ),
            (
                "theta",
                "error_novelty",
                lambda: self.sketches.update_theta(
                    "error_novelty", "host", fingerprint, now
                ),
            ),
        )
        for _kind, metric, operation in operations:
            try:
                operation()
            except Exception as exc:
                self._record_sketch_fault(metric, exc)
        if tool_failure:
            for metric, operation in (
                (
                    "frequent_tool_failures",
                    lambda: self.sketches.update_frequency(
                        "frequent_tool_failures",
                        session_id or "session",
                        fingerprint,
                        now,
                    ),
                ),
                (
                    "tool_failure_novelty",
                    lambda: self.sketches.update_theta(
                        "tool_failure_novelty",
                        session_id or "session",
                        fingerprint,
                        now,
                    ),
                ),
            ):
                try:
                    operation()
                except Exception as exc:
                    self._record_sketch_fault(metric, exc)

    def _record_sketch_fault(self, metric: str, error: Exception) -> None:
        key = f"{metric}:{type(error).__name__}:{str(error)[:256]}"
        if key in self._sketch_faults:
            self._sketch_faults.move_to_end(key)
            return
        self._sketch_faults[key] = None
        if len(self._sketch_faults) > 128:
            self._sketch_faults.popitem(last=False)
        try:
            self._append_exact(
                "interoception.metric.unavailable",
                metric,
                {"metric": metric, "error": str(error)[:1000]},
                task_id=None,
                severity=Severity.WARNING,
                idempotency_key="sketch-fault:" + hashlib.sha256(key.encode()).hexdigest(),
            )
        except Exception:
            # The approximate plane must never block an exact Hermes event.
            return

    def _remember(self, mapping: OrderedDict, key: str, value) -> None:
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > self._mapping_capacity:
            old_key, _old_value = mapping.popitem(last=False)
            self._first_delta_seen.discard(str(old_key))

    def _append_exact(
        self,
        event_type: str,
        subject: str,
        payload: dict,
        *,
        task_id: str | None,
        idempotency_key: str,
        severity: Severity = Severity.INFO,
        retention_class: RetentionClass = RetentionClass.FOREVER,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
    ) -> StoredEvent:
        bounded_payload, _digest, _size, _truncated = bounded_redacted_record(
            payload, maximum_bytes=128 * 1024
        )
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hermes.gateway",
            event_type=event_type,
            subject=canonical_subject(subject, fallback="hermes"),
            severity=severity,
            retention_class=retention_class,
            sensitivity=sensitivity,
            origin=EventOrigin.OBSERVATION,
            payload=bounded_payload,
            task_id=task_id,
            idempotency_key=idempotency_key,
        )
        return self.event_bus.publish_exact(event).result(timeout=10)

    def _is_currently_degraded(self) -> bool:
        return self.degradation.status().state.value in {"DEGRADED", "RECOVERING"}

    def _record_degraded_reference(self, reference: str, reason: str) -> None:
        if not self._is_currently_degraded():
            return
        try:
            self.degradation.record_conclusion(reference, reason)
        except RuntimeError:
            # A concurrent exact recovery transition won the race; there is no
            # degraded episode in which this conclusion can be registered.
            return

    @staticmethod
    def _is_consequential(name: str) -> bool:
        lowered = name.lower()
        return any(marker in lowered for marker in _CONSEQUENTIAL)

    @staticmethod
    def _capability_for_tool(name: str) -> str | None:
        lowered = name.lower()
        return next(
            (capability for marker, capability in _CAPABILITY_FOR_TOOL.items() if marker in lowered),
            None,
        )

    def _current_or_unknown(self, capability: str) -> CapabilityLifecycle:
        try:
            return self.registry.current(capability).state
        except KeyError:
            return CapabilityLifecycle.UNKNOWN
