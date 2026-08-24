"""Time-bucket boundaries and configurable duration parsing."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_DURATION = re.compile(r"^(\d+)(s|m|h|d)$")


def duration_seconds(value: str) -> int:
    match = _DURATION.fullmatch(value.strip().lower())
    if not match:
        raise ValueError(f"invalid duration {value!r}")
    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    result = amount * multiplier
    if result <= 0:
        raise ValueError("duration must be positive")
    return result


def bucket_bounds(observed_at: datetime, width_seconds: int) -> tuple[datetime, datetime]:
    if observed_at.tzinfo is None:
        raise ValueError("bucket timestamps must be timezone-aware")
    if width_seconds <= 0:
        raise ValueError("bucket width must be positive")
    utc = observed_at.astimezone(UTC)
    second = int(utc.timestamp())
    start = datetime.fromtimestamp(second - second % width_seconds, UTC)
    return start, start + timedelta(seconds=width_seconds)


def bucket_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
