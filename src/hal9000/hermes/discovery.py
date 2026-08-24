"""Read-only discovery of the installed Hermes runtime."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True, slots=True)
class HermesInstallation:
    executable: Path | None
    version: str
    install_directory: Path | None
    available: bool
    detail: str = ""


def discover_hermes(preferred: str = "") -> HermesInstallation:
    candidate = preferred.strip() or shutil.which("hermes") or ""
    if not candidate:
        return HermesInstallation(None, "", None, False, "Hermes executable not found")
    executable = Path(candidate).expanduser().resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return HermesInstallation(executable, "", None, False, "Hermes executable is not runnable")
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return HermesInstallation(executable, "", None, False, str(exc))
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    version_match = re.search(r"Hermes Agent v([^\s]+)", output)
    directory_match = re.search(r"^Install directory:\s*(.+)$", output, re.MULTILINE)
    directory = Path(directory_match.group(1)).expanduser() if directory_match else None
    return HermesInstallation(
        executable=executable,
        version=version_match.group(1) if version_match else "unknown",
        install_directory=directory,
        available=result.returncode == 0,
        detail=output,
    )


def is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def probe_backend(base_url: str, token: str = "", timeout: float = 2.0) -> tuple[bool, float, str]:
    """Return health, latency in milliseconds, and a concise detail."""
    import time

    started = time.perf_counter()
    headers = {"x-hermes-session-token": token} if token else {}
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/api/status",
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        return False, (time.perf_counter() - started) * 1000, str(exc)
    latency = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        return False, latency, f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    label = str(payload.get("status") or payload.get("state") or "ready")
    return True, latency, label
