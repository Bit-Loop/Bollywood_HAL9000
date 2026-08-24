"""Bounded current operational metrics and low-overhead host sampling."""

from __future__ import annotations

import json
import math
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.events.redact import redact_data
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class OperationalMetric:
    metric_name: str
    scope: str
    value: float
    unit: str
    observed_at: str
    monotonic_ns: int
    boot_id: str
    exact: bool
    source_event_id: str | None
    metadata: dict


@dataclass(frozen=True, slots=True)
class ResourceSample:
    observed_at: datetime
    monotonic_ns: int
    values: dict[str, float]
    metadata: dict[str, dict]


class OperationalMetricStore:
    """One fixed row per metric/scope; historical distributions live in KLL."""

    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id

    def update(
        self,
        metric_name: str,
        scope: str,
        value: float,
        *,
        unit: str,
        exact: bool,
        observed_at: datetime | None = None,
        monotonic_ns: int | None = None,
        source_event_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("operational metrics must be finite")
        if not metric_name or len(metric_name) > 255 or not scope or len(scope) > 255:
            raise ValueError("operational metric name and scope are required and bounded")
        stamp = observed_at or datetime.now(UTC)
        mono = time.monotonic_ns() if monotonic_ns is None else int(monotonic_ns)
        safe_metadata = redact_data(metadata or {})
        encoded = json.dumps(safe_metadata, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > 16_384:
            raise ValueError("operational metric metadata exceeds its bounded size")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO operational_metrics_current(metric_name,scope,value,unit,"
                "observed_at,monotonic_ns,boot_id,exact,source_event_id,metadata_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(metric_name,scope) DO UPDATE SET "
                "value=excluded.value,unit=excluded.unit,observed_at=excluded.observed_at,"
                "monotonic_ns=excluded.monotonic_ns,boot_id=excluded.boot_id,"
                "exact=excluded.exact,source_event_id=excluded.source_event_id,"
                "metadata_json=excluded.metadata_json",
                (
                    metric_name,
                    scope,
                    numeric,
                    unit[:64],
                    utc_iso(stamp),
                    mono,
                    self.boot_id,
                    int(exact),
                    source_event_id,
                    encoded,
                ),
            )

    def get(self, metric_name: str, scope: str = "host") -> OperationalMetric | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM operational_metrics_current WHERE metric_name=? AND scope=?",
                (metric_name, scope),
            ).fetchone()
        if row is None:
            return None
        return OperationalMetric(
            str(row["metric_name"]),
            str(row["scope"]),
            float(row["value"]),
            str(row["unit"]),
            str(row["observed_at"]),
            int(row["monotonic_ns"]),
            str(row["boot_id"]),
            bool(row["exact"]),
            str(row["source_event_id"]) if row["source_event_id"] else None,
            json.loads(str(row["metadata_json"])),
        )


class HostResourceSampler:
    """Read bounded Linux counters; never launch a GPU probe in the hot path."""

    def __init__(self, filesystem_root: Path) -> None:
        self.filesystem_root = filesystem_root
        self._previous_cpu: tuple[int, int, int] | None = None

    def sample(self) -> ResourceSample:
        stamp = datetime.now(UTC)
        monotonic_ns = time.monotonic_ns()
        values: dict[str, float] = {}
        metadata: dict[str, dict] = {}
        cpu = self._cpu()
        if cpu is not None:
            busy, iowait = cpu
            values["cpu_utilization"] = busy
            values["io_wait"] = iowait
        memory = self._memory()
        if memory is not None:
            values.update(memory[0])
            metadata["memory_utilization"] = memory[1]
        disk = shutil.disk_usage(self.filesystem_root)
        values["disk_utilization"] = 1.0 - (disk.free / disk.total if disk.total else 0.0)
        metadata["disk_utilization"] = {
            "total_bytes": disk.total,
            "free_bytes": disk.free,
        }
        return ResourceSample(stamp, monotonic_ns, values, metadata)

    def _cpu(self) -> tuple[float, float] | None:
        try:
            fields = [int(item) for item in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        except (OSError, ValueError, IndexError):
            return None
        if len(fields) < 5:
            return None
        idle = fields[3] + fields[4]
        total = sum(fields)
        iowait = fields[4]
        previous = self._previous_cpu
        self._previous_cpu = (total, idle, iowait)
        if previous is None or total <= previous[0]:
            return None
        delta_total = total - previous[0]
        busy = 1.0 - (idle - previous[1]) / delta_total
        wait = (iowait - previous[2]) / delta_total
        return min(1.0, max(0.0, busy)), min(1.0, max(0.0, wait))

    @staticmethod
    def _memory() -> tuple[dict[str, float], dict] | None:
        try:
            pairs = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                name, value = line.split(":", 1)
                pairs[name] = int(value.strip().split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        total = pairs.get("MemTotal", 0)
        available = pairs.get("MemAvailable", 0)
        swap_total = pairs.get("SwapTotal", 0)
        swap_free = pairs.get("SwapFree", 0)
        if not total:
            return None
        return (
            {
                "memory_utilization": min(1.0, max(0.0, 1.0 - available / total)),
                "swap_utilization": (
                    min(1.0, max(0.0, 1.0 - swap_free / swap_total))
                    if swap_total
                    else 0.0
                ),
            },
            {
                "total_bytes": total,
                "available_bytes": available,
                "swap_total_bytes": swap_total,
                "swap_free_bytes": swap_free,
            },
        )
