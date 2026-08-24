"""Transactional exact capability and active-task projections."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hal9000.sentience.capabilities.task_impact import ImpactLevel, classify_task_impact
from hal9000.sentience.event_envelope import EventEnvelope, utc_iso
from hal9000.sentience.events.redact import redact_data, redact_text
from hal9000.sentience.models import (
    CapabilityLifecycle,
    EventOrigin,
    RetentionClass,
    Sensitivity,
    Severity,
)
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    capability_id: str
    display_name: str
    category: str
    nominal_requirement: str
    weight: float
    material_class: str
    configured: bool = True


@dataclass(frozen=True, slots=True)
class CapabilityState:
    capability_id: str
    state: CapabilityLifecycle
    health: float | None
    permission_scope: str
    trust_state: str
    confidence: float
    observed_at: str
    freshness_deadline: str | None
    evidence_event_id: str
    replacement_capability: str | None
    task_impact: ImpactLevel


@dataclass(frozen=True, slots=True)
class CapabilityTransition:
    transition_id: str
    capability_id: str
    previous: CapabilityLifecycle
    current: CapabilityLifecycle
    expected: bool
    material: bool
    task_impact: ImpactLevel
    event_id: str


DEFAULT_CAPABILITIES = (
    CapabilityDefinition("primary_reasoning", "Primary reasoning model", "cognition", "required", 2.0, "cognitive"),
    CapabilityDefinition("persistent_memory", "Persistent machine memory", "cognition", "required", 1.5, "cognitive"),
    CapabilityDefinition("memory_retrieval", "Memory retrieval", "cognition", "required", 1.25, "cognitive"),
    CapabilityDefinition("session_context", "Required session context", "cognition", "required", 1.5, "cognitive"),
    CapabilityDefinition("codex", "Coding specialist", "specialist", "optional", 1.4, "cognitive"),
    CapabilityDefinition("terminal", "Terminal", "agency", "optional", 1.0, "operational"),
    CapabilityDefinition("filesystem_read", "Filesystem read", "agency", "optional", 1.0, "operational"),
    CapabilityDefinition("filesystem_write", "Filesystem write", "agency", "optional", 1.0, "operational"),
    CapabilityDefinition("browser", "Browser", "agency", "optional", 0.8, "operational"),
    CapabilityDefinition("network", "Network", "agency", "optional", 0.8, "operational"),
    CapabilityDefinition("mcp_runtime", "MCP runtime", "agency", "optional", 1.0, "operational"),
    CapabilityDefinition("approval_channel", "Approval channel", "safety", "required", 1.5, "operational"),
    CapabilityDefinition("verification", "Verification path", "safety", "required", 1.5, "operational"),
    CapabilityDefinition("microphone", "Microphone", "sensor", "optional", 0.5, "peripheral"),
    CapabilityDefinition("display", "Display", "sensor", "required", 0.8, "peripheral"),
    CapabilityDefinition("speech", "Speech output", "output", "optional", 0.3, "cosmetic"),
    CapabilityDefinition("vision", "Visual observation", "sensor", "disabled", 0.7, "peripheral", False),
)

_READY_STATES = {CapabilityLifecycle.READY}
_LOSS_STATES = {
    CapabilityLifecycle.DEGRADED,
    CapabilityLifecycle.UNRELIABLE,
    CapabilityLifecycle.DENIED,
    CapabilityLifecycle.UNAVAILABLE,
    CapabilityLifecycle.DISCONNECTED,
    CapabilityLifecycle.FAILED,
    CapabilityLifecycle.STALE,
}


class CapabilityRegistry:
    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id

    def install_defaults(self) -> None:
        for definition in DEFAULT_CAPABILITIES:
            self.define(definition)
        for parent, dependency in (
            ("codex", "terminal"),
            ("codex", "filesystem_read"),
            ("filesystem_write", "approval_channel"),
            ("verification", "filesystem_read"),
        ):
            self.add_dependency(parent, dependency)

    def define(self, definition: CapabilityDefinition) -> None:
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.capabilities",
            event_type="capability.defined",
            subject=definition.capability_id,
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.CONFIGURATION,
            payload={
                "display_name": definition.display_name,
                "category": definition.category,
                "nominal_requirement": definition.nominal_requirement,
                "weight": definition.weight,
                "material_class": definition.material_class,
                "configured": definition.configured,
            },
            idempotency_key=f"capability-definition:v1:{definition.capability_id}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO capability_definitions(capability_id,display_name,category,"
                "nominal_requirement,weight,material_class,configured) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(capability_id) DO UPDATE SET display_name=excluded.display_name,"
                "category=excluded.category,nominal_requirement=excluded.nominal_requirement,"
                "weight=excluded.weight,material_class=excluded.material_class,"
                "configured=excluded.configured",
                (
                    definition.capability_id,
                    definition.display_name,
                    definition.category,
                    definition.nominal_requirement,
                    definition.weight,
                    definition.material_class,
                    int(definition.configured),
                ),
            )

        self.database.append_exact_event(event, projection=project)

    def add_dependency(self, parent: str, required: str) -> None:
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.capabilities",
            event_type="capability.dependency.configured",
            subject=parent,
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.CONFIGURATION,
            payload={"required_capability": required, "minimum_state": "READY"},
            idempotency_key=f"capability-edge:v1:{parent}:{required}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT OR IGNORE INTO capability_edges(parent_capability,required_capability) "
                "VALUES(?,?)",
                (parent, required),
            )

        self.database.append_exact_event(event, projection=project)

    def create_task(
        self,
        title: str,
        *,
        risk_level: str = "ordinary",
        requirements: dict[str, tuple[CapabilityLifecycle, bool]] | None = None,
        task_id: str | None = None,
    ) -> str:
        identity = task_id or str(uuid.uuid4())
        clean_title = redact_text(title)[:1000]
        requirement_payload = {
            capability: {"minimum_state": state.value, "unsafe_if_lost": unsafe}
            for capability, (state, unsafe) in (requirements or {}).items()
        }
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.tasks",
            event_type="task.created",
            subject=identity,
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={"title": clean_title, "risk_level": risk_level, "requirements": requirement_payload},
            task_id=identity,
            idempotency_key=f"task-create:{identity}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO tasks(task_id,title,state,created_at,updated_at,risk_level) "
                "VALUES(?,?,'active',?,?,?)",
                (identity, clean_title, event.occurred_at_utc, event.occurred_at_utc, risk_level),
            )
            for capability, (minimum, unsafe) in (requirements or {}).items():
                connection.execute(
                    "INSERT INTO task_capability_requirements(task_id,capability_id,minimum_state,"
                    "required,unsafe_if_lost) VALUES(?,?,?,1,?)",
                    (identity, capability, minimum.value, int(unsafe)),
                )

        self.database.append_exact_event(event, projection=project)
        return identity

    def transition(
        self,
        capability_id: str,
        state: CapabilityLifecycle,
        *,
        reason: str,
        evidence: dict[str, Any],
        task_id: str | None = None,
        expected: bool = False,
        permission_scope: str = "available",
        trust_state: str = "verified",
        confidence: float = 1.0,
        health: float | None = None,
        freshness_deadline: str | None = None,
        replacement_capability: str | None = None,
        active_profile: str | None = None,
    ) -> CapabilityTransition:
        target = CapabilityLifecycle(state)
        clean_reason = redact_text(reason)[:2000]
        clean_evidence = redact_data(evidence)
        with self.database.read_connection() as connection:
            definition = connection.execute(
                "SELECT * FROM capability_definitions WHERE capability_id=?", (capability_id,)
            ).fetchone()
            prior = connection.execute(
                "SELECT lifecycle_state FROM capability_current WHERE capability_id=?", (capability_id,)
            ).fetchone()
            requirement = (
                connection.execute(
                    "SELECT required,unsafe_if_lost FROM task_capability_requirements "
                    "WHERE task_id=? AND capability_id=?",
                    (task_id, capability_id),
                ).fetchone()
                if task_id
                else None
            )
        if definition is None:
            raise KeyError(f"undefined capability {capability_id}")
        previous = CapabilityLifecycle(prior[0]) if prior else CapabilityLifecycle.UNKNOWN
        impact = ImpactLevel.NONE
        if target in _LOSS_STATES:
            impact = classify_task_impact(
                nominal_requirement=str(definition["nominal_requirement"]),
                material_class=str(definition["material_class"]),
                task_requires=bool(requirement and requirement["required"]),
                unsafe_if_lost=bool(requirement and requirement["unsafe_if_lost"]),
            )
        material = not expected and target in _LOSS_STATES and impact in {
            ImpactLevel.COGNITIVE,
            ImpactLevel.CRITICAL,
        }
        transition_id = str(uuid.uuid4())
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.capabilities",
            event_type="capability.transitioned",
            subject=capability_id,
            severity=Severity.ERROR if impact is ImpactLevel.CRITICAL else Severity.WARNING if material else Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={
                "from": previous.value,
                "to": target.value,
                "reason": clean_reason,
                "expected": expected,
                "material": material,
                "task_impact": impact.value,
                "evidence": clean_evidence,
                "replacement_capability": replacement_capability,
                "active_profile": active_profile,
            },
            task_id=task_id,
            idempotency_key=f"capability-transition:{transition_id}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO capability_transitions(transition_id,capability_id,from_state,to_state,"
                "expected,material,occurred_at,event_id,reason,task_impact) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    transition_id,
                    capability_id,
                    previous.value,
                    target.value,
                    int(expected),
                    int(material),
                    event.occurred_at_utc,
                    event.event_id,
                    clean_reason,
                    impact.value,
                ),
            )
            connection.execute(
                "INSERT INTO capability_current(capability_id,lifecycle_state,health,permission_scope,"
                "trust_state,confidence,observed_at,freshness_deadline,evidence_event_id,"
                "replacement_capability,active_profile,current_task_impact) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(capability_id) DO UPDATE SET lifecycle_state=excluded.lifecycle_state,"
                "health=excluded.health,permission_scope=excluded.permission_scope,"
                "trust_state=excluded.trust_state,confidence=excluded.confidence,"
                "observed_at=excluded.observed_at,freshness_deadline=excluded.freshness_deadline,"
                "evidence_event_id=excluded.evidence_event_id,replacement_capability=excluded.replacement_capability,"
                "active_profile=excluded.active_profile,current_task_impact=excluded.current_task_impact",
                (
                    capability_id,
                    target.value,
                    health,
                    permission_scope,
                    trust_state,
                    confidence,
                    event.occurred_at_utc,
                    freshness_deadline,
                    event.event_id,
                    replacement_capability,
                    active_profile,
                    impact.value,
                ),
            )

        self.database.append_exact_event(event, projection=project)
        return CapabilityTransition(
            transition_id, capability_id, previous, target, expected, material, impact, event.event_id
        )

    def current(self, capability_id: str) -> CapabilityState:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM capability_current WHERE capability_id=?", (capability_id,)
            ).fetchone()
        if row is None:
            raise KeyError(capability_id)
        return CapabilityState(
            capability_id,
            CapabilityLifecycle(row["lifecycle_state"]),
            float(row["health"]) if row["health"] is not None else None,
            str(row["permission_scope"]),
            str(row["trust_state"]),
            float(row["confidence"]),
            str(row["observed_at"]),
            str(row["freshness_deadline"]) if row["freshness_deadline"] else None,
            str(row["evidence_event_id"] or ""),
            str(row["replacement_capability"]) if row["replacement_capability"] else None,
            ImpactLevel(row["current_task_impact"]),
        )

    def list_current(self) -> tuple[CapabilityState, ...]:
        with self.database.read_connection() as connection:
            identifiers = [
                str(row[0])
                for row in connection.execute(
                    "SELECT capability_id FROM capability_current ORDER BY capability_id"
                ).fetchall()
            ]
        return tuple(self.current(identifier) for identifier in identifiers)

    def unsatisfied_requirements(self, task_id: str) -> tuple[str, ...]:
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT r.capability_id,c.lifecycle_state FROM task_capability_requirements r "
                "LEFT JOIN capability_current c ON c.capability_id=r.capability_id "
                "WHERE r.task_id=? AND r.required=1 ORDER BY r.capability_id",
                (task_id,),
            ).fetchall()
        return tuple(
            str(row["capability_id"])
            for row in rows
            if row["lifecycle_state"] is None
            or CapabilityLifecycle(row["lifecycle_state"]) not in _READY_STATES
        )

    def set_task_requirement(
        self,
        task_id: str,
        capability_id: str,
        *,
        minimum_state: CapabilityLifecycle = CapabilityLifecycle.READY,
        unsafe_if_lost: bool = False,
        reason: str = "observed task dependency",
    ) -> None:
        clean_reason = redact_text(reason)[:2000]
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.tasks",
            event_type="task.capability.required",
            subject=capability_id,
            severity=Severity.NOTICE,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={
                "task_id": task_id,
                "minimum_state": minimum_state.value,
                "unsafe_if_lost": unsafe_if_lost,
                "reason": clean_reason,
            },
            task_id=task_id,
            idempotency_key=f"task-requirement:{task_id}:{capability_id}:{int(unsafe_if_lost)}",
        )

        def project(connection, _sequence: int) -> None:
            task = connection.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            definition = connection.execute(
                "SELECT 1 FROM capability_definitions WHERE capability_id=?", (capability_id,)
            ).fetchone()
            if task is None or definition is None:
                raise KeyError(task_id if task is None else capability_id)
            connection.execute(
                "INSERT INTO task_capability_requirements(task_id,capability_id,minimum_state,"
                "required,unsafe_if_lost,reason) VALUES(?,?,?,1,?,?) "
                "ON CONFLICT(task_id,capability_id) DO UPDATE SET "
                "minimum_state=excluded.minimum_state,required=1,"
                "unsafe_if_lost=max(task_capability_requirements.unsafe_if_lost,excluded.unsafe_if_lost),"
                "reason=excluded.reason",
                (task_id, capability_id, minimum_state.value, int(unsafe_if_lost), clean_reason),
            )

        self.database.append_exact_event(event, projection=project)
