"""Bounded exact-event writer with a reserved synchronous safety path."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass

from hal9000.sentience.event_envelope import EventEnvelope
from hal9000.sentience.models import StoredEvent
from hal9000.sentience.storage.database import Projection, SentienceDatabase


@dataclass(slots=True)
class _WriteRequest:
    event: EventEnvelope
    projection: Projection | None
    future: Future[StoredEvent]


class ExactEventWriter:
    """Serializes routine exact writes and never discards a control event.

    If the reserved queue itself fills, the caller uses the same database's
    serialized transaction lock as an emergency path. That path is bounded by
    one SQLite transaction and cannot silently convert an exact event into a
    dropped or approximate record.
    """

    def __init__(self, database: SentienceDatabase, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("exact writer capacity must be positive")
        self.database = database
        self._queue: queue.Queue[_WriteRequest] = queue.Queue(maxsize=capacity)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hal-sentience-writer", daemon=True)
        self._started = False
        self._closed = False
        self.synchronous_fallbacks = 0

    def start(self) -> None:
        if self._closed or self._started:
            return
        self._started = True
        self._thread.start()

    def submit(
        self, event: EventEnvelope, projection: Projection | None = None
    ) -> Future[StoredEvent]:
        if self._closed:
            raise RuntimeError("exact event writer is closed")
        future: Future[StoredEvent] = Future()
        request = _WriteRequest(event, projection, future)
        try:
            self._queue.put_nowait(request)
        except queue.Full:
            self.synchronous_fallbacks += 1
            try:
                future.set_result(
                    self.database.append_exact_event(event, projection=projection)
                )
            except BaseException as exc:
                future.set_exception(exc)
        return future

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                request = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                if not request.future.set_running_or_notify_cancel():
                    continue
                request.future.set_result(
                    self.database.append_exact_event(
                        request.event, projection=request.projection
                    )
                )
            except BaseException as exc:
                request.future.set_exception(exc)
            finally:
                self._queue.task_done()

    def flush(self) -> None:
        if not self._started:
            self.start()
        self._queue.join()

    def close(self) -> None:
        if self._closed:
            return
        if not self._started:
            self.start()
        self.flush()
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("exact event writer did not stop cleanly")
        self._closed = True
