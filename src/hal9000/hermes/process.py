"""Owned Hermes backend process lifecycle."""

from __future__ import annotations

import logging
import os
import re
import secrets
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal, Slot


class HermesProcessManager(QObject):
    backendReady = Signal(str, str)
    outputReceived = Signal(str)
    errorOccurred = Signal(str)
    runningChanged = Signal(bool)

    READY_PATTERN = re.compile(r"HERMES_(?:BACKEND|DASHBOARD)_READY\s+port=(\d+)")

    def __init__(
        self,
        executable: Path,
        parent: QObject | None = None,
        *,
        profile: str = "",
    ) -> None:
        super().__init__(parent)
        self.executable = executable
        self.profile = profile.strip()
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.errorOccurred.connect(self._on_error)
        self._process.finished.connect(self._on_finished)
        self._token = ""
        self._announced = False
        self._output_buffer = ""
        self._stopping = False

    @property
    def owns_process(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    @property
    def token(self) -> str:
        return self._token

    @Slot()
    def start(self) -> None:
        if self.owns_process:
            return
        self._announced = False
        self._output_buffer = ""
        self._stopping = False
        self._token = secrets.token_urlsafe(32)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("HERMES_DASHBOARD_SESSION_TOKEN", self._token)
        environment.insert("HERMES_DESKTOP", "1")
        environment.insert("HERMES_PARENT_PID", str(os.getpid()))
        self._process.setProcessEnvironment(environment)
        self._process.setProgram(str(self.executable))
        arguments = ["serve", "--host", "127.0.0.1", "--port", "0", "--skip-build"]
        if self.profile:
            arguments[0:0] = ["--profile", self.profile]
        self._process.setArguments(arguments)
        self._process.start()
        self.runningChanged.emit(True)

    @Slot()
    def stop(self) -> None:
        if not self.owns_process:
            return
        self._stopping = True
        self._process.terminate()
        if not self._process.waitForFinished(3500):
            self._process.kill()
            self._process.waitForFinished(1500)

    def _read_output(self) -> None:
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self.outputReceived.emit(text.rstrip())
        if self._announced:
            return
        self._output_buffer = (self._output_buffer + text)[-4096:]
        match = self.READY_PATTERN.search(self._output_buffer)
        if not match:
            return
        self._announced = True
        port = int(match.group(1))
        base = f"http://127.0.0.1:{port}"
        socket = f"ws://127.0.0.1:{port}/api/ws?token={self._token}"
        self.backendReady.emit(base, socket)

    def _on_error(self, _error: QProcess.ProcessError) -> None:
        if self._stopping:
            return
        self.errorOccurred.emit(self._process.errorString())

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self.runningChanged.emit(False)
        if exit_code != 0 and not self._announced and not self._stopping:
            self.errorOccurred.emit(f"Hermes backend exited before ready ({exit_code})")
        self._stopping = False
