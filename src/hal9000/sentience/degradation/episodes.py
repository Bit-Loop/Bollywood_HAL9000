"""Typed persisted degradation episode views."""

from __future__ import annotations

import json
from dataclasses import dataclass
from sqlite3 import Row

from hal9000.sentience.models import DegradationSeverity, DegradationState


@dataclass(frozen=True, slots=True)
class DegradationStatus:
    state: DegradationState
    episode_id: str | None = None
    severity: DegradationSeverity = DegradationSeverity.PERIPHERAL
    nominal_profile: str = "hal-full"
    active_profile: str = "hal-full"
    lost_capabilities: tuple[str, ...] = ()
    affected_tasks: tuple[str, ...] = ()
    fallback_model: str | None = None
    phrase_emitted: bool = False
    recovery_phrase_emitted: bool = False
    recovery_started_at: str | None = None
    recovered_at: str | None = None
    conclusions_requiring_revalidation: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: Row) -> "DegradationStatus":
        return cls(
            state=DegradationState(str(row["state"])),
            episode_id=str(row["episode_id"]),
            severity=DegradationSeverity(str(row["severity"])),
            nominal_profile=str(row["nominal_profile"]),
            active_profile=str(row["active_profile"]),
            lost_capabilities=tuple(json.loads(str(row["lost_capabilities_json"]))),
            affected_tasks=tuple(json.loads(str(row["affected_tasks_json"]))),
            fallback_model=str(row["fallback_model"]) if row["fallback_model"] else None,
            phrase_emitted=bool(row["phrase_emitted"]),
            recovery_phrase_emitted=bool(row["recovery_phrase_emitted"]),
            recovery_started_at=(str(row["recovery_started_at"]) if row["recovery_started_at"] else None),
            recovered_at=str(row["recovered_at"]) if row["recovered_at"] else None,
            conclusions_requiring_revalidation=tuple(
                json.loads(str(row["conclusions_requiring_revalidation_json"]))
            ),
        )
