"""Application coordinator exposed to QML."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Property, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from hal9000.audio.coordinator import AudioCoordinator
from hal9000.audio.devices import AudioDeviceCatalog
from hal9000.audio.playback import AudioPlayback
from hal9000.autostart import set_launch_on_login
from hal9000.clicks import SpeakerClickAggregator
from hal9000.config import AppConfig, ConfigStore
from hal9000.diagnostics import DiagnosticsRunner
from hal9000.hermes.service import HermesService
from hal9000.models import ActivityModel, ApprovalModel, ConversationModel
from hal9000.paths import AppPaths
from hal9000.secrets import SecretStore
from hal9000.speech.stt import FasterWhisperService
from hal9000.speech.tts.manager import TtsManager
from hal9000.speech.wake_service import WakeWordService
from hal9000.state import HalState, HalStateMachine, InvalidTransition


class HalController(QObject):
    stateChanged = Signal(str)
    manualOpenChanged = Signal(bool)
    settingsOpenChanged = Signal(bool)
    fullscreenChanged = Signal(bool)
    micLevelChanged = Signal(float)
    speakerLevelChanged = Signal(float)
    subsystemChanged = Signal()
    integrationsChanged = Signal()
    configChanged = Signal()
    firstRunChanged = Signal(bool)
    diagnosticsChanged = Signal()
    focusInputRequested = Signal()
    fullscreenRequested = Signal(bool)
    notification = Signal(str)
    modelProgressChanged = Signal()
    credentialChanged = Signal()
    recentErrorsChanged = Signal()
    _cudaDetected = Signal(str)

    def __init__(
        self,
        paths: AppPaths,
        config_store: ConfigStore,
        config: AppConfig,
        cwd: Path,
        services_enabled: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.paths = paths
        self.config_store = config_store
        self.config = config
        self.state_machine = HalStateMachine(self)
        self.state_machine.stateChanged.connect(self.stateChanged)
        self.state_machine.manualOpenChanged.connect(self.manualOpenChanged)
        self.clicks = SpeakerClickAggregator(parent=self)
        self.clicks.doubleClick.connect(self.openManual)
        self.clicks.tripleClick.connect(self.closeManual)
        self.conversations = ConversationModel(self)
        self.activities = ActivityModel(self)
        self.approvals = ApprovalModel(self)
        self.audio_devices = AudioDeviceCatalog(self)
        self.secret_store = SecretStore()
        self._hermes_token = self.secret_store.get_hermes_token()
        self.hermes = HermesService(config.hermes, cwd, self._hermes_token, self)
        self.audio = AudioCoordinator(config.stt, self)
        self.wake = WakeWordService(
            config.wake.phrase,
            config.wake.sensitivity,
            paths.model_cache / "sherpa",
            self,
        )
        self.stt = FasterWhisperService(
            config.stt.model,
            config.stt.language,
            paths.model_cache / "faster-whisper",
            self,
        )
        self.tts = TtsManager(
            paths.model_cache,
            config.voice.mode,
            config.voice.speaking_rate,
            config.voice.selected_engine,
            self,
        )
        self.playback = AudioPlayback(
            config.voice.output_device,
            config.voice.volume,
            self,
        )
        self.diagnostics_runner = DiagnosticsRunner(paths, self)
        self._settings_open = False
        self._fullscreen = config.general.last_fullscreen
        self._mic_level = 0.0
        self._speaker_level = 0.0
        self._first_run = not config.general.setup_complete
        self._setup_in_progress = False
        self._diagnostics: dict[str, Any] = {}
        self._assistant_row = -1
        self._assistant_text = ""
        self._voice_conversation = False
        self._capture_origin = ""
        self._startup_complete = False
        self._services_enabled = services_enabled
        self._integrations: list[str] = []
        self._model_task = ""
        self._model_progress = 0.0
        self._recent_errors: list[str] = []
        self._cuda_status = "not probed"
        self._manual_idle_timer = QTimer(self)
        self._manual_idle_timer.setSingleShot(True)
        self._manual_idle_timer.timeout.connect(self._manual_idle_timeout)
        self._cudaDetected.connect(self._set_cuda_status)
        self._connect_signals()

    def _connect_signals(self) -> None:
        self.hermes.statusChanged.connect(self._hermes_status_changed)
        self.hermes.versionChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.hermes.latencyChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.hermes.integrationsChanged.connect(self._set_integrations)
        self.hermes.sessionChanged.connect(self._hermes_session_changed)
        self.hermes.assistantStarted.connect(self._assistant_started)
        self.hermes.assistantDelta.connect(self._assistant_delta)
        self.hermes.assistantCompleted.connect(self._assistant_completed)
        self.hermes.toolActivity.connect(self._tool_activity)
        self.hermes.approvalRequested.connect(self._approval_requested)
        self.hermes.errorOccurred.connect(self._error)
        self.audio.levelChanged.connect(self._set_mic_level)
        self.audio.wakeDetected.connect(self._wake_detected)
        self.audio.utteranceReady.connect(self._utterance_ready)
        self.audio.errorOccurred.connect(self._audio_error)
        self.audio.modeChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.wake.ready.connect(self._wake_ready)
        self.wake.statusChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.wake.progressChanged.connect(self._wake_progress)
        self.wake.errorOccurred.connect(self._wake_error)
        self.stt.statusChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.stt.backendChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.stt.transcriptionReady.connect(self._transcription_ready)
        self.stt.errorOccurred.connect(self._stt_error)
        self.tts.statusChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.tts.activeEngineChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.tts.engineStatusChanged.connect(self.subsystemChanged.emit)
        self.tts.progressChanged.connect(self._tts_progress)
        self.tts.synthesisReady.connect(self._synthesis_ready)
        self.tts.fallbackOccurred.connect(self._tts_fallback)
        self.tts.benchmarkCompleted.connect(self._benchmark_complete)
        self.tts.errorOccurred.connect(self._tts_error)
        self.playback.playingChanged.connect(self._playback_changed)
        self.playback.levelChanged.connect(self._set_speaker_level)
        self.playback.finished.connect(self._playback_finished)
        self.playback.errorOccurred.connect(self._playback_error)
        self.diagnostics_runner.completed.connect(self._diagnostics_complete)
        self.state_machine.transitionOccurred.connect(self._state_transitioned)

    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self.state_machine.state

    @Property(bool, constant=True)
    def servicesEnabled(self) -> bool:
        return self._services_enabled

    @Property(bool, notify=stateChanged)
    def active(self) -> bool:
        return self.state_machine.active

    @Property(bool, notify=manualOpenChanged)
    def manualOpen(self) -> bool:
        return self.state_machine.manualOpen

    @Property(bool, notify=settingsOpenChanged)
    def settingsOpen(self) -> bool:
        return self._settings_open

    @Property(bool, notify=fullscreenChanged)
    def fullscreen(self) -> bool:
        return self._fullscreen

    @Property(bool, notify=firstRunChanged)
    def firstRun(self) -> bool:
        return self._first_run

    @Property(bool, notify=firstRunChanged)
    def setupInProgress(self) -> bool:
        return self._setup_in_progress

    @Property(float, notify=micLevelChanged)
    def micLevel(self) -> float:
        return self._mic_level

    @Property(float, notify=speakerLevelChanged)
    def speakerLevel(self) -> float:
        return self._speaker_level

    @Property(bool, notify=subsystemChanged)
    def microphoneMuted(self) -> bool:
        return self.audio.muted

    @Property(str, notify=subsystemChanged)
    def hermesStatus(self) -> str:
        return self.hermes.status

    @Property(str, notify=subsystemChanged)
    def hermesVersion(self) -> str:
        return self.hermes.version

    @Property(str, notify=subsystemChanged)
    def hermesExecutable(self) -> str:
        return self.hermes.executable

    @Property(float, notify=subsystemChanged)
    def backendLatency(self) -> float:
        return self.hermes.latency

    @Property(str, notify=subsystemChanged)
    def wakeStatus(self) -> str:
        return self.wake.status

    @Property(str, notify=subsystemChanged)
    def microphoneStatus(self) -> str:
        return self.audio.mode

    @Property(str, notify=subsystemChanged)
    def sttStatus(self) -> str:
        return self.stt.status

    @Property(str, notify=subsystemChanged)
    def sttBackend(self) -> str:
        return self.stt.backend

    @Property(str, notify=subsystemChanged)
    def ttsStatus(self) -> str:
        return self.tts.status

    @Property(str, notify=subsystemChanged)
    def ttsEngine(self) -> str:
        return self.tts.activeEngine or "AUTO / XTTS PREFERRED"

    @Property(str, notify=subsystemChanged)
    def xttsStatus(self) -> str:
        return self.tts.xttsStatus

    @Property(str, notify=subsystemChanged)
    def piperStatus(self) -> str:
        return self.tts.piperStatus

    @Property(str, notify=subsystemChanged)
    def cudaStatus(self) -> str:
        return self._cuda_status

    @Property("QVariantMap", notify=configChanged)
    def settingsSnapshot(self) -> dict[str, Any]:
        return asdict(self.config)

    @Property("QVariantMap", notify=diagnosticsChanged)
    def diagnostics(self) -> dict[str, Any]:
        return dict(self._diagnostics)

    @Property("QStringList", notify=integrationsChanged)
    def integrations(self) -> list[str]:
        return list(self._integrations)

    @Property(str, notify=modelProgressChanged)
    def modelTask(self) -> str:
        return self._model_task

    @Property(float, notify=modelProgressChanged)
    def modelProgress(self) -> float:
        return self._model_progress

    @Property(bool, notify=credentialChanged)
    def hermesCredentialPresent(self) -> bool:
        return bool(self._hermes_token)

    @Property("QStringList", notify=recentErrorsChanged)
    def recentErrors(self) -> list[str]:
        return list(self._recent_errors)

    @Slot()
    def startup(self) -> None:
        if self._startup_complete:
            return
        self._startup_complete = True
        if not self._services_enabled:
            QTimer.singleShot(100, self._finish_boot)
            return
        self.audio_devices.refresh()
        threading.Thread(
            target=self._detect_cuda,
            daemon=True,
            name="hal9000-cuda-probe",
        ).start()
        self.hermes.start()
        if self.config.wake.enabled:
            self.wake.start()
        else:
            self.audio.start(self.config.stt.input_device or self.config.wake.input_device)
        QTimer.singleShot(450, self._finish_boot)
        if not self._first_run:
            QTimer.singleShot(800, self.tts.preload)

    @Slot()
    def shutdown(self) -> None:
        self.playback.stop()
        self.audio.stop()
        self.hermes.close()
        self.wake.close()
        self.stt.close()
        self.tts.close()
        self.diagnostics_runner.close()

    @Slot()
    def speakerClick(self) -> None:
        self.touchManual()
        self.clicks.registerClick()

    @Slot()
    def openManual(self) -> None:
        try:
            self.state_machine.enterManual()
        except InvalidTransition as exc:
            logging.getLogger("hal9000.controller").debug("Open manual deferred: %s", exc)
        self.touchManual()
        QTimer.singleShot(380, self.focusInputRequested.emit)

    @Slot()
    def closeManual(self) -> None:
        if self._has_pending_approval():
            self.notification.emit("Resolve the Hermes approval before closing the console")
            return
        self._voice_conversation = False
        self._manual_idle_timer.stop()
        if self.audio.mode == "record":
            self.audio.stopRecording()
        self.state_machine.leaveManual()

    @Slot()
    def openSettings(self) -> None:
        if self._settings_open:
            return
        self._settings_open = True
        self._manual_idle_timer.stop()
        self.settingsOpenChanged.emit(True)

    @Slot()
    def closeSettings(self) -> None:
        if not self._settings_open:
            return
        self._settings_open = False
        self.settingsOpenChanged.emit(False)
        self.touchManual()

    @Slot()
    def handleEscape(self) -> None:
        if self._settings_open:
            self.closeSettings()
        elif self.manualOpen:
            self.closeManual()

    @Slot(str)
    def sendText(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        self.touchManual()
        if not self.manualOpen and self._capture_origin != "wake":
            self.openManual()
        self._append_message("user", clean)
        self._assistant_row = -1
        self._assistant_text = ""
        self._transition(HalState.THINKING, "prompt submitted")
        self.hermes.sendPrompt(clean)

    @Slot()
    def toggleManualMic(self) -> None:
        self.touchManual()
        self.openManual()
        if self.audio.mode == "record":
            self.audio.stopRecording()
            return
        self._capture_origin = "manual"
        self._transition(HalState.LISTENING, "manual microphone")
        self.audio.startRecording()

    @Slot()
    def stopGeneration(self) -> None:
        self.hermes.cancel()
        self.state_machine.return_to_rest("generation stopped")

    @Slot()
    def stopSpeech(self) -> None:
        self.playback.stop()

    @Slot()
    def toggleMicrophoneMute(self) -> None:
        self.audio.toggleMute()
        self.subsystemChanged.emit()

    @Slot()
    def toggleFullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        self.config.general.last_fullscreen = self._fullscreen
        self._save_config()
        self.fullscreenChanged.emit(self._fullscreen)
        self.fullscreenRequested.emit(self._fullscreen)

    @Slot()
    def focusInput(self) -> None:
        if self.manualOpen:
            self.touchManual()
            self.focusInputRequested.emit()

    @Slot()
    def touchManual(self) -> None:
        timeout = self.config.general.standby_timeout_seconds
        if not self.manualOpen or timeout <= 0 or self._settings_open:
            self._manual_idle_timer.stop()
            return
        self._manual_idle_timer.start(timeout * 1000)

    @Slot()
    def beginSetup(self) -> None:
        if self._setup_in_progress:
            return
        self._setup_in_progress = True
        self.firstRunChanged.emit(self._first_run)
        self.wake.start()
        self.stt.warmup()
        self.tts.runBenchmark()

    @Slot()
    def useTypedMode(self) -> None:
        self.config.wake.enabled = False
        self._complete_first_run()
        self._save_config()
        self.openManual()

    @Slot()
    def runVoiceBenchmark(self) -> None:
        self.tts.runBenchmark()

    @Slot()
    def startHermes(self) -> None:
        self.hermes.start()

    @Slot()
    def stopHermes(self) -> None:
        self.hermes.stop()

    @Slot()
    def reconnectHermes(self) -> None:
        self.hermes.reconnect()

    @Slot(str)
    def setHermesToken(self, token: str) -> None:
        clean = token.strip()
        try:
            self.secret_store.set_hermes_token(clean)
        except Exception as exc:
            self._record_error(f"Credential store failed: {exc}")
            self.notification.emit("The Hermes token could not be saved to the desktop keyring")
            return
        self._hermes_token = clean
        self.hermes.set_token(clean)
        self.credentialChanged.emit()
        self.notification.emit("Hermes credential saved to the desktop keyring" if clean else "Hermes credential removed")
        if self.config.hermes.mode == "remote":
            self.hermes.apply_settings()

    @Slot(int, int, int, int)
    def saveWindowGeometry(self, x: int, y: int, width: int, height: int) -> None:
        if self._fullscreen:
            return
        self.config.general.window_x = int(x)
        self.config.general.window_y = int(y)
        self.config.general.window_width = max(600, int(width))
        self.config.general.window_height = max(800, int(height))
        self._save_config()

    @Slot(int)
    def applicationStateChanged(self, state: int) -> None:
        if not self._services_enabled:
            return
        if state == int(Qt.ApplicationState.ApplicationActive.value):
            if self.audio.mode in {"stopped", "error"} and self.config.wake.enabled:
                self.audio.start(self.config.stt.input_device or self.config.wake.input_device)
            if self.hermes.status in {"offline", "error", "unavailable"}:
                self.hermes.reconnect()
        elif self.audio.mode == "record":
            self.audio.stopRecording()

    @Slot()
    def testWakeDetector(self) -> None:
        if self.wake.status != "ready":
            self.wake.start()
            self.notification.emit("Preparing the Sherpa wake detector")
            return
        self.notification.emit(f"Wake detector armed for “{self.config.wake.phrase}”")

    @Slot()
    def testMicrophone(self) -> None:
        if self.audio.mode in {"stopped", "error"}:
            self.audio.start(self.config.stt.input_device or self.config.wake.input_device)
        self.notification.emit("Microphone meter is live")

    @Slot(str)
    def testVoice(self, engine: str) -> None:
        self.tts.speakWith(engine, "I'm sorry, I can't do that.")

    @Slot()
    def runDiagnostics(self) -> None:
        self._diagnostics = {"status": "running", "checks": []}
        self.diagnosticsChanged.emit()
        self.diagnostics_runner.run(
            {
                "hermesStatus": self.hermes.status,
                "microphoneStatus": self.audio.mode,
                "cuda": self.cudaStatus,
                "wakeStatus": self.wake.status,
                "sttStatus": self.stt.status,
                "sttBackend": self.stt.backend,
                "xttsStatus": self.tts.xttsStatus,
                "piperStatus": self.tts.piperStatus,
                "selectedTts": self.tts.activeEngine or self.config.voice.selected_engine,
                "backendLatency": self.hermes.latency,
                "recentErrors": list(self._recent_errors),
            }
        )

    @Slot(str, object)
    def updateSetting(self, dotted: str, value: object) -> None:
        sections = {
            "general": self.config.general,
            "hermes": self.config.hermes,
            "wake": self.config.wake,
            "stt": self.config.stt,
            "voice": self.config.voice,
            "appearance": self.config.appearance,
        }
        section_name, separator, field_name = dotted.partition(".")
        section = sections.get(section_name)
        if not separator or section is None or not hasattr(section, field_name):
            self.notification.emit(f"Unknown setting: {dotted}")
            return
        current = getattr(section, field_name)
        try:
            if isinstance(current, bool):
                converted = bool(value)
            elif isinstance(current, int) and not isinstance(current, bool):
                converted = int(value)
            elif isinstance(current, float):
                converted = float(value)
            else:
                converted = str(value)
        except (TypeError, ValueError):
            self.notification.emit(f"Invalid value for {dotted}")
            return
        setattr(section, field_name, converted)
        self.config.normalize()
        self._apply_runtime_setting(dotted)
        self._save_config()
        self.configChanged.emit()

    @Slot(str, str)
    def respondApproval(self, request_id: str, choice: str) -> None:
        self.touchManual()
        self.hermes.respondApproval(request_id, choice)
        for index, item in enumerate(self.approvals.snapshot()):
            if item.get("requestId") == request_id:
                self.approvals.update(index, {"resolved": True})
        self._transition(HalState.THINKING, "approval answered")

    @Slot(str)
    def revealPath(self, kind: str) -> None:
        path = self.paths.log_file if kind == "logs" else self.paths.config_file
        path.parent.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _finish_boot(self) -> None:
        if self.state_machine.current == HalState.BOOTING:
            self._transition(
                HalState.STANDBY if self.config.general.start_in_standby else HalState.MANUAL,
                "interface ready",
            )
            if not self.config.general.start_in_standby:
                self.state_machine.enterManual()

    def _wake_ready(self, engine: object) -> None:
        self.audio.set_wake_engine(engine)
        self.audio.start(self.config.wake.input_device or self.config.stt.input_device)
        self.subsystemChanged.emit()

    def _wake_detected(self) -> None:
        if self.audio.muted or self.state_machine.current not in {HalState.STANDBY, HalState.MANUAL}:
            return
        self._voice_conversation = True
        self._capture_origin = "wake"
        self._transition(HalState.WAKE_DETECTED, "hey hal")
        QTimer.singleShot(180, self._begin_wake_recording)

    def _begin_wake_recording(self) -> None:
        self._transition(HalState.LISTENING, "capture utterance")
        self.audio.startRecording()

    def _utterance_ready(self, samples: object) -> None:
        try:
            size = len(samples)
        except TypeError:
            size = 0
        if size == 0:
            self._voice_conversation = False
            self.state_machine.return_to_rest("no speech detected")
            return
        self._transition(HalState.TRANSCRIBING, "utterance captured")
        self.stt.transcribe(samples)

    def _transcription_ready(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            self._voice_conversation = False
            self.state_machine.return_to_rest("empty transcription")
            return
        self.sendText(clean)

    def _assistant_started(self, _message_id: str) -> None:
        self._assistant_text = ""
        self._assistant_row = self._append_message("assistant", "", streaming=True)

    def _assistant_delta(self, delta: str) -> None:
        if self._assistant_row < 0:
            self._assistant_started("")
        self._assistant_text += delta
        self.conversations.update(
            self._assistant_row,
            {"text": self._assistant_text, "streaming": True},
        )

    def _assistant_completed(self, text: str) -> None:
        final = text.strip() or self._assistant_text.strip()
        if self._assistant_row < 0:
            self._assistant_row = self._append_message("assistant", final)
        else:
            self.conversations.update(
                self._assistant_row,
                {"text": final, "streaming": False},
            )
        self._assistant_text = final
        if final:
            self._transition(HalState.SPEAKING, "response ready")
            self.tts.speak(final)
        else:
            self.state_machine.return_to_rest("empty response")

    def _tool_activity(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "tool.progress")
        name = str(
            event.get("tool_name")
            or event.get("name")
            or event.get("display_name")
            or "Hermes tool"
        )
        activity_id = str(
            event.get("tool_id") or event.get("tool_call_id") or event.get("id") or name
        )
        status = "running"
        if event_type == "tool.complete":
            status = "complete"
        elif event.get("error"):
            status = "error"
        label = self._tool_label(name)
        detail = str(
            event.get("summary")
            or event.get("context")
            or event.get("status")
            or event.get("message")
            or ""
        )[:180]
        self.activities.upsert(
            activity_id,
            {"label": label, "detail": detail, "status": status, "kind": name},
        )
        if status == "running":
            self._transition(HalState.TOOL_RUNNING, f"Hermes tool {name}")
        elif self.state_machine.current == HalState.TOOL_RUNNING:
            self._transition(HalState.THINKING, "tool complete")

    def _approval_requested(self, event: dict[str, Any]) -> None:
        request_id = str(event.get("request_id") or event.get("id") or "")
        self.approvals.append(
            {
                "requestId": request_id,
                "title": str(event.get("title") or "Hermes approval required"),
                "detail": str(event.get("message") or event.get("description") or "Review this operation"),
                "risk": str(event.get("risk") or event.get("type") or "approval"),
                "resolved": False,
            }
        )
        if not self.manualOpen:
            self.state_machine.enterManual()
        self._transition(HalState.WAITING_APPROVAL, "Hermes safeguard")

    def _synthesis_ready(self, audio: object) -> None:
        self.audio.setSpeaking(True)
        self.playback.play(audio)

    def _playback_changed(self, playing: bool) -> None:
        if playing and self.state_machine.current != HalState.SPEAKING:
            self._transition(HalState.SPEAKING, "audio playback")

    def _playback_finished(self) -> None:
        self.audio.setSpeaking(False)
        if self._voice_conversation and not self.manualOpen:
            QTimer.singleShot(650, self._continue_voice_conversation)
        else:
            self.state_machine.return_to_rest("speech complete")
            self.touchManual()

    def _continue_voice_conversation(self) -> None:
        if not self._voice_conversation or self.manualOpen:
            self.state_machine.return_to_rest("conversation ended")
            return
        self._capture_origin = "wake"
        self._transition(HalState.LISTENING, "conversational follow-up")
        self.audio.startRecording()

    def _benchmark_complete(self, results: dict, selected: str, reason: str) -> None:
        self.config.voice.benchmark_results = results
        self.config.voice.auto_benchmark_complete = True
        self.config.voice.selected_engine = selected
        self.config.voice.last_fallback_reason = "" if selected == "XTTS" else reason
        self._save_config()
        self.configChanged.emit()
        self.notification.emit(f"Auto selected {selected or 'no engine'}: {reason}")
        if self._setup_in_progress:
            self._complete_first_run()

    def _tts_fallback(self, reason: str) -> None:
        self.config.voice.last_fallback_reason = reason
        self._save_config()
        self.subsystemChanged.emit()

    def _error(self, message: str) -> None:
        self._record_error("Hermes: " + message)
        self._append_message("system", message, error=True)
        self._transition(HalState.ERROR, "Hermes error")
        self.notification.emit(message)

    def _audio_error(self, message: str) -> None:
        self._record_error(message)
        self.notification.emit(message + " — typed mode remains available")
        self.subsystemChanged.emit()

    def _wake_error(self, message: str) -> None:
        self._record_error("Wake detector: " + message)
        self.notification.emit("Wake detector unavailable: " + message)
        self.subsystemChanged.emit()

    def _stt_error(self, message: str) -> None:
        self._record_error("Speech recognition: " + message)
        self.notification.emit("Speech recognition failed: " + message)
        self.state_machine.return_to_rest("STT failure")

    def _tts_error(self, message: str) -> None:
        self._record_error("Speech output: " + message)
        self.notification.emit("Speech output unavailable: " + message)
        self.audio.setSpeaking(False)
        self.state_machine.return_to_rest("TTS failure; text retained")
        if self._setup_in_progress:
            self._complete_first_run()

    def _playback_error(self, message: str) -> None:
        self._record_error("Audio output: " + message)
        self.notification.emit("Audio output failed: " + message)

    def _set_mic_level(self, value: float) -> None:
        self._mic_level = value
        self.micLevelChanged.emit(value)

    def _set_speaker_level(self, value: float) -> None:
        self._speaker_level = value
        self.speakerLevelChanged.emit(value)

    def _diagnostics_complete(self, report: dict) -> None:
        self._diagnostics = report
        self.diagnosticsChanged.emit()

    def _set_integrations(self, integrations: list[str]) -> None:
        self._integrations = list(integrations)
        self.integrationsChanged.emit()

    def _detect_cuda(self) -> None:
        status = "CPU"
        try:
            import torch

            if torch.cuda.is_available():
                status = f"CUDA {torch.version.cuda} / {torch.cuda.get_device_name(0)}"
        except Exception as exc:
            logging.getLogger("hal9000.cuda").warning("CUDA probe failed: %s", exc)
        self._cudaDetected.emit(status)

    def _set_cuda_status(self, status: str) -> None:
        self._cuda_status = status
        self.subsystemChanged.emit()

    def _hermes_status_changed(self, status: str) -> None:
        self.subsystemChanged.emit()
        if status == "connected" and self.state_machine.current == HalState.ERROR:
            self.state_machine.return_to_rest("Hermes reconnected")

    def _hermes_session_changed(self, _session_id: str) -> None:
        self._save_config()

    def _wake_progress(self, fraction: float) -> None:
        self._model_task = "Preparing Sherpa wake detector"
        self._model_progress = min(1.0, max(0.0, float(fraction)))
        self.modelProgressChanged.emit()

    def _tts_progress(self, label: str, fraction: float) -> None:
        self._model_task = label
        self._model_progress = min(1.0, max(0.0, float(fraction)))
        self.modelProgressChanged.emit()

    def _state_transitioned(self, _previous: str, target: str, _reason: str) -> None:
        if target == HalState.MANUAL.value:
            self.touchManual()
        elif target not in {HalState.WAITING_APPROVAL.value}:
            self._manual_idle_timer.stop()

    def _manual_idle_timeout(self) -> None:
        if (
            self.manualOpen
            and self.state_machine.current == HalState.MANUAL
            and not self._settings_open
            and not self._has_pending_approval()
        ):
            self.closeManual()

    def _record_error(self, message: str) -> None:
        clean = message.strip()
        if not clean:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self._recent_errors.append(f"{stamp}  {clean}")
        self._recent_errors = self._recent_errors[-12:]
        logging.getLogger("hal9000.controller").error("%s", clean)
        self.recentErrorsChanged.emit()

    def _transition(self, target: HalState, reason: str) -> None:
        try:
            self.state_machine.transition(target, reason)
        except InvalidTransition:
            logging.getLogger("hal9000.controller").warning(
                "Rejected transition %s -> %s (%s)",
                self.state_machine.current.name,
                target.name,
                reason,
            )

    def _append_message(
        self,
        role: str,
        text: str,
        streaming: bool = False,
        error: bool = False,
    ) -> int:
        return self.conversations.append(
            {
                "role": role,
                "text": text,
                "streaming": streaming,
                "error": error,
                "timestamp": datetime.now().strftime("%H:%M"),
            }
        )

    def _has_pending_approval(self) -> bool:
        return any(not item.get("resolved") for item in self.approvals.snapshot())

    @staticmethod
    def _tool_label(name: str) -> str:
        lowered = name.lower()
        if "codex" in lowered:
            return "USING CODEX"
        if any(token in lowered for token in ("terminal", "shell", "command", "exec")):
            return "RUNNING COMMAND"
        if any(token in lowered for token in ("file", "read", "write")):
            return "READING FILE"
        if any(token in lowered for token in ("browser", "search", "web")):
            return "SEARCHING"
        if "system" in lowered:
            return "INSPECTING SYSTEM"
        return name.replace("_", " ").upper()

    def _complete_first_run(self) -> None:
        if not self._first_run:
            return
        self._first_run = False
        self._setup_in_progress = False
        self.config.general.setup_complete = True
        self._save_config()
        self.firstRunChanged.emit(False)

    def _apply_runtime_setting(self, dotted: str) -> None:
        if dotted == "general.launch_on_login":
            try:
                set_launch_on_login(self.config.general.launch_on_login)
            except OSError as exc:
                self._record_error(f"Autostart configuration failed: {exc}")
                self.notification.emit("Could not update login autostart")
        elif dotted == "general.standby_timeout_seconds":
            self.touchManual()
        elif dotted in {"hermes.mode", "hermes.backend_url", "hermes.profile", "hermes.auto_start"}:
            self.hermes.apply_settings()
        elif dotted == "voice.mode":
            self.tts.set_mode(self.config.voice.mode)
        elif dotted == "voice.volume":
            self.playback.volume = self.config.voice.volume
        elif dotted == "voice.output_device":
            self.playback.device = self.config.voice.output_device
        elif dotted == "voice.speaking_rate":
            self.tts.rate = self.config.voice.speaking_rate
        elif dotted in {"wake.phrase", "wake.sensitivity"}:
            self.audio.set_wake_engine(None)
            self.wake.configure(self.config.wake.phrase, self.config.wake.sensitivity)
        elif dotted == "wake.enabled":
            if self.config.wake.enabled:
                self.wake.configure(self.config.wake.phrase, self.config.wake.sensitivity)
            else:
                self.audio.set_wake_engine(None)
        elif dotted in {"wake.input_device", "stt.input_device"}:
            if self._services_enabled:
                self.audio.restart(self.config.stt.input_device or self.config.wake.input_device)
        elif dotted in {"stt.model", "stt.language"}:
            self.stt.configure(self.config.stt.model, self.config.stt.language)

    def _save_config(self) -> None:
        try:
            self.config_store.save(self.config)
        except OSError as exc:
            self.notification.emit(f"Could not save settings: {exc}")
