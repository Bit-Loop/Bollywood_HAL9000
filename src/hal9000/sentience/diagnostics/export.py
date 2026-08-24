"""Atomic redacted support-report export."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from hal9000.sentience.events.redact import redact_data


def export_support_report(report: dict, destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix="hal-support.", suffix=".tmp", dir=destination.parent)
    temporary = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(redact_data(report), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
        os.chmod(destination, 0o600)
        return destination
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
