"""Versioned, source-specific observation normalization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hal9000.sentience.events.redact import redact_data

_JOURNAL_VOLATILE = {
    "_PID",
    "SYSLOG_PID",
    "_SOURCE_REALTIME_TIMESTAMP",
    "__REALTIME_TIMESTAMP",
    "_MONOTONIC_TIMESTAMP",
    "INVOCATION_ID",
}
_HERMES_VOLATILE = {"delta", "token", "chunk_index", "received_at"}
_BROWSER_VOLATILE = {"request_id", "trace_id", "span_id", "timestamp"}
NORMALIZATION_VERSIONS = {"journald": 1, "hermes": 1, "browser": 1, "default": 1}


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    source: str
    type: str
    subject: str
    normalization_version: int
    payload: dict[str, Any]
    canonical: str


def normalize_observation(
    source: str, event_type: str, subject: str, payload: dict[str, Any]
) -> NormalizedObservation:
    """Normalize only fields known to be volatile for a particular source.

    Meaning-bearing numbers, paths, ports, exit codes, device names, models,
    capabilities, and correlation fields are deliberately preserved.
    """

    family = source.split(".", 1)[0].lower()
    if family == "journald":
        omitted = _JOURNAL_VOLATILE
    elif family == "hermes":
        omitted = _HERMES_VOLATILE if event_type == "model.token.delta" else set()
    elif family == "browser":
        omitted = _BROWSER_VOLATILE
    else:
        family = "default"
        omitted = set()
    safe = redact_data(payload)
    normalized = {
        str(key): value
        for key, value in safe.items()
        if str(key) not in omitted
    }
    canonical = json.dumps(
        {
            "normalization_version": NORMALIZATION_VERSIONS[family],
            "source": source,
            "type": event_type,
            "subject": subject,
            "payload": normalized,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return NormalizedObservation(
        source=source,
        type=event_type,
        subject=subject,
        normalization_version=NORMALIZATION_VERSIONS[family],
        payload=normalized,
        canonical=canonical,
    )
