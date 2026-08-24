"""Versioned checksum envelope common to every persisted sketch."""

from __future__ import annotations

import hashlib
import json
import struct
from typing import Any

MAGIC = b"HALSK01\0"
SERIALIZATION_VERSION = 1


class SketchSerializationError(ValueError):
    pass


def pack_envelope(metadata: dict[str, Any], blob: bytes) -> bytes:
    header = dict(metadata)
    header["serialization_version"] = SERIALIZATION_VERSION
    header["checksum"] = "sha256:" + hashlib.sha256(blob).hexdigest()
    encoded = json.dumps(
        header,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > 1_000_000:
        raise SketchSerializationError("sketch metadata exceeds its hard limit")
    return MAGIC + struct.pack(">I", len(encoded)) + encoded + blob


def unpack_envelope(data: bytes) -> tuple[dict[str, Any], bytes]:
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 4:
        raise SketchSerializationError("invalid sketch envelope magic")
    header_length = struct.unpack(">I", data[len(MAGIC) : len(MAGIC) + 4])[0]
    start = len(MAGIC) + 4
    end = start + header_length
    if header_length > 1_000_000 or end > len(data):
        raise SketchSerializationError("invalid sketch envelope header length")
    try:
        metadata = json.loads(data[start:end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SketchSerializationError("invalid sketch metadata") from exc
    if not isinstance(metadata, dict):
        raise SketchSerializationError("sketch metadata must be an object")
    if metadata.get("serialization_version") != SERIALIZATION_VERSION:
        raise SketchSerializationError("unsupported sketch serialization version")
    blob = data[end:]
    expected = "sha256:" + hashlib.sha256(blob).hexdigest()
    if metadata.get("checksum") != expected:
        raise SketchSerializationError("sketch checksum mismatch")
    return metadata, blob
