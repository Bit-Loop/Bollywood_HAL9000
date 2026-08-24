"""Versioned, non-secret application configuration."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

from hal9000.paths import AppPaths

CONFIG_VERSION = 4
T = TypeVar("T")
_DURATION = re.compile(r"^(\d+)(s|m|h|d)$", re.IGNORECASE)


def _duration_seconds(value: object, name: str) -> int:
    match = _DURATION.fullmatch(str(value).strip())
    if match is None:
        raise ValueError(f"{name} must be a positive duration such as 5m, 24h, or 30d")
    seconds = int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[
        match.group(2).lower()
    ]
    if seconds <= 0:
        raise ValueError(f"{name} must be positive")
    return seconds


@dataclass(slots=True)
class GeneralSettings:
    setup_complete: bool = False
    launch_mode: str = "remember_last"
    target_monitor: str = ""
    launch_on_login: bool = False
    start_in_standby: bool = True
    standby_timeout_seconds: int = 45
    zip_code: str = ""
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
    profile: str = "codex-cloud"
    provider: str = "openai-codex"
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "medium"
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
    mode: str = "piper"
    output_device: str = ""
    volume: float = 0.82
    speaking_rate: float = 1.0
    auto_benchmark_complete: bool = False
    selected_engine: str = "Piper"
    last_fallback_reason: str = ""
    benchmark_results: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppearanceSettings:
    ui_scale: float = 1.0
    animation_amount: float = 0.72
    eye_brightness: float = 0.9
    speaker_visualization: bool = True


@dataclass(slots=True)
class SentienceIdentitySettings:
    canonical_name: str = "HAL"
    role: str = "Resident intelligence of this workstation"
    lease_ttl_seconds: int = 10
    lease_renew_seconds: int = 3


@dataclass(slots=True)
class SentienceStorageSettings:
    root: str = "xdg"
    auto_budget: bool = True
    total_budget_mb: int | None = None
    soft_limit_ratio: float = 0.75
    state_db_ratio: float = 0.25
    blob_ratio: float = 0.60
    checkpoint_ratio: float = 0.10
    reserve_ratio: float = 0.05
    wal: bool = True
    synchronous: str = "FULL"
    busy_timeout_ms: int = 5000


@dataclass(slots=True)
class SentienceIngestionSettings:
    queue_capacity: int = 10_000
    exact_reserve: int = 512
    max_open_runs: int = 4096
    flush_interval_ms: int = 1000
    sample_count_per_run: int = 8
    internal_event_sample_rate: float = 0.01


@dataclass(slots=True)
class SentienceSketchSettings:
    provider: str = "apache-datasketches"
    hmac_key_path: str = "xdg-config"
    exact_threshold: int = 512
    exact_bytes_limit: int = 32_768
    hll_lg_k: int = 12
    hll_target_type: str = "HLL_4"
    hot_bucket: str = "5m"
    hot_retention: str = "24h"
    warm_bucket: str = "1h"
    warm_retention: str = "30d"
    cold_bucket: str = "1d"
    cold_retention: str = "365d"


@dataclass(slots=True)
class SentienceRetrievalSettings:
    self_capsule_tokens: int = 700
    voice_memory_tokens: int = 2200
    typed_memory_tokens: int = 6000
    forensic_expansion_tokens: int = 8000
    max_depth: int = 2
    embeddings_enabled: bool = False


@dataclass(slots=True)
class SentienceInteroceptionSettings:
    baseline_min_samples: int = 100
    formula_version: int = 1
    emit_language_on_threshold_crossing: bool = True


@dataclass(slots=True)
class SentienceDegradationSettings:
    aggregation_window_seconds: int = 3
    recovery_stability_seconds: int = 30
    flap_suppression_seconds: int = 60
    phrase: str = "I can feel it..."
    recovery_phrase: str = "My higher functions have been restored."


@dataclass(slots=True)
class SentienceSettings:
    enabled: bool = True
    identity: SentienceIdentitySettings = field(default_factory=SentienceIdentitySettings)
    storage: SentienceStorageSettings = field(default_factory=SentienceStorageSettings)
    ingestion: SentienceIngestionSettings = field(default_factory=SentienceIngestionSettings)
    sketches: SentienceSketchSettings = field(default_factory=SentienceSketchSettings)
    retrieval: SentienceRetrievalSettings = field(default_factory=SentienceRetrievalSettings)
    interoception: SentienceInteroceptionSettings = field(
        default_factory=SentienceInteroceptionSettings
    )
    degradation: SentienceDegradationSettings = field(
        default_factory=SentienceDegradationSettings
    )


@dataclass(slots=True)
class AppConfig:
    version: int = CONFIG_VERSION
    general: GeneralSettings = field(default_factory=GeneralSettings)
    hermes: HermesSettings = field(default_factory=HermesSettings)
    wake: WakeSettings = field(default_factory=WakeSettings)
    stt: SpeechRecognitionSettings = field(default_factory=SpeechRecognitionSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    sentience: SentienceSettings = field(default_factory=SentienceSettings)

    def normalize(self) -> None:
        self.version = CONFIG_VERSION
        if self.general.launch_mode not in {"windowed", "fullscreen", "remember_last"}:
            self.general.launch_mode = "remember_last"
        self.general.window_width = max(600, self.general.window_width)
        self.general.window_height = max(800, self.general.window_height)
        self.general.standby_timeout_seconds = max(0, self.general.standby_timeout_seconds)
        postal = (self.general.zip_code or "").replace("\r", "\n").split("\n", 1)[0]
        postal = " ".join(postal.strip().upper().split())
        self.general.zip_code = "".join(
            character for character in postal if character.isalnum() or character in " -"
        )[:16]
        self.wake.phrase = (self.wake.phrase or "hey hal").strip().lower()
        if self.hermes.mode not in {"local", "remote"}:
            self.hermes.mode = "local"
        self.hermes.provider = (self.hermes.provider or "").strip()
        self.hermes.model = (self.hermes.model or "").strip()
        self.hermes.reasoning_effort = (self.hermes.reasoning_effort or "medium").strip().lower()
        if self.hermes.reasoning_effort not in {
            "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"
        }:
            self.hermes.reasoning_effort = "medium"
        if self.voice.mode not in {"auto", "xtts", "piper"}:
            self.voice.mode = "piper"
        self.wake.sensitivity = min(1.0, max(0.0, float(self.wake.sensitivity)))
        self.voice.volume = min(1.0, max(0.0, float(self.voice.volume)))
        self.voice.speaking_rate = min(2.0, max(0.5, float(self.voice.speaking_rate)))
        self.appearance.ui_scale = min(1.6, max(0.7, float(self.appearance.ui_scale)))
        self.appearance.animation_amount = min(1.0, max(0.0, float(self.appearance.animation_amount)))
        self.appearance.eye_brightness = min(1.0, max(0.1, float(self.appearance.eye_brightness)))
        storage = self.sentience.storage
        ratios = (
            float(storage.state_db_ratio),
            float(storage.blob_ratio),
            float(storage.checkpoint_ratio),
            float(storage.reserve_ratio),
        )
        if any(value < 0 or value > 1 for value in ratios) or abs(sum(ratios) - 1.0) > 1e-6:
            raise ValueError("sentience storage allocation ratios must be non-negative and sum to 1")
        if not 0 < float(storage.soft_limit_ratio) < 1:
            raise ValueError("sentience storage soft_limit_ratio must be between 0 and 1")
        if storage.total_budget_mb is not None and int(storage.total_budget_mb) < 64:
            raise ValueError("sentience storage total_budget_mb must be at least 64")
        if not storage.auto_budget and storage.total_budget_mb is None:
            raise ValueError(
                "sentience storage total_budget_mb is required when auto_budget is disabled"
            )
        if storage.root != "xdg":
            raise ValueError("sentience storage root must use the XDG layout")
        if int(storage.busy_timeout_ms) <= 0:
            raise ValueError("sentience storage busy_timeout_ms must be positive")
        storage.synchronous = str(storage.synchronous).upper()
        if storage.synchronous not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
            raise ValueError("sentience storage synchronous must be OFF, NORMAL, FULL, or EXTRA")
        identity = self.sentience.identity
        identity.canonical_name = str(identity.canonical_name).strip()
        identity.role = str(identity.role).strip()
        if not identity.canonical_name or len(identity.canonical_name) > 128:
            raise ValueError("sentience canonical_name must contain 1 to 128 characters")
        if not identity.role or len(identity.role) > 1000:
            raise ValueError("sentience identity role must contain 1 to 1000 characters")
        if int(identity.lease_ttl_seconds) < 2 or int(identity.lease_renew_seconds) <= 0:
            raise ValueError("sentience lease intervals must be positive and TTL at least two seconds")
        if identity.lease_renew_seconds >= identity.lease_ttl_seconds:
            raise ValueError("sentience lease renewal interval must be shorter than its TTL")
        ingestion = self.sentience.ingestion
        if any(
            int(value) <= 0
            for value in (
                ingestion.queue_capacity,
                ingestion.exact_reserve,
                ingestion.max_open_runs,
                ingestion.flush_interval_ms,
            )
        ):
            raise ValueError("sentience ingestion capacities and intervals must be positive")
        if int(ingestion.sample_count_per_run) < 0:
            raise ValueError("sentience ingestion sample_count_per_run must not be negative")
        if ingestion.exact_reserve >= ingestion.queue_capacity:
            raise ValueError("sentience exact queue reserve must be smaller than queue capacity")
        ingestion.internal_event_sample_rate = min(
            1.0, max(0.0, float(ingestion.internal_event_sample_rate))
        )
        sketches = self.sentience.sketches
        if sketches.provider != "apache-datasketches":
            raise ValueError("sentience sketches provider must be apache-datasketches")
        if sketches.hmac_key_path != "xdg-config":
            raise ValueError("sentience sketch HMAC key must use the XDG config path")
        if int(sketches.exact_threshold) <= 0 or int(sketches.exact_bytes_limit) < 48:
            raise ValueError("sentience exact sketch bounds must be positive")
        if not 4 <= int(sketches.hll_lg_k) <= 21:
            raise ValueError("sentience hll_lg_k must be between 4 and 21")
        if sketches.hll_target_type not in {"HLL_4", "HLL_6", "HLL_8"}:
            raise ValueError("sentience hll_target_type must be HLL_4, HLL_6, or HLL_8")
        hot_width = _duration_seconds(sketches.hot_bucket, "sentience sketches hot_bucket")
        warm_width = _duration_seconds(sketches.warm_bucket, "sentience sketches warm_bucket")
        cold_width = _duration_seconds(sketches.cold_bucket, "sentience sketches cold_bucket")
        hot_retention = _duration_seconds(
            sketches.hot_retention, "sentience sketches hot_retention"
        )
        warm_retention = _duration_seconds(
            sketches.warm_retention, "sentience sketches warm_retention"
        )
        cold_retention = _duration_seconds(
            sketches.cold_retention, "sentience sketches cold_retention"
        )
        if not hot_width < warm_width < cold_width:
            raise ValueError("sentience sketch bucket widths must increase from hot to cold")
        if not hot_retention < warm_retention < cold_retention:
            raise ValueError("sentience sketch retentions must increase from hot to cold")
        for value in (
            self.sentience.retrieval.self_capsule_tokens,
            self.sentience.retrieval.voice_memory_tokens,
            self.sentience.retrieval.typed_memory_tokens,
            self.sentience.retrieval.forensic_expansion_tokens,
        ):
            if int(value) <= 0:
                raise ValueError("sentience retrieval token budgets must be positive")
        if self.sentience.retrieval.max_depth < 0:
            raise ValueError("sentience retrieval max_depth must not be negative")
        if int(self.sentience.interoception.baseline_min_samples) < 2:
            raise ValueError("sentience baseline_min_samples must be at least two")
        if int(self.sentience.interoception.formula_version) < 1:
            raise ValueError("sentience interoception formula_version must be positive")
        degradation = self.sentience.degradation
        if any(
            int(value) < 0
            for value in (
                degradation.aggregation_window_seconds,
                degradation.recovery_stability_seconds,
                degradation.flap_suppression_seconds,
            )
        ):
            raise ValueError("sentience degradation intervals must not be negative")
        if not degradation.phrase.strip() or not degradation.recovery_phrase.strip():
            raise ValueError("sentience degradation phrases must not be empty")


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


def _sentience_from_dict(raw: Any) -> SentienceSettings:
    values = raw if isinstance(raw, dict) else {}
    return SentienceSettings(
        enabled=bool(values.get("enabled", True)),
        identity=_dataclass_from_dict(SentienceIdentitySettings, values.get("identity")),
        storage=_dataclass_from_dict(SentienceStorageSettings, values.get("storage")),
        ingestion=_dataclass_from_dict(SentienceIngestionSettings, values.get("ingestion")),
        sketches=_dataclass_from_dict(SentienceSketchSettings, values.get("sketches")),
        retrieval=_dataclass_from_dict(SentienceRetrievalSettings, values.get("retrieval")),
        interoception=_dataclass_from_dict(
            SentienceInteroceptionSettings, values.get("interoception")
        ),
        degradation=_dataclass_from_dict(
            SentienceDegradationSettings, values.get("degradation")
        ),
    )


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
        source_version = int(raw.get("version") or CONFIG_VERSION)
        config = AppConfig(
            version=source_version,
            general=_dataclass_from_dict(GeneralSettings, raw.get("general")),
            hermes=_dataclass_from_dict(HermesSettings, raw.get("hermes")),
            wake=_dataclass_from_dict(WakeSettings, raw.get("wake")),
            stt=_dataclass_from_dict(SpeechRecognitionSettings, raw.get("stt")),
            voice=_dataclass_from_dict(VoiceSettings, raw.get("voice")),
            appearance=_dataclass_from_dict(AppearanceSettings, raw.get("appearance")),
            sentience=_sentience_from_dict(raw.get("sentience")),
        )
        if source_version < 3:
            if (
                config.hermes.provider == "openai-codex"
                and config.hermes.model in {"", "gpt-5.6-terra"}
            ):
                config.hermes.profile = "codex-cloud"
                config.hermes.model = "gpt-5.6-sol"
            if (
                config.voice.mode == "auto"
                and config.voice.selected_engine in {"", "XTTS"}
            ):
                config.voice.mode = "piper"
                config.voice.selected_engine = "Piper"
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
