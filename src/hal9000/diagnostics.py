"""Read-only subsystem diagnostics presented as pass/warn/fail checks."""

from __future__ import annotations

import importlib.util
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from hal9000.paths import AppPaths


class DiagnosticsRunner(QObject):
    completed = Signal(dict)

    def __init__(self, paths: AppPaths, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.paths = paths
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hal9000-diag")

    @Slot(dict)
    def run(self, runtime: dict[str, Any]) -> None:
        future = self._executor.submit(self._collect, dict(runtime))
        future.add_done_callback(self._done)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _collect(self, runtime: dict[str, Any]) -> dict[str, Any]:
        modules = {
            "PySide6": "GUI",
            "sounddevice": "Microphone/audio",
            "sherpa_onnx": "Wake detector",
            "faster_whisper": "Speech recognition",
            "TTS": "XTTS",
            "piper": "Piper",
        }
        checks: list[dict[str, str]] = []
        for module, label in modules.items():
            available = importlib.util.find_spec(module) is not None
            checks.append(
                {
                    "name": label,
                    "status": "pass" if available else "warn",
                    "detail": f"Python module {module} {'available' if available else 'not installed'}",
                }
            )
        hermes = shutil.which("hermes")
        checks.append(
            {
                "name": "Hermes",
                "status": "pass" if hermes and runtime.get("hermesStatus") == "connected" else "fail",
                "detail": str(runtime.get("hermesStatus") or "offline"),
            }
        )
        checks.append(
            {
                "name": "Microphone",
                "status": "pass" if runtime.get("microphoneStatus") not in {"error", "stopped"} else "warn",
                "detail": str(runtime.get("microphoneStatus") or "unknown"),
            }
        )
        checks.append(
            {
                "name": "CUDA",
                "status": "pass" if str(runtime.get("cuda") or "").startswith("CUDA") else "warn",
                "detail": str(runtime.get("cuda") or "not detected; CPU fallback available"),
            }
        )
        checks.extend(
            [
                {
                    "name": "Wake runtime",
                    "status": "pass" if runtime.get("wakeStatus") == "ready" else "warn",
                    "detail": str(runtime.get("wakeStatus") or "not loaded"),
                },
                {
                    "name": "STT runtime",
                    "status": "pass" if runtime.get("sttStatus") == "ready" else "warn",
                    "detail": f"{runtime.get('sttStatus') or 'not loaded'} / {runtime.get('sttBackend') or 'pending'}",
                },
                {
                    "name": "XTTS",
                    "status": "pass" if runtime.get("xttsStatus") == "ready" else "warn",
                    "detail": str(runtime.get("xttsStatus") or "not loaded"),
                },
                {
                    "name": "Piper",
                    "status": "pass" if runtime.get("piperStatus") == "ready" else "warn",
                    "detail": str(runtime.get("piperStatus") or "not loaded"),
                },
                {
                    "name": "Backend latency",
                    "status": "pass" if float(runtime.get("backendLatency") or 0) < 1000 else "warn",
                    "detail": f"{float(runtime.get('backendLatency') or 0):.0f} ms",
                },
                {
                    "name": "Recent errors",
                    "status": "warn" if runtime.get("recentErrors") else "pass",
                    "detail": (
                        str(runtime.get("recentErrors")[-1])
                        if runtime.get("recentErrors")
                        else "none"
                    ),
                },
            ]
        )
        severity = "pass"
        if any(check["status"] == "fail" for check in checks):
            severity = "fail"
        elif any(check["status"] == "warn" for check in checks):
            severity = "warn"
        return {"status": severity, "checks": checks, "log": str(self.paths.log_file)}

    def _done(self, future: Future) -> None:
        try:
            result = future.result()
        except Exception as exc:
            result = {"status": "fail", "checks": [], "error": str(exc)}
        self.completed.emit(result)
