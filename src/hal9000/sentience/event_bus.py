"""Bounded non-blocking telemetry ingestion with exact-control reserve."""

from __future__ import annotations

import queue
import hashlib
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass

from hal9000.config import SentienceIngestionSettings
from hal9000.sentience.event_envelope import EventEnvelope, utc_iso
from hal9000.sentience.events.coalescer import EventRunCoalescer, EventRunInput
from hal9000.sentience.events.fingerprint import stable_fingerprint
from hal9000.sentience.events.normalize import normalize_observation
from hal9000.sentience.models import (
    EventOrigin,
    RetentionClass,
    Sensitivity,
    Severity,
    StoredEvent,
)
from hal9000.sentience.storage.database import Projection, SentienceDatabase
from hal9000.sentience.storage.writer import ExactEventWriter


@dataclass(slots=True)
class _DropCounter:
    source: str
    event_type: str
    count: int
    first_at: str
    last_at: str
    reason: str


class BoundedEventBus:
    def __init__(
        self,
        database: SentienceDatabase,
        settings: SentienceIngestionSettings,
        *,
        boot_id: str,
        autostart: bool = True,
    ) -> None:
        telemetry_capacity = settings.queue_capacity - settings.exact_reserve
        if telemetry_capacity <= 0:
            raise ValueError("ingestion queue must leave capacity beyond its exact reserve")
        self.database = database
        self.settings = settings
        self.boot_id = boot_id
        self.writer = ExactEventWriter(database, settings.exact_reserve)
        self.coalescer = EventRunCoalescer(database, settings)
        self._telemetry: queue.Queue[EventRunInput] = queue.Queue(maxsize=telemetry_capacity)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="hal-sentience-telemetry", daemon=True
        )
        self._started = False
        self._closed = False
        self._drop_lock = threading.Lock()
        self._drops: dict[tuple[str, str, str], _DropCounter] = {}
        self._summary_nonce = uuid.uuid4().hex
        self._high_fingerprints: OrderedDict[str, None] = OrderedDict()
        self._high_fingerprint_capacity = 1024
        self._fingerprint_lock = threading.Lock()
        self._internal_sample_counter = 0
        self._internal_sample_lock = threading.Lock()
        if autostart:
            self.start()

    def start(self) -> None:
        if self._closed or self._started:
            return
        self._started = True
        self.writer.start()
        self._thread.start()

    def publish_exact(
        self, event: EventEnvelope, projection: Projection | None = None
    ) -> Future[StoredEvent]:
        return self.writer.submit(event, projection)

    @property
    def telemetry_queue_depth(self) -> int:
        return self._telemetry.qsize()

    def publish_observation(self, event: EventRunInput) -> bool:
        if self._closed:
            return False
        if not self.database.non_authority_state_writes_allowed(2048):
            self._record_drop(event, "state-database telemetry allocation exhausted")
            return False
        if event.source.startswith("hal.sentience") and event.severity not in {
            Severity.ERROR,
            Severity.CRITICAL,
        }:
            if not self._sample_internal_event():
                self._record_drop(event, "routine internal event sampled out")
                return False
        if Severity(event.severity) in {Severity.ERROR, Severity.CRITICAL}:
            self._persist_first_high_severity(event)
        try:
            self._telemetry.put_nowait(event)
            return True
        except queue.Full:
            self._record_drop(event, "bounded telemetry queue full")
            return False

    def _sample_internal_event(self) -> bool:
        rate = float(self.settings.internal_event_sample_rate)
        if rate <= 0:
            return False
        if rate >= 1:
            return True
        with self._internal_sample_lock:
            ordinal = self._internal_sample_counter
            self._internal_sample_counter += 1
        score = int.from_bytes(
            hashlib.blake2b(
                f"{self._summary_nonce}:{ordinal}".encode(), digest_size=8
            ).digest(),
            "big",
        ) / float(2**64)
        return score < rate

    def _persist_first_high_severity(self, item: EventRunInput) -> None:
        observation = normalize_observation(
            item.source, item.type, item.subject, item.redacted_payload
        )
        fingerprint = stable_fingerprint(observation)
        with self._fingerprint_lock:
            if fingerprint in self._high_fingerprints:
                self._high_fingerprints.move_to_end(fingerprint)
                return
        key = f"first-high-severity:{fingerprint}"
        with self.database.read_connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM exact_events WHERE idempotency_key=?", (key,)
            ).fetchone()
        with self._fingerprint_lock:
            self._high_fingerprints[fingerprint] = None
            if len(self._high_fingerprints) > self._high_fingerprint_capacity:
                self._high_fingerprints.popitem(last=False)
        if exists is not None:
            return
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.sentience.ingestion",
            event_type="telemetry.high_severity.first",
            subject="fingerprint",
            severity=Severity(item.severity),
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={
                "fingerprint": fingerprint,
                "source": item.source,
                "type": item.type,
                "subject": item.subject,
            },
            task_id=item.task_id,
            idempotency_key=key,
            internal=True,
        )
        self.publish_exact(event)

    def _record_drop(self, event: EventRunInput, reason: str) -> None:
        stamp = utc_iso(event.observed_at)
        key = (event.source, event.type, reason)
        with self._drop_lock:
            counter = self._drops.get(key)
            if counter is None and len(self._drops) >= 128:
                key = ("multiple", "multiple", reason)
                counter = self._drops.get(key)
            if counter is None:
                self._drops[key] = _DropCounter(
                    key[0], key[1], 1, stamp, stamp, reason
                )
            else:
                counter.count += 1
                counter.last_at = stamp

    def _run(self) -> None:
        flush_interval = max(0.001, self.settings.flush_interval_ms / 1000)
        next_flush = time.monotonic() + flush_interval
        while not self._stop.is_set() or not self._telemetry.empty():
            timeout = min(0.05, max(0.001, next_flush - time.monotonic()))
            try:
                item = self._telemetry.get(timeout=timeout)
            except queue.Empty:
                item = None
            if item is not None:
                try:
                    self.coalescer.add(item)
                finally:
                    self._telemetry.task_done()
            if time.monotonic() >= next_flush:
                self.coalescer.flush()
                next_flush = time.monotonic() + flush_interval

    def _emit_drop_summary(self) -> Future[StoredEvent] | None:
        with self._drop_lock:
            if not self._drops:
                return None
            counters = list(self._drops.values())
            self._drops.clear()
        total = sum(item.count for item in counters)
        started = min(item.first_at for item in counters)
        ended = max(item.last_at for item in counters)
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.sentience.ingestion",
            event_type="telemetry.dropped",
            subject="telemetry",
            severity=Severity.WARNING,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={
                "count": total,
                "first_at": started,
                "last_at": ended,
                "groups": [
                    {
                        "source": item.source,
                        "type": item.event_type,
                        "count": item.count,
                        "reason": item.reason,
                    }
                    for item in counters
                ],
            },
            idempotency_key=f"telemetry-dropped:{self.boot_id}:{self._summary_nonce}",
            internal=True,
        )
        return self.publish_exact(event)

    def flush(self) -> None:
        if not self._started:
            self.start()
        self._telemetry.join()
        self.coalescer.flush()
        summary = self._emit_drop_summary()
        if summary is not None:
            summary.result(timeout=10)
        self.writer.flush()

    def close(self) -> None:
        if self._closed:
            return
        if not self._started:
            self.start()
        self._telemetry.join()
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("telemetry event worker did not stop cleanly")
        self.coalescer.close()
        summary = self._emit_drop_summary()
        if summary is not None:
            summary.result(timeout=10)
        self.writer.close()
        self._closed = True
