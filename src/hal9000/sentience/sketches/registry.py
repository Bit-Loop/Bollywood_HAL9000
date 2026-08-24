"""Source-gated persistent sketch registry and verified time roll-ups."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hal9000.config import SentienceSketchSettings
from hal9000.paths import AppPaths
from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.models import CardinalityEstimate
from hal9000.sentience.sketches.buckets import bucket_bounds, bucket_iso, duration_seconds
from hal9000.sentience.sketches.hybrid_distinct import (
    HybridDistinctBucket,
    IncompatibleSketchError,
    keyed_item_digest,
)
from hal9000.sentience.sketches.frequency import FrequencySketch, HeavyHitter
from hal9000.sentience.sketches.quantiles import QuantileSketch, QuantileSummary
from hal9000.sentience.sketches.serialization import pack_envelope, unpack_envelope
from hal9000.sentience.sketches.theta import RelationshipEstimate, ThetaSetSketch
from hal9000.sentience.storage.database import SentienceDatabase

_KEY_MAGIC = b"HALKEY01"


class MetricUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SketchKey:
    version: int
    key: bytes


class SketchKeyStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self) -> SketchKey:
        if self.path.exists():
            data = self.path.read_bytes()
            if len(data) != len(_KEY_MAGIC) + 4 + 32 or not data.startswith(_KEY_MAGIC):
                raise ValueError("sketch HMAC key file is malformed")
            if os.stat(self.path).st_mode & 0o077:
                os.chmod(self.path, 0o600)
            return SketchKey(struct.unpack(">I", data[8:12])[0], data[12:])
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = SketchKey(1, os.urandom(32))
        self._write(key)
        return key

    def rotate(self) -> SketchKey:
        current = self.load_or_create()
        replacement = SketchKey(current.version + 1, os.urandom(32))
        self._write(replacement)
        return replacement

    def _write(self, key: SketchKey) -> None:
        fd, name = tempfile.mkstemp(prefix="sketch-key.", dir=self.path.parent)
        temporary = Path(name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(_KEY_MAGIC + struct.pack(">I", key.version) + key.key)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    event_sources: frozenset[str]
    bucket_seconds: int


def default_distinct_metrics(default_bucket_seconds: int) -> dict[str, MetricDefinition]:
    sources = {
        "unique_error_fingerprints": {"error.fingerprint"},
        "unique_processes_observed": {"process.observed"},
        "unique_files_touched_per_task": {"filesystem.touched"},
        "unique_tools_invoked_per_session": {"hermes.tool"},
        "unique_network_peers": {"network.peer"},
        "unique_repository_objects_examined": {"repository.object_examined"},
        "unique_memory_subjects_retrieved": {"memory.subject_retrieved"},
        "unique_failed_capabilities": {"capability.failed"},
        "unique_contradiction_subjects": {"contradiction.subject"},
        "unique_external_sources_consulted": {"external.source_consulted"},
    }
    return {
        name: MetricDefinition(name, frozenset(metric_sources), default_bucket_seconds)
        for name, metric_sources in sources.items()
    }


def _stream_metric_definitions(default_bucket_seconds: int) -> dict[str, MetricDefinition]:
    sources = {
        "repeating_error_fingerprints": {"error.fingerprint"},
        "frequent_failing_services": {"service.failure"},
        "frequent_tool_failures": {"hermes.tool.failure"},
        "commonly_retrieved_memory_subjects": {"memory.subject_retrieved"},
        "repeated_network_peers": {"network.peer"},
        "model_latency": {"hermes.model.latency"},
        "time_to_first_token": {"hermes.model.latency"},
        "time_to_first_audio": {"hal.audio.latency"},
        "tool_latency": {"hermes.tool.latency"},
        "cpu_utilization": {"resource.sample"},
        "gpu_utilization": {"resource.sample"},
        "memory_utilization": {"resource.sample"},
        "disk_utilization": {"resource.sample"},
        "queue_depth": {"queue.sample"},
        "event_ingestion_latency": {"hal.ingestion.latency"},
        "retrieval_latency": {"hal.retrieval.latency"},
        "context_utilization": {"hermes.context"},
        "error_novelty": {"error.fingerprint"},
        "tool_failure_novelty": {"hermes.tool.failure"},
        "repository_objects_by_pass": {"repository.object_examined"},
        "degradation_error_overlap": {"error.fingerprint"},
    }
    return {
        name: MetricDefinition(name, frozenset(metric_sources), default_bucket_seconds)
        for name, metric_sources in sources.items()
    }


_FREQUENCY_METRICS = frozenset(
    {
        "repeating_error_fingerprints",
        "frequent_failing_services",
        "frequent_tool_failures",
        "commonly_retrieved_memory_subjects",
        "repeated_network_peers",
    }
)
_QUANTILE_METRICS = frozenset(
    {
        "model_latency",
        "time_to_first_token",
        "time_to_first_audio",
        "tool_latency",
        "cpu_utilization",
        "gpu_utilization",
        "memory_utilization",
        "disk_utilization",
        "queue_depth",
        "event_ingestion_latency",
        "retrieval_latency",
        "context_utilization",
    }
)
_THETA_METRICS = frozenset(
    {
        "error_novelty",
        "tool_failure_novelty",
        "repository_objects_by_pass",
        "degradation_error_overlap",
    }
)


class SketchRegistry:
    def __init__(
        self,
        database: SentienceDatabase,
        paths: AppPaths,
        settings: SentienceSketchSettings,
    ) -> None:
        self.database = database
        self.paths = paths
        self.settings = settings
        self.key_store = SketchKeyStore(paths.sentience_hmac_key)
        self.key = self.key_store.load_or_create()
        self.metrics = default_distinct_metrics(duration_seconds(settings.hot_bucket))
        self.streaming_metrics = _stream_metric_definitions(
            duration_seconds(settings.hot_bucket)
        )
        self._permitted_sources = frozenset().union(
            *(
                definition.event_sources
                for definition in {**self.metrics, **self.streaming_metrics}.values()
            )
        )
        self._sources: set[str] = set()
        self._lock = threading.RLock()

    def register_event_source(self, source: str) -> tuple[str, ...]:
        # Event source registration is a finite schema operation, not an
        # arbitrary telemetry-label cache. Unknown labels cannot grow this
        # process for the lifetime of a noisy or malicious source.
        with self._lock:
            if source in self._permitted_sources:
                self._sources.add(source)
            enabled_sources = frozenset(self._sources)
            return tuple(
                sorted(
                    name
                    for name, definition in {
                        **self.metrics,
                        **self.streaming_metrics,
                    }.items()
                    if definition.event_sources & enabled_sources
                )
            )

    def enabled(self, metric_name: str) -> bool:
        definition = self.metrics.get(metric_name) or self.streaming_metrics.get(metric_name)
        with self._lock:
            return bool(definition and definition.event_sources & self._sources)

    def update_distinct(
        self,
        metric_name: str,
        scope: str,
        item: bytes | str | int,
        observed_at: datetime,
    ) -> CardinalityEstimate:
        definition = self.metrics.get(metric_name)
        if definition is None:
            raise MetricUnavailable(f"unknown distinct metric {metric_name}")
        if not self.enabled(metric_name):
            raise MetricUnavailable(f"metric {metric_name} has no registered real event source")
        if not self.database.non_authority_state_writes_allowed(8192):
            raise MetricUnavailable("state-database sketch allocation is full")
        start, end = bucket_bounds(observed_at, definition.bucket_seconds)
        start_text, end_text = bucket_iso(start), bucket_iso(end)
        with self._lock:
            bucket = self._load_or_new(
                metric_name, scope, start_text, end_text, definition.bucket_seconds
            )
            bucket.update(item)
            self._persist(bucket, definition.bucket_seconds, sealed=False)
            return bucket.estimate()

    def _load_or_new(
        self,
        metric_name: str,
        scope: str,
        start: str,
        end: str,
        width_seconds: int,
    ) -> HybridDistinctBucket:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT blob,key_version,sealed FROM sketch_buckets WHERE metric_name=? AND scope=? "
                "AND bucket_start=? AND bucket_width_seconds=? AND key_version=?",
                (metric_name, scope, start, width_seconds, self.key.version),
            ).fetchone()
        if row is not None:
            if bool(row["sealed"]):
                raise MetricUnavailable("a sealed distinct bucket cannot accept late updates")
            return HybridDistinctBucket.deserialize(bytes(row["blob"]), hmac_key=self.key.key)
        return HybridDistinctBucket(
            metric_name=metric_name,
            scope=scope,
            bucket_start=start,
            bucket_end=end,
            hmac_key=self.key.key,
            key_version=self.key.version,
            exact_threshold=self.settings.exact_threshold,
            exact_bytes_limit=self.settings.exact_bytes_limit,
            hll_lg_k=self.settings.hll_lg_k,
            hll_target_type=self.settings.hll_target_type,
        )

    def _persist(
        self, bucket: HybridDistinctBucket, width_seconds: int, *, sealed: bool
    ) -> str:
        serialized = bucket.serialize()
        metadata, _blob = unpack_envelope(serialized)
        estimate = bucket.estimate()
        bucket_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hal-sketch:{bucket.metric_name}:{bucket.scope}:{bucket.bucket_start}:"
                f"{width_seconds}:{bucket.key_version}",
            )
        )
        checksum = "sha256:" + hashlib.sha256(serialized).hexdigest()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO sketch_buckets(bucket_id,metric_name,scope,bucket_start,bucket_end,"
                "bucket_width_seconds,mode,sketch_kind,parameters_json,key_version,library,"
                "library_version,serialization_version,item_updates,estimate,lower_bound,"
                "upper_bound,sealed,rollup_state,last_updated_at,checksum,blob) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, ?,?,?) "
                "ON CONFLICT(metric_name,scope,bucket_start,bucket_width_seconds,key_version) "
                "DO UPDATE SET mode=excluded.mode,sketch_kind=excluded.sketch_kind,parameters_json=excluded.parameters_json,"
                "item_updates=excluded.item_updates,estimate=excluded.estimate,"
                "lower_bound=excluded.lower_bound,upper_bound=excluded.upper_bound,"
                "sealed=MAX(sketch_buckets.sealed,excluded.sealed),"
                "last_updated_at=excluded.last_updated_at,checksum=excluded.checksum,blob=excluded.blob",
                (
                    bucket_id,
                    bucket.metric_name,
                    bucket.scope,
                    bucket.bucket_start,
                    bucket.bucket_end,
                    width_seconds,
                    bucket.mode.value,
                    str(metadata["sketch_kind"]),
                    json.dumps(bucket.parameters, sort_keys=True, separators=(",", ":")),
                    bucket.key_version,
                    str(metadata["library"]),
                    str(metadata["library_version"]),
                    int(metadata["serialization_version"]),
                    bucket.item_updates,
                    estimate.estimate,
                    estimate.lower_bound,
                    estimate.upper_bound,
                    int(sealed),
                    "verified_parent" if sealed and width_seconds > duration_seconds(self.settings.hot_bucket) else "none",
                    utc_iso(),
                    checksum,
                    serialized,
                ),
            )
        return bucket_id

    def update_frequency(
        self,
        metric_name: str,
        scope: str,
        item: bytes | str | int,
        observed_at: datetime,
        *,
        weight: int = 1,
        lg_max_k: int = 10,
    ) -> tuple[HeavyHitter, ...]:
        definition = self._stream_definition(metric_name, _FREQUENCY_METRICS)
        start, end = bucket_bounds(observed_at, definition.bucket_seconds)
        start_text, end_text = bucket_iso(start), bucket_iso(end)
        with self._lock:
            row = self._stream_row(
                metric_name,
                scope,
                start_text,
                definition.bucket_seconds,
                self.key.version,
            )
            if row is not None and bool(row["sealed"]):
                raise MetricUnavailable("a sealed frequency bucket cannot accept late updates")
            sketch = (
                FrequencySketch.deserialize(bytes(row["blob"]))
                if row is not None
                else FrequencySketch(metric_name, scope, lg_max_k=lg_max_k)
            )
            digest = keyed_item_digest(
                item, metric_name=metric_name, key=self.key.key
            ).hex()
            sketch.update(digest, weight)
            self._persist_stream(
                metric_name,
                scope,
                start_text,
                end_text,
                definition.bucket_seconds,
                self.key.version,
                sketch.serialize(),
                mode="FREQUENCY",
                sketch_kind="frequent_strings",
                item_updates=sketch.item_updates,
            )
            return sketch.frequent_items(no_false_negatives=True)

    def update_quantile(
        self,
        metric_name: str,
        scope: str,
        value: float,
        observed_at: datetime,
        *,
        k: int = 200,
        minimum_samples: int = 20,
    ) -> QuantileSummary:
        definition = self._stream_definition(metric_name, _QUANTILE_METRICS)
        start, end = bucket_bounds(observed_at, definition.bucket_seconds)
        start_text, end_text = bucket_iso(start), bucket_iso(end)
        with self._lock:
            row = self._stream_row(
                metric_name, scope, start_text, definition.bucket_seconds, 0
            )
            if row is not None and bool(row["sealed"]):
                raise MetricUnavailable("a sealed quantile bucket cannot accept late updates")
            sketch = (
                QuantileSketch.deserialize(bytes(row["blob"]))
                if row is not None
                else QuantileSketch(
                    metric_name, scope, k=k, minimum_samples=minimum_samples
                )
            )
            sketch.update(value)
            summary = sketch.summary()
            self._persist_stream(
                metric_name,
                scope,
                start_text,
                end_text,
                definition.bucket_seconds,
                0,
                sketch.serialize(),
                mode="KLL",
                sketch_kind="kll_floats",
                item_updates=sketch.sample_count,
                estimate=summary.p50,
            )
            return summary

    def update_theta(
        self,
        metric_name: str,
        scope: str,
        item: bytes | str | int,
        observed_at: datetime,
        *,
        lg_k: int = 12,
        seed: int = 9001,
    ) -> CardinalityEstimate:
        definition = self._stream_definition(metric_name, _THETA_METRICS)
        start, end = bucket_bounds(observed_at, definition.bucket_seconds)
        start_text, end_text = bucket_iso(start), bucket_iso(end)
        with self._lock:
            row = self._stream_row(
                metric_name,
                scope,
                start_text,
                definition.bucket_seconds,
                self.key.version,
            )
            if row is not None and bool(row["sealed"]):
                raise MetricUnavailable("a sealed Theta bucket cannot accept late updates")
            sketch = (
                ThetaSetSketch.deserialize(bytes(row["blob"]), hmac_key=self.key.key)
                if row is not None
                else ThetaSetSketch(
                    metric_name,
                    scope,
                    hmac_key=self.key.key,
                    key_version=self.key.version,
                    lg_k=lg_k,
                    seed=seed,
                )
            )
            sketch.update(item)
            estimate = sketch.estimate()
            self._persist_stream(
                metric_name,
                scope,
                start_text,
                end_text,
                definition.bucket_seconds,
                self.key.version,
                sketch.serialize(),
                mode="THETA",
                sketch_kind="theta",
                item_updates=sketch.item_updates,
                estimate=estimate.estimate,
                lower_bound=estimate.lower_bound,
                upper_bound=estimate.upper_bound,
            )
            return estimate

    def theta_relationship(
        self,
        metric_name: str,
        scope: str,
        *,
        left_bucket_start: datetime,
        right_bucket_start: datetime,
        operation: str = "difference",
    ) -> RelationshipEstimate:
        definition = self._stream_definition(metric_name, _THETA_METRICS)
        left_start, _ = bucket_bounds(left_bucket_start, definition.bucket_seconds)
        right_start, _ = bucket_bounds(right_bucket_start, definition.bucket_seconds)
        left_row = self._stream_row(
            metric_name,
            scope,
            bucket_iso(left_start),
            definition.bucket_seconds,
            self.key.version,
        )
        right_row = self._stream_row(
            metric_name,
            scope,
            bucket_iso(right_start),
            definition.bucket_seconds,
            self.key.version,
        )
        if left_row is None or right_row is None:
            raise MetricUnavailable("both Theta buckets are required for comparison")
        left = ThetaSetSketch.deserialize(bytes(left_row["blob"]), hmac_key=self.key.key)
        right = ThetaSetSketch.deserialize(bytes(right_row["blob"]), hmac_key=self.key.key)
        operations = {
            "union": left.union,
            "intersection": left.intersection,
            "difference": left.difference,
            "jaccard": left.jaccard,
        }
        try:
            return operations[operation](right)
        except KeyError as exc:
            raise ValueError("Theta operation must be union, intersection, difference, or jaccard") from exc

    def latest_quantile(
        self, metric_name: str, scope: str
    ) -> tuple[QuantileSummary, QuantileSketch] | None:
        if metric_name not in _QUANTILE_METRICS:
            raise MetricUnavailable(f"{metric_name} is not a KLL metric")
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT blob FROM sketch_buckets WHERE metric_name=? AND scope=? "
                "AND sketch_kind='kll_floats' ORDER BY bucket_start DESC LIMIT 1",
                (metric_name, scope),
            ).fetchone()
        if row is None:
            return None
        sketch = QuantileSketch.deserialize(bytes(row["blob"]))
        return sketch.summary(), sketch

    def latest_heavy_hitters(
        self, metric_name: str, scope: str, *, threshold: int = 0
    ) -> tuple[HeavyHitter, ...]:
        if metric_name not in _FREQUENCY_METRICS:
            raise MetricUnavailable(f"{metric_name} is not a frequency metric")
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT blob,key_version FROM sketch_buckets WHERE metric_name=? AND scope=? "
                "AND sketch_kind='frequent_strings' ORDER BY bucket_start DESC LIMIT 1",
                (metric_name, scope),
            ).fetchone()
        if row is None:
            return ()
        if int(row["key_version"]) != self.key.version:
            raise IncompatibleSketchError("frequency bucket uses an incompatible HMAC key")
        return FrequencySketch.deserialize(bytes(row["blob"])).frequent_items(
            no_false_negatives=True, threshold=threshold
        )

    def _stream_definition(
        self, metric_name: str, allowed: frozenset[str]
    ) -> MetricDefinition:
        definition = self.streaming_metrics.get(metric_name)
        if definition is None or metric_name not in allowed:
            raise MetricUnavailable(f"metric {metric_name} has the wrong or unknown sketch kind")
        if not self.enabled(metric_name):
            raise MetricUnavailable(f"metric {metric_name} has no registered real event source")
        if not self.database.non_authority_state_writes_allowed(8192):
            raise MetricUnavailable("state-database sketch allocation is full")
        return definition

    def _stream_row(
        self,
        metric_name: str,
        scope: str,
        bucket_start: str,
        width_seconds: int,
        key_version: int,
    ):
        with self.database.read_connection() as connection:
            return connection.execute(
                "SELECT * FROM sketch_buckets WHERE metric_name=? AND scope=? "
                "AND bucket_start=? AND bucket_width_seconds=? AND key_version=?",
                (metric_name, scope, bucket_start, width_seconds, key_version),
            ).fetchone()

    def _persist_stream(
        self,
        metric_name: str,
        scope: str,
        bucket_start: str,
        bucket_end: str,
        width_seconds: int,
        key_version: int,
        serialized: bytes,
        *,
        mode: str,
        sketch_kind: str,
        item_updates: int,
        estimate: float | None = None,
        lower_bound: float | None = None,
        upper_bound: float | None = None,
        sealed: bool = False,
    ) -> str:
        metadata, raw_blob = unpack_envelope(serialized)
        metadata.update(
            {
                "metric_name": metric_name,
                "scope": scope,
                "bucket_start": bucket_start,
                "bucket_end": bucket_end,
                "mode": mode,
                "sketch_kind": sketch_kind,
                "key_version": key_version,
                "item_updates": int(item_updates),
                "estimate": estimate,
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
                "sealed": bool(sealed),
            }
        )
        persisted = pack_envelope(metadata, raw_blob)
        bucket_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hal-sketch:{metric_name}:{scope}:{bucket_start}:{width_seconds}:{key_version}",
            )
        )
        checksum = "sha256:" + hashlib.sha256(persisted).hexdigest()
        parameters = metadata.get("parameters") or {}
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO sketch_buckets(bucket_id,metric_name,scope,bucket_start,bucket_end,"
                "bucket_width_seconds,mode,sketch_kind,parameters_json,key_version,library,"
                "library_version,serialization_version,item_updates,estimate,lower_bound,"
                "upper_bound,sealed,rollup_state,last_updated_at,checksum,blob) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'none',?,?,?) "
                "ON CONFLICT(metric_name,scope,bucket_start,bucket_width_seconds,key_version) "
                "DO UPDATE SET mode=excluded.mode,sketch_kind=excluded.sketch_kind,"
                "parameters_json=excluded.parameters_json,item_updates=excluded.item_updates,"
                "estimate=excluded.estimate,lower_bound=excluded.lower_bound,"
                "upper_bound=excluded.upper_bound,sealed=MAX(sketch_buckets.sealed,excluded.sealed),"
                "last_updated_at=excluded.last_updated_at,checksum=excluded.checksum,blob=excluded.blob",
                (
                    bucket_id,
                    metric_name,
                    scope,
                    bucket_start,
                    bucket_end,
                    width_seconds,
                    mode,
                    sketch_kind,
                    json.dumps(parameters, sort_keys=True, separators=(",", ":")),
                    key_version,
                    str(metadata.get("library") or "apache-datasketches"),
                    str(metadata.get("library_version") or "unknown"),
                    int(metadata.get("serialization_version") or 1),
                    int(item_updates),
                    estimate,
                    lower_bound,
                    upper_bound,
                    int(sealed),
                    utc_iso(),
                    checksum,
                    persisted,
                ),
            )
        return bucket_id

    def rollup_stream(
        self,
        metric_name: str,
        scope: str,
        *,
        parent_start: datetime,
        parent_width_seconds: int,
        child_width_seconds: int,
    ) -> dict[str, object]:
        """Merge a KLL, frequency, or Theta family into a verified parent."""

        parent_begin, parent_end = bucket_bounds(parent_start, parent_width_seconds)
        begin_text, end_text = bucket_iso(parent_begin), bucket_iso(parent_end)
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM sketch_buckets WHERE metric_name=? AND scope=? "
                "AND bucket_width_seconds=? AND bucket_start>=? AND bucket_end<=? "
                "AND sealed=1 ORDER BY bucket_start",
                (metric_name, scope, child_width_seconds, begin_text, end_text),
            ).fetchall()
        if not rows:
            raise MetricUnavailable("no sealed stream-sketch children are available")
        kinds = {str(row["sketch_kind"]) for row in rows}
        versions = {int(row["key_version"]) for row in rows}
        if len(kinds) != 1 or len(versions) != 1:
            raise IncompatibleSketchError("roll-up children use incompatible sketch envelopes")
        kind = next(iter(kinds))
        key_version = next(iter(versions))
        if kind == "frequent_strings":
            if key_version != self.key.version:
                raise IncompatibleSketchError("frequency children use a retired HMAC key")
            first = FrequencySketch.deserialize(bytes(rows[0]["blob"]))
            parent = FrequencySketch(metric_name, scope, lg_max_k=first.lg_max_k)
            for row in rows:
                parent.merge(FrequencySketch.deserialize(bytes(row["blob"])))
            serialized = parent.serialize()
            updates = parent.item_updates
            estimate = None
            lower = upper = None
            mode = "FREQUENCY"
        elif kind == "kll_floats":
            first = QuantileSketch.deserialize(bytes(rows[0]["blob"]))
            parent = QuantileSketch(
                metric_name,
                scope,
                k=first.k,
                minimum_samples=first.minimum_samples,
            )
            for row in rows:
                parent.merge(QuantileSketch.deserialize(bytes(row["blob"])))
            summary = parent.summary()
            serialized = parent.serialize()
            updates = parent.sample_count
            estimate = summary.p50
            lower = upper = None
            mode = "KLL"
        elif kind == "theta":
            if key_version != self.key.version:
                raise IncompatibleSketchError("Theta children use a retired HMAC key")
            first = ThetaSetSketch.deserialize(bytes(rows[0]["blob"]), hmac_key=self.key.key)
            parent = ThetaSetSketch(
                metric_name,
                scope,
                hmac_key=self.key.key,
                key_version=key_version,
                lg_k=first.lg_k,
                seed=first.seed,
            )
            for row in rows:
                parent.merge(
                    ThetaSetSketch.deserialize(bytes(row["blob"]), hmac_key=self.key.key)
                )
            cardinality = parent.estimate()
            serialized = parent.serialize()
            updates = parent.item_updates
            estimate = cardinality.estimate
            lower = cardinality.lower_bound
            upper = cardinality.upper_bound
            mode = "THETA"
        else:
            raise MetricUnavailable(f"{kind} does not support stream roll-up")
        parent_id = self._persist_stream(
            metric_name,
            scope,
            begin_text,
            end_text,
            parent_width_seconds,
            key_version,
            serialized,
            mode=mode,
            sketch_kind=kind,
            item_updates=updates,
            estimate=estimate,
            lower_bound=lower,
            upper_bound=upper,
            sealed=True,
        )
        with self.database.read_connection() as connection:
            persisted = connection.execute(
                "SELECT checksum,blob FROM sketch_buckets WHERE bucket_id=?", (parent_id,)
            ).fetchone()
        if persisted is None or str(persisted["checksum"]) != "sha256:" + hashlib.sha256(
            bytes(persisted["blob"])
        ).hexdigest():
            raise RuntimeError("persisted stream-sketch parent failed checksum verification")
        verification = json.dumps(
            {"kind": kind, "item_updates": updates, "child_count": len(rows)},
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE sketch_buckets SET rollup_state='verified_parent' WHERE bucket_id=?",
                (parent_id,),
            )
            for row in rows:
                connection.execute(
                    "INSERT INTO sketch_rollups(parent_bucket_id,child_bucket_id,verified_at,"
                    "verification_json) VALUES(?,?,?,?) ON CONFLICT(parent_bucket_id,child_bucket_id) "
                    "DO UPDATE SET verified_at=excluded.verified_at,"
                    "verification_json=excluded.verification_json",
                    (parent_id, str(row["bucket_id"]), utc_iso(), verification),
                )
                connection.execute(
                    "UPDATE sketch_buckets SET rollup_state='verified_child' WHERE bucket_id=?",
                    (str(row["bucket_id"]),),
                )
        return {
            "parent_bucket_id": parent_id,
            "sketch_kind": kind,
            "item_updates": updates,
            "estimate": estimate,
            "child_count": len(rows),
        }

    def maintain(self, now: datetime, *, maximum_groups: int = 128) -> dict[str, int]:
        """Seal, roll up, and expire sparse buckets under configured policies."""

        if now.tzinfo is None:
            raise ValueError("sketch maintenance time must be timezone-aware")
        now = now.astimezone(UTC)
        sealed = self.seal_due(now)
        hot = duration_seconds(self.settings.hot_bucket)
        warm = duration_seconds(self.settings.warm_bucket)
        cold = duration_seconds(self.settings.cold_bucket)
        rolled = 0
        for child_width, parent_width in ((hot, warm), (warm, cold)):
            with self.database.read_connection() as connection:
                rows = connection.execute(
                    "SELECT metric_name,scope,bucket_start,sketch_kind FROM sketch_buckets "
                    "WHERE bucket_width_seconds=? AND sealed=1 AND rollup_state NOT IN "
                    "('verified_child') ORDER BY bucket_start LIMIT ?",
                    (child_width, maximum_groups),
                ).fetchall()
            parents: set[tuple[str, str, str, str]] = set()
            for row in rows:
                start = datetime.fromisoformat(str(row["bucket_start"]).replace("Z", "+00:00"))
                parent_start, parent_end = bucket_bounds(start, parent_width)
                if parent_end > now:
                    continue
                parents.add(
                    (
                        str(row["metric_name"]),
                        str(row["scope"]),
                        bucket_iso(parent_start),
                        str(row["sketch_kind"]),
                    )
                )
            for metric, scope, parent_text, kind in sorted(parents)[:maximum_groups]:
                parent_start = datetime.fromisoformat(parent_text.replace("Z", "+00:00"))
                try:
                    if kind in {"exact_set", "hll"}:
                        self.rollup_distinct(
                            metric,
                            scope,
                            parent_start=parent_start,
                            parent_width_seconds=parent_width,
                            child_width_seconds=child_width,
                        )
                    else:
                        self.rollup_stream(
                            metric,
                            scope,
                            parent_start=parent_start,
                            parent_width_seconds=parent_width,
                            child_width_seconds=child_width,
                        )
                    rolled += 1
                except (MetricUnavailable, IncompatibleSketchError):
                    continue
        expired = self.expire_verified_children(
            older_than=now - timedelta(seconds=duration_seconds(self.settings.hot_retention)),
            child_width_seconds=hot,
        )
        expired += self.expire_verified_children(
            older_than=now - timedelta(seconds=duration_seconds(self.settings.warm_retention)),
            child_width_seconds=warm,
        )
        expired += self._expire_cold(
            now - timedelta(seconds=duration_seconds(self.settings.cold_retention)), cold
        )
        return {"sealed": sealed, "rollups": rolled, "expired": expired}

    def _expire_cold(self, cutoff: datetime, width_seconds: int) -> int:
        cutoff_text = bucket_iso(cutoff)
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT bucket_id,length(blob) AS bytes FROM sketch_buckets "
                "WHERE bucket_width_seconds=? AND sealed=1 AND bucket_end<=? LIMIT 10000",
                (width_seconds, cutoff_text),
            ).fetchall()
        deleted = 0
        for row in rows:
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO retention_tombstones(tombstone_id,object_type,object_id,"
                    "retention_class,deleted_at,reason,bytes_reclaimed) VALUES(?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        "sketch_bucket",
                        str(row["bucket_id"]),
                        "short",
                        utc_iso(),
                        "cold sketch retention expired",
                        int(row["bytes"] or 0),
                    ),
                )
                connection.execute(
                    "DELETE FROM sketch_rollups WHERE parent_bucket_id=? OR child_bucket_id=?",
                    (str(row["bucket_id"]), str(row["bucket_id"])),
                )
                deleted += connection.execute(
                    "DELETE FROM sketch_buckets WHERE bucket_id=?",
                    (str(row["bucket_id"]),),
                ).rowcount
        return deleted

    def seal_due(self, now: datetime) -> int:
        cutoff = bucket_iso(now)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sketch_buckets SET sealed=1 WHERE sealed=0 AND bucket_end<=?", (cutoff,)
            )
        return cursor.rowcount

    def rollup_distinct(
        self,
        metric_name: str,
        scope: str,
        *,
        parent_start: datetime,
        parent_width_seconds: int,
        child_width_seconds: int,
    ) -> CardinalityEstimate:
        parent_begin, parent_end = bucket_bounds(parent_start, parent_width_seconds)
        begin_text, end_text = bucket_iso(parent_begin), bucket_iso(parent_end)
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM sketch_buckets WHERE metric_name=? AND scope=? "
                "AND bucket_width_seconds=? AND bucket_start>=? AND bucket_end<=? AND sealed=1 "
                "ORDER BY bucket_start",
                (metric_name, scope, child_width_seconds, begin_text, end_text),
            ).fetchall()
        if not rows:
            raise MetricUnavailable("no sealed child buckets are available for roll-up")
        versions = {int(row["key_version"]) for row in rows}
        if versions != {self.key.version}:
            raise IncompatibleSketchError("roll-up children use incompatible key versions")
        parent = HybridDistinctBucket(
            metric_name=metric_name,
            scope=scope,
            bucket_start=begin_text,
            bucket_end=end_text,
            hmac_key=self.key.key,
            key_version=self.key.version,
            exact_threshold=self.settings.exact_threshold,
            exact_bytes_limit=self.settings.exact_bytes_limit,
            hll_lg_k=self.settings.hll_lg_k,
            hll_target_type=self.settings.hll_target_type,
        )
        for row in rows:
            parent.merge(HybridDistinctBucket.deserialize(bytes(row["blob"]), hmac_key=self.key.key))
        parent_serialized = parent.serialize()
        verified = HybridDistinctBucket.deserialize(parent_serialized, hmac_key=self.key.key)
        if verified.estimate().estimate != parent.estimate().estimate:
            raise RuntimeError("serialized parent roll-up did not verify")
        parent_id = self._persist(parent, parent_width_seconds, sealed=True)
        verification = json.dumps(
            {"estimate": parent.estimate().estimate, "child_count": len(rows)},
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.database.transaction() as connection:
            for row in rows:
                connection.execute(
                    "INSERT INTO sketch_rollups(parent_bucket_id,child_bucket_id,verified_at,"
                    "verification_json) VALUES(?,?,?,?) "
                    "ON CONFLICT(parent_bucket_id,child_bucket_id) DO UPDATE SET "
                    "verified_at=excluded.verified_at,verification_json=excluded.verification_json",
                    (parent_id, row["bucket_id"], utc_iso(), verification),
                )
                connection.execute(
                    "UPDATE sketch_buckets SET rollup_state='verified_child' WHERE bucket_id=?",
                    (row["bucket_id"],),
                )
        return parent.estimate()

    def expire_verified_children(
        self, *, older_than: datetime, child_width_seconds: int
    ) -> int:
        cutoff = bucket_iso(older_than)
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT child.* FROM sketch_buckets child WHERE child.bucket_width_seconds=? "
                "AND child.bucket_end<=? AND child.rollup_state='verified_child' AND EXISTS("
                "SELECT 1 FROM sketch_rollups r JOIN sketch_buckets parent "
                "ON parent.bucket_id=r.parent_bucket_id WHERE r.child_bucket_id=child.bucket_id "
                "AND parent.sealed=1) ORDER BY child.bucket_end LIMIT 10000",
                (child_width_seconds, cutoff),
            ).fetchall()
        deleted = 0
        for row in rows:
            with self.database.transaction() as connection:
                parent = connection.execute(
                    "SELECT parent.checksum,parent.blob FROM sketch_rollups r JOIN sketch_buckets parent "
                    "ON parent.bucket_id=r.parent_bucket_id WHERE r.child_bucket_id=? LIMIT 1",
                    (row["bucket_id"],),
                ).fetchone()
                if parent is None or "sha256:" + hashlib.sha256(bytes(parent["blob"])).hexdigest() != parent["checksum"]:
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO retention_tombstones(tombstone_id,object_type,object_id,"
                    "retention_class,deleted_at,reason,replacement_reference,bytes_reclaimed) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        "sketch_bucket",
                        row["bucket_id"],
                        "short",
                        utc_iso(),
                        "verified parent roll-up retained",
                        str(parent["checksum"]),
                        len(bytes(row["blob"])),
                    ),
                )
                connection.execute(
                    "DELETE FROM sketch_rollups WHERE parent_bucket_id=?",
                    (row["bucket_id"],),
                )
                cursor = connection.execute(
                    "DELETE FROM sketch_buckets WHERE bucket_id=? AND rollup_state='verified_child'",
                    (row["bucket_id"],),
                )
                if cursor.rowcount:
                    connection.execute(
                        "DELETE FROM sketch_rollups WHERE child_bucket_id=?",
                        (row["bucket_id"],),
                    )
                deleted += cursor.rowcount
        return deleted
