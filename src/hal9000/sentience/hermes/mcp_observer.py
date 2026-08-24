"""Project structured Hermes tool inventory into exact capability state."""

from __future__ import annotations

import hashlib
import re

from hal9000.sentience.capabilities.registry import (
    CapabilityDefinition,
    CapabilityRegistry,
    CapabilityTransition,
)
from hal9000.sentience.models import CapabilityLifecycle

_TOOL_MARKERS = {
    "terminal": ("terminal", "shell", "exec", "command"),
    "filesystem_read": ("read_file", "search_files", "list_files"),
    "filesystem_write": ("write_file", "patch", "apply_patch"),
    "browser": ("browser",),
    "network": ("web_search", "web_extract", "browser"),
    "codex": ("codex", "delegate_task"),
}


class McpCapabilityObserver:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        blocked_capabilities: frozenset[str] = frozenset(),
    ) -> None:
        self.registry = registry
        self.blocked_capabilities = blocked_capabilities
        self.seen_snapshot = False

    def observe(
        self,
        tools: dict,
        *,
        task_id: str | None,
        lazy: bool = False,
    ) -> tuple[CapabilityTransition, ...]:
        if lazy:
            return ()
        available_by_capability = {capability: False for capability in _TOOL_MARKERS}
        any_name = False
        verification_available = False
        evidence_names: list[str] = []
        evidence_seen: set[str] = set()
        for name in self._tool_names(tools):
            any_name = True
            for capability, markers in _TOOL_MARKERS.items():
                if not available_by_capability[capability] and any(
                    marker in name for marker in markers
                ):
                    available_by_capability[capability] = True
            verification_available = verification_available or (
                "terminal" in name or "read_file" in name
            )
            if len(evidence_names) < 256 and name not in evidence_seen:
                evidence_names.append(name)
                evidence_seen.add(name)
        transitions: list[CapabilityTransition] = []
        for capability, markers in _TOOL_MARKERS.items():
            available = available_by_capability[capability]
            desired = (
                CapabilityLifecycle.DENIED
                if capability in self.blocked_capabilities
                else CapabilityLifecycle.READY
                if available
                else CapabilityLifecycle.UNAVAILABLE
            )
            current = self._current_or_unknown(capability)
            if current == desired:
                continue
            # First complete inventory establishes the intended starting
            # profile; later disappearance is an unexpected exact loss.
            transitions.append(
                self.registry.transition(
                    capability,
                    desired,
                    reason=(
                        "machine-self integrity policy denies consequential authority"
                        if capability in self.blocked_capabilities
                        else "Hermes structured tool inventory reports availability"
                        if available
                        else "Hermes structured tool inventory reports absence"
                    ),
                    evidence={"event": "session.info", "matching_tools": sorted(evidence_names)},
                    task_id=task_id,
                    expected=not self.seen_snapshot and not self._task_requires(
                        task_id, capability
                    ),
                    permission_scope=(
                        "none"
                        if capability in self.blocked_capabilities
                        else "available"
                        if available
                        else "none"
                    ),
                )
            )
        for capability, available in (
            ("mcp_runtime", any_name),
            ("approval_channel", True),
            ("verification", verification_available),
        ):
            desired = (
                CapabilityLifecycle.DENIED
                if capability in self.blocked_capabilities
                else CapabilityLifecycle.READY
                if available
                else CapabilityLifecycle.UNAVAILABLE
            )
            if self._current_or_unknown(capability) == desired:
                continue
            transitions.append(
                self.registry.transition(
                    capability,
                    desired,
                    reason="Hermes Gateway structured capability inventory",
                    evidence={"event": "session.info", "available": available},
                    task_id=task_id,
                    expected=not self.seen_snapshot and not self._task_requires(
                        task_id, capability
                    ),
                )
            )
        self.seen_snapshot = True
        return tuple(transitions)

    @staticmethod
    def _tool_names(tools: dict):
        """Yield normalized inventory labels without building an unbounded set."""

        for group, entries in tools.items():
            yield str(group).lower()
            if isinstance(entries, dict):
                for name in entries:
                    yield str(name).lower()
            elif isinstance(entries, (list, tuple, set)):
                for name in entries:
                    yield str(name).lower()
            elif isinstance(entries, str):
                yield entries.lower()

    def observe_servers(
        self, servers: list[dict], *, task_id: str | None
    ) -> tuple[CapabilityTransition, ...]:
        """Persist each structured MCP server health state without log parsing."""

        reported: set[str] = set()
        transitions: list[CapabilityTransition] = []
        for server in servers[:256]:
            name = str(server.get("name") or server.get("server") or "").strip()
            if not name:
                continue
            identifier = self._server_capability(name)
            reported.add(identifier)
            self.registry.define(
                CapabilityDefinition(
                    identifier,
                    f"MCP server {name}"[:255],
                    "mcp",
                    "optional",
                    0.3,
                    "peripheral",
                )
            )
            status = str(server.get("status") or server.get("state") or "unknown").lower()
            enabled = bool(server.get("enabled", True))
            ready = enabled and (
                bool(server.get("connected"))
                or bool(server.get("ok"))
                or status in {"ready", "connected", "running", "healthy", "ok"}
            )
            desired = (
                CapabilityLifecycle.READY
                if ready
                else CapabilityLifecycle.DENIED
                if not enabled
                else CapabilityLifecycle.UNAVAILABLE
            )
            if self._current_or_unknown(identifier) != desired:
                transitions.append(
                    self.registry.transition(
                        identifier,
                        desired,
                        reason="Hermes session.info reported MCP server health",
                        evidence={
                            "event": "session.info",
                            "server": name,
                            "status": status,
                            "enabled": enabled,
                        },
                        task_id=task_id,
                        expected=not self.seen_snapshot,
                    )
                )
        with self.registry.database.read_connection() as connection:
            known = {
                str(row[0])
                for row in connection.execute(
                    "SELECT capability_id FROM capability_definitions WHERE category='mcp' "
                    "ORDER BY capability_id LIMIT 256"
                ).fetchall()
            }
        for missing in sorted(known - reported):
            current = self._current_or_unknown(missing)
            if current in {CapabilityLifecycle.UNKNOWN, CapabilityLifecycle.UNAVAILABLE}:
                continue
            transitions.append(
                self.registry.transition(
                    missing,
                    CapabilityLifecycle.UNAVAILABLE,
                    reason="MCP server disappeared from the complete Hermes health snapshot",
                    evidence={"event": "session.info", "reported": False},
                    task_id=task_id,
                    expected=not self.seen_snapshot,
                )
            )
        return tuple(transitions)

    @staticmethod
    def _server_capability(name: str) -> str:
        slug = re.sub(r"[^a-z0-9_.-]+", "_", name.lower()).strip("_.-")[:96]
        digest = hashlib.sha256(name.encode()).hexdigest()[:12]
        return f"mcp.{slug or 'server'}.{digest}"

    def _current_or_unknown(self, capability: str) -> CapabilityLifecycle:
        try:
            return self.registry.current(capability).state
        except KeyError:
            return CapabilityLifecycle.UNKNOWN

    def _task_requires(self, task_id: str | None, capability: str) -> bool:
        if not task_id:
            return False
        with self.registry.database.read_connection() as connection:
            row = connection.execute(
                "SELECT required FROM task_capability_requirements WHERE task_id=? "
                "AND capability_id=?",
                (task_id, capability),
            ).fetchone()
        return bool(row and row["required"])
