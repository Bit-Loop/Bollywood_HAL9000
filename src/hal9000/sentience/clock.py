"""Wall and monotonic time with explicit UTC jump detection."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class ClockReading:
    utc: datetime
    monotonic_ns: int


@dataclass(frozen=True, slots=True)
class ClockJump:
    direction: str
    wall_elapsed: timedelta
    monotonic_elapsed: timedelta
    discrepancy: timedelta


class MachineClock:
    def __init__(self, jump_threshold_seconds: float = 5.0) -> None:
        self.jump_threshold = timedelta(seconds=max(0.0, jump_threshold_seconds))
        self._last: ClockReading | None = None

    @staticmethod
    def now() -> ClockReading:
        return ClockReading(datetime.now(UTC), time.monotonic_ns())

    @staticmethod
    def duration(start: ClockReading, end: ClockReading) -> timedelta:
        if end.monotonic_ns < start.monotonic_ns:
            raise ValueError("monotonic time moved backward within one boot")
        return timedelta(microseconds=(end.monotonic_ns - start.monotonic_ns) / 1000)

    def observe(self, reading: ClockReading | None = None) -> ClockJump | None:
        current = reading or self.now()
        if current.utc.tzinfo is None or current.utc.utcoffset() != timedelta(0):
            raise ValueError("wall clock readings must be timezone-aware UTC")
        previous = self._last
        self._last = current
        if previous is None:
            return None
        monotonic_elapsed = self.duration(previous, current)
        wall_elapsed = current.utc - previous.utc
        discrepancy = wall_elapsed - monotonic_elapsed
        if abs(discrepancy) <= self.jump_threshold:
            return None
        return ClockJump(
            direction="backward" if discrepancy.total_seconds() < 0 else "forward",
            wall_elapsed=wall_elapsed,
            monotonic_elapsed=monotonic_elapsed,
            discrepancy=discrepancy,
        )
