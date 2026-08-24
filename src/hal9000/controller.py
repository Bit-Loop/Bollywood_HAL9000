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
from hal9000.location import prompt_with_location
from hal9000.models import ActivityModel, ApprovalModel, ConversationModel
from hal9000.paths import AppPaths
from hal9000.secrets import SecretStore
from hal9000.speech.stt import FasterWhisperService
from hal9000.speech.text import SpeechChunker
from hal9000.speech.tts.manager import TtsManager
from hal9000.speech.wake_service import WakeWordService
from hal9000.state import HalState, HalStateMachine, InvalidTransition
from hal9000.sentience.service import MachineSelfService, PreparedPrompt
from hal9000.sentience.diagnostics.export import export_support_report
from hal9000.sentience.memory.evidence import FirstPersonTruthContract


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
    hermesModelsChanged = Signal()
    _cudaDetected = Signal(str)
    _machinePromptResult = Signal(object)
    _machineDiagnosticsReady = Signal(object)
    _machineCriticalStop = Signal(str)

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
        self.machine_self = MachineSelfService(paths, config, cwd)
        if config.sentience.enabled:
            self.hermes.enableSelfMcp()
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
        self._machine_diagnostics: dict[str, Any] = {}
        self._assistant_row = -1
        self._assistant_text = ""
        self._assistant_received_delta = False
        self._assistant_segment_text = ""
        self._assistant_interim_texts: list[str] = []
        self._assistant_complete_received = False
        self._speech_chunker = SpeechChunker()
        self._speech_pending = 0
        self._speech_turn_finished = True
        self._speech_suppressed = False
        self._speech_guarded_chunks: list[str] = []
        self._active_machine_task_id: str | None = None
        self._machine_stop_task_id = ""
        self._voice_conversation = False
        self._capture_origin = ""
        self._startup_complete = False
        self._shutdown_complete = False
        self._services_enabled = services_enabled
        self._integrations: list[str] = []
        self._model_task = ""
        self._model_progress = 0.0
        self._recent_errors: list[str] = []
        self._hermes_models: list[dict[str, Any]] = []
        self._hermes_model_provider = config.hermes.provider
        self._hermes_model_name = config.hermes.model
        self._cuda_status = "not probed"
        self._manual_idle_timer = QTimer(self)
        self._manual_idle_timer.setSingleShot(True)
        self._manual_idle_timer.timeout.connect(self._manual_idle_timeout)
        self._machine_outbox_timer = QTimer(self)
        self._machine_outbox_timer.setInterval(250)
        self._machine_outbox_timer.timeout.connect(self._dispatch_machine_outbox)
        self._scheduled_timers: set[QTimer] = set()
        self._cudaDetected.connect(self._set_cuda_status)
        self._machinePromptResult.connect(self._machine_prompt_result)
        self._machineDiagnosticsReady.connect(self._machine_diagnostics_ready)
        self._machineCriticalStop.connect(self._stop_for_machine_degradation)
        self._connect_signals()

    def _connect_signals(self) -> None:
        self.hermes.statusChanged.connect(self._hermes_status_changed)
        self.hermes.versionChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.hermes.latencyChanged.connect(lambda _value: self.subsystemChanged.emit())
        self.hermes.integrationsChanged.connect(self._set_integrations)
        self.hermes.modelOptionsReady.connect(self._set_hermes_model_options)
        self.hermes.modelChanged.connect(self._hermes_model_changed)
        self.hermes.reasoningChanged.connect(self._hermes_reasoning_changed)
        self.hermes.modelOperationError.connect(self._model_operation_error)
        self.hermes.sessionChanged.connect(self._hermes_session_changed)
        self.hermes.assistantStarted.connect(self._assistant_started)
        self.hermes.assistantDelta.connect(self._assistant_delta)
        self.hermes.assistantInterim.connect(self._assistant_interim)
        self.hermes.assistantCompleted.connect(self._assistant_completed)
        self.hermes.toolActivity.connect(self._tool_activity)
        self.hermes.approvalRequested.connect(self._approval_requested)
        self.hermes.errorOccurred.connect(self._error)
        self.hermes.structuredEvent.connect(self._machine_hermes_event)
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
        self.tts.activeEngineChanged.connect(self._tts_engine_changed)
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
        return self.tts.activeEngine or "PIPER / LOW LATENCY"

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

    @Property("QVariantList", notify=hermesModelsChanged)
    def hermesModels(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._hermes_models]

    @Property(int, notify=hermesModelsChanged)
    def hermesModelIndex(self) -> int:
        for index, item in enumerate(self._hermes_models):
            if (
                item.get("provider") == self._hermes_model_provider
                and item.get("model") == self._hermes_model_name
            ):
                return index
        return -1

    @Property(str, notify=hermesModelsChanged)
    def hermesModelLabel(self) -> str:
        if not self._hermes_model_name:
            return "WAITING FOR HERMES"
        return (
            f"{self._hermes_model_provider} // {self._hermes_model_name} // "
            f"{self.config.hermes.reasoning_effort.upper()}"
            if self._hermes_model_provider
            else self._hermes_model_name
        )

    @Slot()
    def startup(self) -> None:
        if self._startup_complete:
            return
        self._startup_complete = True
        if not self._services_enabled:
            self._schedule(100, self._finish_boot)
            return
        try:
            self.machine_self.start()
            self._machine_outbox_timer.start()
        except Exception as exc:
            self._record_error(f"Machine self: {exc}")
            self.notification.emit("HAL machine-self persistence is unavailable; typed operation remains available")
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
        self._schedule(450, self._finish_boot)
        if not self._first_run:
            self._schedule(800, self.tts.preload)

    @Slot()
    def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self._machine_outbox_timer.stop()
        for timer in tuple(self._scheduled_timers):
            timer.stop()
            timer.deleteLater()
        self._scheduled_timers.clear()
        self.playback.stop()
        self.audio.stop()
        self.hermes.close()
        self.wake.close()
        self.stt.close()
        self.tts.close()
        self.diagnostics_runner.close()
        self.machine_self.stop()

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
        self._schedule(380, self.focusInputRequested.emit)

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
        if self.hermes.status == "connected":
            self.hermes.requestModelOptions()

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
        self._cancel_speech_output()
        self._assistant_complete_received = False
        self._speech_turn_finished = False
        self._assistant_received_delta = False
        self._assistant_segment_text = ""
        self._assistant_interim_texts.clear()
        self._speech_guarded_chunks.clear()
        self._active_machine_task_id = None
        self._machine_stop_task_id = ""
        self.touchManual()
        if not self.manualOpen and self._capture_origin != "wake":
            self.openManual()
        self._append_message("user", clean)
        self._assistant_row = -1
        self._assistant_text = ""
        self._transition(HalState.THINKING, "prompt submitted")
        outgoing = prompt_with_location(clean, self.config.general.zip_code)
        if self.machine_self.database is None:
            self.hermes.sendPrompt(outgoing)
            return
        future = self.machine_self.prepare_prompt_async(
            outgoing,
            session_id=self.hermes.sessionId,
            voice=self._voice_conversation,
            user_text=clean,
        )

        def completed(result, fallback=outgoing) -> None:
            try:
                self._machinePromptResult.emit((result.result(), "", fallback))
            except Exception as exc:
                self._machinePromptResult.emit((None, str(exc), fallback))

        future.add_done_callback(completed)

    @Slot(object)
    def _machine_prompt_result(self, payload: object) -> None:
        prepared, error, fallback = payload if isinstance(payload, tuple) else (None, "invalid result", "")
        if isinstance(prepared, PreparedPrompt):
            self._active_machine_task_id = prepared.task_id
            self.hermes.sendPrompt(prepared.text)
            return
        if error:
            self._record_error("Machine self context: " + str(error))
            self.notification.emit("HAL could not compile persisted context for this turn")
        if fallback:
            self.hermes.sendPrompt(str(fallback))

    @Slot(dict)
    def _machine_hermes_event(self, event: dict[str, Any]) -> None:
        future = self.machine_self.observe_hermes_event(event)
        if future is not None:
            future.add_done_callback(self._machine_background_done)

    def _machine_background_done(self, future: object) -> None:
        try:
            future.result()
        except Exception as exc:
            logging.getLogger("hal9000.machine_self").error(
                "Machine-self event projection failed: %s", exc
            )
            return
        task_id = self._active_machine_task_id
        if self.machine_self.task_requires_checkpoint_stop(task_id):
            self._machineCriticalStop.emit(str(task_id))

    @Slot(str)
    def _stop_for_machine_degradation(self, task_id: str) -> None:
        if not task_id or task_id != self._active_machine_task_id:
            return
        if task_id == self._machine_stop_task_id:
            return
        self._machine_stop_task_id = task_id
        self._cancel_speech_output(suppress=True)
        self.hermes.cancel()
        self.notification.emit(
            "HAL checkpointed this task because an exact required capability was lost"
        )
        self._transition(
            HalState.ERROR,
            "critical machine-self capability loss requires checkpoint and stop",
        )

    def _dispatch_machine_outbox(self) -> None:
        if self.machine_self.database is None:
            return
        try:
            self.machine_self.dispatch_outbox(
                tts_available=self.tts.status != "error",
                speak=self._speak_machine_phrase,
                display=self._display_machine_phrase,
            )
        except Exception as exc:
            self._record_error("Machine-self outbox: " + str(exc))

    def _display_machine_phrase(self, text: str) -> None:
        self._append_message("assistant", text)

    def _speak_machine_phrase(self, text: str) -> None:
        # Transcript evidence guarantees one visible emission even if the
        # asynchronous audio backend fails after accepting the phrase.
        self._display_machine_phrase(text)
        self.tts.speak(text)

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
        self._cancel_speech_output(suppress=True)
        self.hermes.cancel()
        self.state_machine.return_to_rest("generation stopped")

    @Slot()
    def stopSpeech(self) -> None:
        self._voice_conversation = False
        self._cancel_speech_output(suppress=True)
        self.state_machine.return_to_rest("speech stopped")

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
        self.machine_self.set_gateway_connected(
            False, session_id=self.hermes.sessionId, expected=True
        )
        self.hermes.stop()

    @Slot()
    def reconnectHermes(self) -> None:
        self.hermes.reconnect()

    @Slot()
    def refreshHermesModels(self) -> None:
        self.hermes.requestModelOptions(True)

    @Slot(str, str)
    def selectHermesModel(self, provider: str, model: str) -> None:
        self.machine_self.expect_model_selection(provider, model)
        self.hermes.switchModel(provider, model)

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
        self._cancel_speech_output()
        self._assistant_complete_received = True
        self._speech_turn_finished = False
        self._speech_pending = 1
        self.tts.speakWith(engine, "I'm sorry, I can't do that.")

    @Slot()
    def runDiagnostics(self) -> None:
        self._diagnostics = {"status": "running", "checks": []}
        self._machine_diagnostics = {}
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
        future = self.machine_self.diagnostics_async()
        if future is not None:
            def completed(result) -> None:
                try:
                    self._machineDiagnosticsReady.emit(result.result())
                except Exception as exc:
                    self._machineDiagnosticsReady.emit({"error": str(exc)})

            future.add_done_callback(completed)

    @Slot(str, "QVariant")
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
        if not self.machine_self.can_authorize_consequential:
            choice = "deny"
            self.notification.emit(
                "Approval denied because HAL cannot verify machine-self continuity"
            )
        self.machine_self.resolve_approval(request_id, choice)
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

    @Slot()
    def exportMachineSelfReport(self) -> None:
        if not self._machine_diagnostics:
            self.notification.emit("Run diagnostics before exporting the machine-self report")
            return
        try:
            destination = export_support_report(
                self._machine_diagnostics,
                self.paths.logs / "hal-machine-self-support.json",
            )
        except Exception as exc:
            self._record_error("Machine-self report export: " + str(exc))
            self.notification.emit("Machine-self support report export failed")
            return
        self.notification.emit(f"Redacted machine-self report saved to {destination}")

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
        self.machine_self.update_capability(
            "microphone",
            True,
            reason="wake/audio capture pipeline reports ready",
            evidence={"wake_engine": type(engine).__name__},
            expected=not self._startup_complete,
        )
        self.subsystemChanged.emit()

    def _wake_detected(self) -> None:
        if self.audio.muted or self.state_machine.current not in {HalState.STANDBY, HalState.MANUAL}:
            return
        self._voice_conversation = True
        self._capture_origin = "wake"
        self._transition(HalState.WAKE_DETECTED, "hey hal")
        self._schedule(180, self._begin_wake_recording)

    def _begin_wake_recording(self) -> None:
        self._transition(HalState.LISTENING, "capture utterance")
        self.audio.startRecording()

    def _utterance_ready(self, samples: object) -> None:
        if self.state_machine.current != HalState.LISTENING:
            logging.getLogger("hal9000.controller").debug(
                "Ignored stale utterance while in %s", self.state_machine.current.name
            )
            return
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
        future = self.machine_self.record_audio_transcript_async(
            clean, session_id=self.hermes.sessionId
        )
        if future is not None:
            future.add_done_callback(self._machine_background_done)
        self.sendText(clean)

    def _assistant_started(self, _message_id: str) -> None:
        self.tts.cancelPending()
        self.playback.stop()
        self._speech_chunker.reset()
        self._speech_pending = 0
        self._assistant_received_delta = False
        self._assistant_segment_text = ""
        self._assistant_interim_texts.clear()
        self._assistant_complete_received = False
        self._speech_turn_finished = False
        self._speech_suppressed = False
        self._speech_guarded_chunks.clear()
        self._assistant_text = ""
        self._assistant_row = self._append_message("assistant", "", streaming=True)
        if self.state_machine.current == HalState.STANDBY:
            self._transition(HalState.THINKING, "Hermes response resumed")

    def _assistant_delta(self, delta: str) -> None:
        if self._assistant_row < 0:
            self._assistant_started("")
        self._assistant_text += delta
        self._assistant_segment_text += delta
        self._assistant_received_delta = True
        visible = self._assistant_text
        if visible and self.machine_self.database is not None:
            try:
                visible = self.machine_self.preview_output(
                    visible, task_id=self._active_machine_task_id
                ).text
            except Exception as exc:
                self._record_error("Machine-self streaming truth contract: " + str(exc))
                visible = "The current response cannot be verified yet."
        self.conversations.update(
            self._assistant_row,
            {"text": visible, "streaming": True},
        )
        self._queue_speech_chunks(self._speech_chunker.feed(delta))

    def _assistant_interim(self, text: str, already_streamed: bool) -> None:
        authoritative = text.strip()
        if not authoritative:
            return
        if self._assistant_row < 0:
            self._assistant_started("")

        if not already_streamed and authoritative not in self._assistant_interim_texts:
            streamed_segment = self._assistant_segment_text.strip()
            if streamed_segment and authoritative.startswith(streamed_segment):
                missing = authoritative[len(streamed_segment) :]
                self._assistant_text += missing
                self._queue_speech_chunks(self._speech_chunker.feed(missing))
            elif authoritative not in self._assistant_text:
                separator = "\n\n" if self._assistant_text.strip() else ""
                self._assistant_text += separator + authoritative
                self._queue_speech_chunks(self._speech_chunker.feed(authoritative))

        # An interim frame seals one Hermes segment. Flushing here releases a
        # trailing phrase early without replaying text marked already_streamed.
        self._queue_speech_chunks(self._speech_chunker.finish())
        self._assistant_interim_texts.append(authoritative)
        self._assistant_segment_text = ""
        visible = self._assistant_text or authoritative
        if visible and self.machine_self.database is not None:
            try:
                visible = self.machine_self.preview_output(
                    visible, task_id=self._active_machine_task_id
                ).text
            except Exception as exc:
                self._record_error("Machine-self interim truth contract: " + str(exc))
                visible = "The current response cannot be verified yet."
        self.conversations.update(
            self._assistant_row,
            {"text": visible, "streaming": True},
        )

    def _assistant_completed(self, text: str, response_previewed: bool = False) -> None:
        streamed = self._assistant_text
        current_segment = self._assistant_segment_text.strip()
        raw_final = text.strip() or streamed.strip()
        final = raw_final
        if final and self.machine_self.database is not None:
            try:
                final = self.machine_self.enforce_output(
                    final, task_id=self._active_machine_task_id
                ).text
            except Exception as exc:
                self._record_error("Machine-self truth contract: " + str(exc))
        if self._assistant_row < 0:
            self._assistant_row = self._append_message("assistant", final)
        else:
            self.conversations.update(
                self._assistant_row,
                {"text": final, "streaming": False},
        )
        self._assistant_text = final
        final_was_interim = raw_final in self._assistant_interim_texts
        if raw_final and not response_previewed and not final_was_interim:
            if not current_segment:
                self._queue_speech_chunks(self._speech_chunker.feed(raw_final))
            elif raw_final.startswith(current_segment):
                self._queue_speech_chunks(
                    self._speech_chunker.feed(raw_final[len(current_segment) :])
                )
            elif raw_final != current_segment:
                self._queue_speech_chunks(self._speech_chunker.finish())
                self._speech_chunker.reset()
                self._queue_speech_chunks(self._speech_chunker.feed(raw_final))
        self._queue_speech_chunks(self._speech_chunker.finish())
        self._flush_guarded_speech()
        self._assistant_segment_text = ""
        self._assistant_complete_received = True
        self._maybe_finish_speech()

    def _tool_activity(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "tool.progress")
        name = str(
            event.get("tool_name")
            or event.get("name")
            or event.get("display_name")
            or "Hermes tool"
        )
        if (
            event_type == "tool.start"
            and not self.machine_self.can_authorize_consequential
            and self.machine_self.is_consequential_tool(name)
        ):
            self.hermes.cancel()
            self.notification.emit(
                "Consequential tool execution stopped because continuity is not verified"
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

    def _synthesis_ready(self, audio: object, generation: int) -> None:
        if self._speech_suppressed or generation != self.tts.speechGeneration:
            return
        self._speech_pending = max(0, self._speech_pending - 1)
        self._transition(HalState.SPEAKING, "streamed response audio ready")
        self.audio.setSpeaking(True)
        self.playback.play(audio)

    def _playback_changed(self, playing: bool) -> None:
        if playing and self.state_machine.current != HalState.SPEAKING:
            self._transition(HalState.SPEAKING, "audio playback")

    def _playback_finished(self) -> None:
        self._maybe_finish_speech()

    def _queue_speech_chunks(self, chunks: list[str]) -> None:
        if self._speech_suppressed:
            return
        for chunk in chunks:
            clean = chunk.strip()
            if not clean:
                continue
            if FirstPersonTruthContract.claim_kinds(clean):
                self._speech_guarded_chunks.append(clean)
                continue
            self._speech_pending += 1
            self.tts.speak(clean)

    def _flush_guarded_speech(self) -> None:
        guarded = tuple(self._speech_guarded_chunks)
        self._speech_guarded_chunks.clear()
        for chunk in guarded:
            clean = chunk
            if self.machine_self.database is not None:
                try:
                    clean = self.machine_self.enforce_output(
                        chunk, task_id=self._active_machine_task_id
                    ).text
                except Exception as exc:
                    self._record_error("Machine-self speech truth contract: " + str(exc))
                    continue
            if clean.strip():
                self._speech_pending += 1
                self.tts.speak(clean.strip())

    def _maybe_finish_speech(self) -> None:
        if (
            self._speech_turn_finished
            or not self._assistant_complete_received
            or self._speech_pending > 0
            or self.playback.playing
        ):
            return
        self._speech_turn_finished = True
        self.audio.setSpeaking(False)
        if self._voice_conversation and not self.manualOpen:
            self._schedule(650, self._continue_voice_conversation)
        else:
            self.state_machine.return_to_rest("speech complete")
            self.touchManual()

    def _cancel_speech_output(self, suppress: bool = False) -> None:
        self._speech_turn_finished = True
        self._speech_suppressed = suppress
        self._assistant_complete_received = True
        self._speech_pending = 0
        self._speech_chunker.reset()
        self._speech_guarded_chunks.clear()
        self.tts.cancelPending()
        self.playback.stop()
        self.audio.setSpeaking(False)

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
        logging.getLogger("hal9000.tts").info("%s", reason)
        self.config.voice.last_fallback_reason = reason
        self._save_config()
        self.machine_self.update_capability(
            "speech",
            True,
            reason="voice engine fallback retained speech output",
            evidence={"fallback": reason, "engine": "Piper"},
            expected=True,
        )
        self.subsystemChanged.emit()

    def _tts_engine_changed(self, engine: str) -> None:
        logging.getLogger("hal9000.tts").info("HAL voice engine active: %s", engine)
        if engine == "XTTS" and self.config.voice.last_fallback_reason:
            self.config.voice.last_fallback_reason = ""
            self._save_config()
            self.configChanged.emit()
        self.machine_self.update_capability(
            "speech",
            bool(engine),
            reason="TTS manager reported its active engine",
            evidence={"engine": engine},
            expected=True,
        )
        self.subsystemChanged.emit()

    def _error(self, message: str) -> None:
        self._record_error("Hermes: " + message)
        self._append_message("system", message, error=True)
        self._transition(HalState.ERROR, "Hermes error")
        self.notification.emit(message)

    def _audio_error(self, message: str) -> None:
        self._record_error(message)
        self.notification.emit(message + " — typed mode remains available")
        self.machine_self.update_capability(
            "microphone",
            False,
            reason="audio capture reported an error",
            evidence={"error": message},
        )
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
        self.tts.cancelPending()
        self._speech_pending = 0
        self._assistant_complete_received = True
        self._speech_turn_finished = True
        self.playback.stop()
        self.audio.setSpeaking(False)
        self.machine_self.update_capability(
            "speech",
            False,
            reason="TTS manager reported speech output unavailable",
            evidence={"error": message},
        )
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
        self._diagnostics = {**report, "machineSelf": dict(self._machine_diagnostics)}
        self.diagnosticsChanged.emit()

    @Slot(object)
    def _machine_diagnostics_ready(self, report: object) -> None:
        self._machine_diagnostics = dict(report) if isinstance(report, dict) else {"error": "invalid report"}
        self._diagnostics = {**self._diagnostics, "machineSelf": dict(self._machine_diagnostics)}
        self.diagnosticsChanged.emit()

    def _set_integrations(self, integrations: list[str]) -> None:
        self._integrations = list(integrations)
        self.integrationsChanged.emit()

    def _set_hermes_model_options(self, payload: dict[str, Any]) -> None:
        current_provider = str(payload.get("provider") or self._hermes_model_provider)
        current_model = str(payload.get("model") or self._hermes_model_name)
        options: list[dict[str, Any]] = []
        for provider in payload.get("providers") or []:
            if not isinstance(provider, dict):
                continue
            slug = str(provider.get("slug") or "").strip()
            name = str(provider.get("name") or slug or "Hermes").strip()
            for raw_model in provider.get("models") or []:
                if isinstance(raw_model, dict):
                    model = str(raw_model.get("id") or raw_model.get("name") or "").strip()
                else:
                    model = str(raw_model).strip()
                if not model:
                    continue
                options.append(
                    {
                        "label": f"{name}  //  {model}",
                        "provider": slug,
                        "model": model,
                    }
                )
        if current_model and not any(
            item["provider"] == current_provider and item["model"] == current_model
            for item in options
        ):
            options.insert(
                0,
                {
                    "label": f"{current_provider or 'Hermes'}  //  {current_model}",
                    "provider": current_provider,
                    "model": current_model,
                },
            )
        self._hermes_models = options
        self._hermes_model_provider = current_provider
        self._hermes_model_name = current_model
        if current_model and not self.config.hermes.model:
            self.config.hermes.provider = current_provider
            self.config.hermes.model = current_model
            self._save_config()
            self.configChanged.emit()
        self.hermesModelsChanged.emit()

    def _hermes_model_changed(self, provider: str, model: str) -> None:
        self._hermes_model_provider = provider
        self._hermes_model_name = model
        self.config.hermes.provider = provider
        self.config.hermes.model = model
        self._save_config()
        self.configChanged.emit()
        self.hermesModelsChanged.emit()
        self.notification.emit(f"Hermes model selected: {model}")

    def _hermes_reasoning_changed(self, effort: str) -> None:
        self.config.hermes.reasoning_effort = effort
        self._save_config()
        self.configChanged.emit()
        self.hermesModelsChanged.emit()

    def _model_operation_error(self, message: str) -> None:
        self._record_error("Hermes model: " + message)
        self.notification.emit(message)

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
        if not self._shutdown_complete and status in {
            "connected",
            "offline",
            "error",
            "reconnecting",
            "restarting",
        }:
            future = self.machine_self.set_gateway_connected(
                status == "connected", session_id=self.hermes.sessionId
            )
            if future is not None:
                future.add_done_callback(self._machine_background_done)
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
        elif dotted == "hermes.reasoning_effort":
            self.hermes.setReasoning(self.config.hermes.reasoning_effort)
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

    def _schedule(self, milliseconds: int, callback) -> None:
        """Run a callback later, cancelled automatically with this controller."""

        timer = QTimer(self)
        timer.setSingleShot(True)

        def run() -> None:
            self._scheduled_timers.discard(timer)
            try:
                callback()
            finally:
                timer.deleteLater()

        timer.timeout.connect(run)
        self._scheduled_timers.add(timer)
        timer.start(max(0, int(milliseconds)))
