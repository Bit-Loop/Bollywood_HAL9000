"""Secret redaction that runs before persistence, indexing, or hashing."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from itertools import islice
from typing import Any

REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"(?:^|[_-])(api[_-]?key|authorization|bearer|credential|passwd|password|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=?=?"),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])-[-A-Za-z0-9_]{8,}\b", re.IGNORECASE),
    re.compile(r"(?i)(?:password|passwd|token|api[_-]?key)\s*[=:]\s*[^\s,;]+"),
)


def redact_text(value: str) -> str:
    clean = value
    for pattern in _TEXT_PATTERNS:
        clean = pattern.sub(REDACTED, clean)
    return clean


def redact_data(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 24:
        return "[DEPTH-LIMIT]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in islice(value.items(), 10_000):
            key = str(raw_key)[:256]
            result[key] = REDACTED if _SECRET_KEY.search(key) else redact_data(item, _depth=_depth + 1)
        return result
    if isinstance(value, str):
        return redact_text(value[:1_000_000])
    if isinstance(value, bytes):
        return redact_text(value[:1_000_000].decode("utf-8", errors="replace"))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_data(item, _depth=_depth + 1) for item in islice(value, 10_000)]
    if isinstance(value, float) and not math.isfinite(value):
        return "[NON-FINITE]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def bounded_redacted_record(
    value: Any, *, maximum_bytes: int = 32_768
) -> tuple[Any, str, int, bool]:
    """Return redacted JSON data or a bounded provenance-preserving summary.

    The digest is calculated only after secret redaction. Oversized structured
    input retains its redacted digest, size, top-level keys, and safe preview;
    the full payload never enters SQLite, an index, or a sketch.
    """

    if maximum_bytes < 1024:
        raise ValueError("bounded redacted records require at least 1024 bytes")
    redacted = redact_data(value)
    encoded = json.dumps(
        redacted,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if len(encoded) <= maximum_bytes:
        return redacted, digest, len(encoded), False
    preview_budget = min(8192, maximum_bytes // 2)
    summary = {
        "payload_truncated": True,
        "redacted_sha256": digest,
        "redacted_bytes": len(encoded),
        "top_level_keys": sorted(str(key)[:256] for key in value)[:64]
        if isinstance(value, Mapping)
        else [],
        "redacted_preview": encoded[:preview_budget].decode("utf-8", errors="replace"),
    }
    return summary, digest, len(encoded), True
