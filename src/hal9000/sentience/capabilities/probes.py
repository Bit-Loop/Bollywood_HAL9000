"""Small exact host probes; failures remain observations, never authority guesses."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from hal9000.sentience.models import CapabilityLifecycle


@dataclass(frozen=True, slots=True)
class ProbeResult:
    capability_id: str
    state: CapabilityLifecycle
    evidence: dict[str, object]
    confidence: float = 1.0


def probe_terminal() -> ProbeResult:
    shell = os.environ.get("SHELL") or ""
    executable = shutil.which(Path(shell).name) if shell else shutil.which("sh")
    return ProbeResult(
        "terminal",
        CapabilityLifecycle.READY if executable else CapabilityLifecycle.UNAVAILABLE,
        {"shell": shell, "executable": executable, "verified": bool(executable)},
    )


def probe_filesystem(path: Path) -> tuple[ProbeResult, ProbeResult]:
    resolved = path.resolve(strict=False)
    return (
        ProbeResult("filesystem_read", CapabilityLifecycle.READY if os.access(resolved, os.R_OK) else CapabilityLifecycle.DENIED, {"path": str(resolved), "mode": "read"}),
        ProbeResult("filesystem_write", CapabilityLifecycle.READY if os.access(resolved, os.W_OK) else CapabilityLifecycle.DENIED, {"path": str(resolved), "mode": "write"}),
    )
