"""Bounded LRU coalescer for repeated equivalent observations."""

from __future__ import annotations

import json
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime

from hal9000.config import SentienceIngestionSettings
from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.events.fingerprint import stable_fingerprint
from hal9000.sentience.events.normalize import normalize_observation
from hal9000.sentience.events.redact import redact_data, redact_text
from hal9000.sentience.models import RetentionClass, Sensitivity, Severity
from hal9000.sentience.sketches.sampling import (
    BoundedRepresentativeSampler,
    RepresentativeSample,
)
from hal9000.sentience.storage.database import SentienceDatabase

_SEVERITY_VALUE = {
    Severity.DEBUG: 0,
    Severity.INFO: 1,
    Severity.NOTICE: 2,
    Severity.WARNING: 3,
    Severity.ERROR: 4,
    Severity.CRITICAL: 5,
}


@dataclass(frozen=True, slots=True)
class EventRunInput:
    source: str
    type: str
    subject: str
    observed_at: datetime
    severity: Severity
    task_id: str | None
    normalized_template: str
    redacted_payload: dict
    retention_class: RetentionClass
    sensitivity: Sensitivity


@dataclass(slots=True)
class _OpenRun:
    run_id: str
    fingerprint: str
    item: EventRunInput
    epoch: str
    first_seen: str
    last_seen: str
    pending_count: int
    severity_max: Severity
    sampler: BoundedRepresentativeSampler


class EventRunCoalescer:
    def __init__(
        self,
        database: SentienceDatabase,
        settings: SentienceIngestionSettings,
        *,
        epoch_seconds: int = 300,
    ) -> None:
        if epoch_seconds <= 0:
            raise ValueError("coalescing epoch must be positive")
        self.database = database
        self.settings = settings
        self.epoch_seconds = epoch_seconds
        self._runs: OrderedDict[tuple[str, ...], _OpenRun] = OrderedDict()
        self._closed = False

    @property
    def open_run_count(self) -> int:
        return len(self._runs)

    def add(self, item: EventRunInput) -> None:
        if self._closed:
            raise RuntimeError("event-run coalescer is closed")
        if item.observed_at.tzinfo is None:
            raise ValueError("event-run timestamps must be timezone-aware")
        safe_payload = redact_data(item.redacted_payload)
        normalized = normalize_observation(item.source, item.type, item.subject, safe_payload)
        fingerprint = stable_fingerprint(normalized)
        seconds = int(item.observed_at.astimezone(UTC).timestamp())
        epoch_start = seconds - seconds % self.epoch_seconds
        epoch = datetime.fromtimestamp(epoch_start, UTC).isoformat().replace("+00:00", "Z")
        key = (
            item.source,
            item.type,
            item.subject,
            fingerprint,
            item.task_id or "",
            epoch,
        )
        stamp = utc_iso(item.observed_at)
        run = self._runs.get(key)
        if run is None:
            if len(self._runs) >= self.settings.max_open_runs:
                _old_key, old = self._runs.popitem(last=False)
                self._flush_one(old)
            run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "hal-event-run:" + "|".join(key)))
            sampler = BoundedRepresentativeSampler(
                self.settings.sample_count_per_run, run_id
            )
            with self.database.read_connection() as connection:
                committed = connection.execute(
                    "SELECT run_id,first_seen,last_seen,count,severity_max FROM event_runs WHERE "
                    "source=? AND type=? AND subject=? AND fingerprint=? AND task_id IS ? "
                    "AND coalescing_epoch=?",
                    (
                        item.source,
                        item.type,
                        item.subject,
                        fingerprint,
                        item.task_id,
                        epoch,
                    ),
                ).fetchone()
                sample_rows = (
                    connection.execute(
                        "SELECT sample_kind,ordinal,redacted_text,observed_at,severity FROM "
                        "event_run_samples WHERE run_id=? AND pinned=0 ORDER BY sample_kind,ordinal",
                        (str(committed["run_id"]),),
                    ).fetchall()
                    if committed is not None
                    else ()
                )
            if committed is not None:
                run_id = str(committed["run_id"])
                sampler.namespace = run_id
                sampler.restore(
                    int(committed["count"]),
                    tuple(
                        RepresentativeSample(
                            str(row["sample_kind"]),
                            int(row["ordinal"]),
                            str(row["observed_at"]),
                            Severity(str(row["severity"])),
                            str(row["redacted_text"]),
                        )
                        for row in sample_rows
                    ),
                )
            run = _OpenRun(
                run_id,
                fingerprint,
                item,
                epoch,
                str(committed["first_seen"]) if committed is not None else stamp,
                str(committed["last_seen"]) if committed is not None else stamp,
                0,
                (
                    max(
                        (Severity(str(committed["severity_max"])), Severity(item.severity)),
                        key=lambda value: _SEVERITY_VALUE[value],
                    )
                    if committed is not None
                    else Severity(item.severity)
                ),
                sampler,
            )
            self._runs[key] = run
        else:
            self._runs.move_to_end(key)
        run.pending_count += 1
        run.last_seen = stamp
        if _SEVERITY_VALUE[Severity(item.severity)] > _SEVERITY_VALUE[run.severity_max]:
            run.severity_max = Severity(item.severity)
        sample_value = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"))[:16_384]
        run.sampler.update(sample_value, stamp, Severity(item.severity))

    def flush(self) -> int:
        flushed = 0
        for run in list(self._runs.values()):
            if run.pending_count:
                self._flush_one(run)
                flushed += 1
        return flushed

    def _flush_one(self, run: _OpenRun) -> None:
        if run.pending_count <= 0:
            return
        item = run.item
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO event_runs(run_id,fingerprint,source,type,subject,first_seen,last_seen,"
                "count,severity_max,task_id,coalescing_epoch,normalized_template,retention_class,"
                "sensitivity) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                # SQLite considers NULL task IDs distinct inside a composite
                # UNIQUE constraint. The UUID is deterministic from the full
                # bounded key (using an empty task component), so it is the
                # authoritative crash/restart conflict target.
                "ON CONFLICT(run_id) DO UPDATE SET "
                "last_seen=MAX(event_runs.last_seen,excluded.last_seen),"
                "count=event_runs.count+excluded.count,"
                "severity_max=CASE "
                "WHEN excluded.severity_max='critical' THEN 'critical' "
                "WHEN excluded.severity_max='error' AND event_runs.severity_max!='critical' THEN 'error' "
                "WHEN excluded.severity_max='warning' AND event_runs.severity_max NOT IN ('critical','error') THEN 'warning' "
                "ELSE event_runs.severity_max END,"
                "normalized_template=excluded.normalized_template",
                (
                    run.run_id,
                    run.fingerprint,
                    item.source,
                    item.type,
                    item.subject,
                    run.first_seen,
                    run.last_seen,
                    run.pending_count,
                    run.severity_max.value,
                    item.task_id,
                    run.epoch,
                    redact_text(item.normalized_template)[:4096],
                    item.retention_class.value,
                    item.sensitivity.value,
                ),
            )
            actual = connection.execute(
                "SELECT run_id FROM event_runs WHERE source=? AND type=? AND subject=? "
                "AND fingerprint=? AND task_id IS ? AND coalescing_epoch=?",
                (item.source, item.type, item.subject, run.fingerprint, item.task_id, run.epoch),
            ).fetchone()[0]
            # A periodic flush must not turn the moving "latest" exemplar into
            # one new row per flush. Rebuild the unpinned sample slots inside
            # the same transaction; explicitly pinned evidence is untouched.
            connection.execute(
                "DELETE FROM event_run_samples WHERE run_id=? AND pinned=0", (actual,)
            )
            for sample in run.sampler.samples():
                sample_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"hal-event-sample:{actual}:{sample.kind}:{sample.ordinal}",
                    )
                )
                connection.execute(
                    "INSERT INTO event_run_samples(sample_id,run_id,sample_kind,redacted_text,"
                    "observed_at,severity,ordinal) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(run_id,sample_kind,ordinal) DO UPDATE SET "
                    "redacted_text=excluded.redacted_text,observed_at=excluded.observed_at,"
                    "severity=excluded.severity",
                    (
                        sample_id,
                        actual,
                        sample.kind,
                        str(sample.value),
                        sample.observed_at,
                        sample.severity.value,
                        sample.ordinal,
                    ),
                )
        run.pending_count = 0

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._runs.clear()
        self._closed = True
