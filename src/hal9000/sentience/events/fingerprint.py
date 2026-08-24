"""Stable fingerprints for already-redacted normalized observations."""

from __future__ import annotations

import hashlib
import hmac

from hal9000.sentience.events.normalize import NormalizedObservation


def stable_fingerprint(
    observation: NormalizedObservation, *, key: bytes | None = None
) -> str:
    data = observation.canonical.encode("utf-8")
    digest = hmac.new(key, data, hashlib.sha256).hexdigest() if key else hashlib.sha256(data).hexdigest()
    return ("hmac-sha256:" if key else "sha256:") + digest
