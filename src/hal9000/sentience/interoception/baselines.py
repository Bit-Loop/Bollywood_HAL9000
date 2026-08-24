"""Bucket-friendly robust baseline learning that excludes severe incidents."""

from __future__ import annotations

import statistics
import json
import math
import uuid
from dataclasses import dataclass

from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class RobustBaseline:
    state: str
    sample_count: int
    median: float | None
    mad: float | None
    version: int


class BaselineLearner:
    def __init__(self, *, minimum_samples: int = 100, maximum_samples: int = 4096, version: int = 1) -> None:
        if minimum_samples < 2 or maximum_samples < minimum_samples:
            raise ValueError("invalid baseline sample bounds")
        self.minimum_samples = minimum_samples
        self.maximum_samples = maximum_samples
        self.version = version
        self._samples: list[float] = []
        self.excluded_incidents = 0

    def update(self, value: float, *, severe_incident: bool = False) -> None:
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError("baseline samples must be finite")
        if severe_incident:
            self.excluded_incidents += 1
            return
        if len(self._samples) < self.maximum_samples:
            self._samples.append(numeric_value)
        else:
            # Deterministic bounded rolling representative window.
            self._samples.pop(0)
            self._samples.append(numeric_value)

    def summary(self) -> RobustBaseline:
        if len(self._samples) < self.minimum_samples:
            return RobustBaseline("learning", len(self._samples), None, None, self.version)
        median = statistics.median(self._samples)
        mad = statistics.median(abs(value - median) for value in self._samples)
        return RobustBaseline("ready", len(self._samples), median, mad, self.version)

    def reset(self) -> None:
        self._samples.clear()
        self.version += 1


class PersistentBaselineStore:
    """Versioned, bounded robust baselines stored with their source range."""

    def __init__(
        self,
        database: SentienceDatabase,
        *,
        minimum_samples: int = 100,
        maximum_samples: int = 4096,
    ) -> None:
        if minimum_samples < 2 or maximum_samples < minimum_samples:
            raise ValueError("invalid persistent baseline sample bounds")
        self.database = database
        self.minimum_samples = minimum_samples
        self.maximum_samples = maximum_samples

    def update(
        self,
        metric_name: str,
        scope: str,
        value: float,
        *,
        observed_at: str | None = None,
        severe_incident_id: str | None = None,
        source_id: str | None = None,
    ) -> RobustBaseline:
        timestamp = observed_at or utc_iso()
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError("baseline samples must be finite")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM baseline_versions WHERE metric_name=? AND scope=? "
                "ORDER BY version DESC LIMIT 1",
                (metric_name, scope),
            ).fetchone()
            if row is None:
                version = 1
                baseline_id = str(uuid.uuid4())
                started_at = timestamp
                samples: list[float] = []
                excluded: list[str] = []
                source_ids: list[str] = []
            else:
                version = int(row["version"])
                baseline_id = str(row["baseline_id"])
                started_at = str(row["started_at"])
                summary = json.loads(str(row["summary_json"]) or "{}")
                source_ids = [
                    str(item) for item in summary.get("source_ids", [])
                ][-self.maximum_samples :]
                if not source_ids and summary.get("last_source_id"):
                    source_ids = [str(summary["last_source_id"])]
                if source_id and source_id in source_ids:
                    return RobustBaseline(
                        str(row["state"]),
                        int(row["sample_count"]),
                        summary.get("median"),
                        summary.get("mad"),
                        int(row["version"]),
                    )
                samples = [float(item) for item in summary.get("samples", [])][
                    -self.maximum_samples :
                ]
                excluded = [
                    str(item)
                    for item in json.loads(str(row["excluded_incident_ids_json"]) or "[]")
                ][-128:]
            if severe_incident_id:
                if severe_incident_id not in excluded:
                    excluded.append(severe_incident_id)
                    excluded = excluded[-128:]
            else:
                samples.append(numeric_value)
                samples = samples[-self.maximum_samples :]
            if source_id:
                source_ids.append(source_id)
                source_ids = source_ids[-self.maximum_samples :]
            learner = BaselineLearner(
                minimum_samples=self.minimum_samples,
                maximum_samples=self.maximum_samples,
                version=version,
            )
            learner._samples = list(samples)
            result = learner.summary()
            summary_json = json.dumps(
                {
                    "median": result.median,
                    "mad": result.mad,
                    "samples": samples,
                    "window_capacity": self.maximum_samples,
                    "formula": "median_mad_v1",
                    "last_source_id": source_id,
                    "source_ids": source_ids,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            connection.execute(
                "INSERT INTO baseline_versions(baseline_id,metric_name,scope,version,state,"
                "started_at,ended_at,sample_count,summary_json,excluded_incident_ids_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(metric_name,scope,version) DO UPDATE SET "
                "state=excluded.state,ended_at=excluded.ended_at,sample_count=excluded.sample_count,"
                "summary_json=excluded.summary_json,"
                "excluded_incident_ids_json=excluded.excluded_incident_ids_json",
                (
                    baseline_id,
                    metric_name,
                    scope,
                    version,
                    result.state,
                    started_at,
                    timestamp,
                    result.sample_count,
                    summary_json,
                    json.dumps(excluded, separators=(",", ":")),
                ),
            )
        return result

    def latest(self, metric_name: str, scope: str) -> RobustBaseline | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT version,state,sample_count,summary_json FROM baseline_versions "
                "WHERE metric_name=? AND scope=? ORDER BY version DESC LIMIT 1",
                (metric_name, scope),
            ).fetchone()
        if row is None:
            return None
        summary = json.loads(str(row["summary_json"]) or "{}")
        return RobustBaseline(
            str(row["state"]),
            int(row["sample_count"]),
            summary.get("median"),
            summary.get("mad"),
            int(row["version"]),
        )

    def reset(self, metric_name: str, scope: str) -> RobustBaseline:
        timestamp = utc_iso()
        with self.database.transaction() as connection:
            latest = connection.execute(
                "SELECT COALESCE(MAX(version),0) FROM baseline_versions "
                "WHERE metric_name=? AND scope=?",
                (metric_name, scope),
            ).fetchone()
            version = int(latest[0]) + 1
            connection.execute(
                "INSERT INTO baseline_versions(baseline_id,metric_name,scope,version,state,"
                "started_at,ended_at,sample_count,summary_json,excluded_incident_ids_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    metric_name,
                    scope,
                    version,
                    "learning",
                    timestamp,
                    None,
                    0,
                    json.dumps(
                        {
                            "median": None,
                            "mad": None,
                            "samples": [],
                            "window_capacity": self.maximum_samples,
                            "formula": "median_mad_v1",
                            "last_source_id": None,
                            "source_ids": [],
                        },
                        separators=(",", ":"),
                    ),
                    "[]",
                ),
            )
        return RobustBaseline("learning", 0, None, None, version)
