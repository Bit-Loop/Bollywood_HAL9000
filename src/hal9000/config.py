"""Versioned, non-secret application configuration."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

from hal9000.paths import AppPaths

CONFIG_VERSION = 1
T = TypeVar("T")


@dataclass(slots=True)
class GeneralSettings:
    setup_complete: bool = False
    launch_mode: str = "remember_last"
    target_monitor: str = ""
    launch_on_login: bool = False
    start_in_standby: bool = True
    standby_timeout_seconds: int = 45
    window_width: int = 800
    window_height: int = 1000
    window_x: int | None = None
    window_y: int | None = None
    last_fullscreen: bool = False


@dataclass(slots=True)
class HermesSettings:
    executable: str = ""
    mode: str = "local"
    backend_url: str = "http://127.0.0.1:9119"
    profile: str = ""
    auto_start: bool = True
    last_session_id: str = ""


@dataclass(slots=True)
class WakeSettings:
    enabled: bool = True
    provider: str = "sherpa"
    phrase: str = "hey hal"
    sensitivity: float = 0.6
    input_device: str = ""


@dataclass(slots=True)
class SpeechRecognitionSettings:
    provider: str = "local"
    model: str = "small"
    language: str = "en"
    input_device: str = ""
    silence_threshold: float = 0.018
    silence_seconds: float = 1.1
    max_utterance_seconds: int = 45


@dataclass(slots=True)
class VoiceSettings:
    mode: str = "auto"
    output_device: str = ""
    volume: float = 0.82
    speaking_rate: float = 1.0
    auto_benchmark_complete: bool = False
    selected_engine: str = ""
    last_fallback_reason: str = ""
    benchmark_results: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppearanceSettings:
    ui_scale: float = 1.0
    animation_amount: float = 0.72
    eye_brightness: float = 0.9
    speaker_visualization: bool = True


@dataclass(slots=True)
class AppConfig:
    version: int = CONFIG_VERSION
    general: GeneralSettings = field(default_factory=GeneralSettings)
    hermes: HermesSettings = field(default_factory=HermesSettings)
    wake: WakeSettings = field(default_factory=WakeSettings)
    stt: SpeechRecognitionSettings = field(default_factory=SpeechRecognitionSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)

    def normalize(self) -> None:
        self.version = CONFIG_VERSION
        if self.general.launch_mode not in {"windowed", "fullscreen", "remember_last"}:
            self.general.launch_mode = "remember_last"
        self.general.window_width = max(600, self.general.window_width)
        self.general.window_height = max(800, self.general.window_height)
        self.general.standby_timeout_seconds = max(0, self.general.standby_timeout_seconds)
        self.wake.phrase = (self.wake.phrase or "hey hal").strip().lower()
        if self.hermes.mode not in {"local", "remote"}:
            self.hermes.mode = "local"
        if self.voice.mode not in {"auto", "xtts", "piper"}:
            self.voice.mode = "auto"
        self.wake.sensitivity = min(1.0, max(0.0, float(self.wake.sensitivity)))
        self.voice.volume = min(1.0, max(0.0, float(self.voice.volume)))
        self.voice.speaking_rate = min(2.0, max(0.5, float(self.voice.speaking_rate)))
        self.appearance.ui_scale = min(1.6, max(0.7, float(self.appearance.ui_scale)))
        self.appearance.animation_amount = min(1.0, max(0.0, float(self.appearance.animation_amount)))
        self.appearance.eye_brightness = min(1.0, max(0.1, float(self.appearance.eye_brightness)))


def _dataclass_from_dict(cls: type[T], raw: Any) -> T:
    if not isinstance(raw, dict):
        return cls()
    known = {item.name for item in fields(cls)}
    values: dict[str, Any] = {}
    for item in fields(cls):
        if item.name not in raw:
            continue
        value = raw[item.name]
        values[item.name] = value
    return cls(**{key: value for key, value in values.items() if key in known})


class ConfigStore:
    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths.discover()

    def load(self) -> AppConfig:
        path = self.paths.config_file
        if not path.exists():
            config = AppConfig()
            config.normalize()
            return config
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = AppConfig()
            config.normalize()
            return config
        if not isinstance(raw, dict):
            raw = {}
        config = AppConfig(
            version=int(raw.get("version") or CONFIG_VERSION),
            general=_dataclass_from_dict(GeneralSettings, raw.get("general")),
            hermes=_dataclass_from_dict(HermesSettings, raw.get("hermes")),
            wake=_dataclass_from_dict(WakeSettings, raw.get("wake")),
            stt=_dataclass_from_dict(SpeechRecognitionSettings, raw.get("stt")),
            voice=_dataclass_from_dict(VoiceSettings, raw.get("voice")),
            appearance=_dataclass_from_dict(AppearanceSettings, raw.get("appearance")),
        )
        config.normalize()
        return config

    def save(self, config: AppConfig) -> None:
        config.normalize()
        self.paths.ensure()
        payload = json.dumps(asdict(config), indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(
            prefix="config.", suffix=".tmp", dir=self.paths.config
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).replace(self.paths.config_file)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
