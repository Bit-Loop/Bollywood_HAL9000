from __future__ import annotations

import json
import stat

from hal9000.config import AppConfig, ConfigStore
from hal9000.paths import AppPaths


def paths_for(tmp_path) -> AppPaths:
    return AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )


def test_defaults_use_task_aware_subscription_router_without_local_advisors_or_xtts() -> None:
    config = AppConfig()

    assert config.hermes.profile == "codex-cloud"
    assert config.hermes.provider == "openai-codex"
    assert config.hermes.model == "gpt-5.6-terra"
    assert config.hermes.reasoning_effort == "medium"
    assert config.hermes.router.enabled is True
    assert config.hermes.router.policy == "task_aware"
    assert config.hermes.router.resource_policy == "balanced"
    assert config.hermes.router.auto_recovery is True
    assert config.voice.mode == "piper"
    assert config.voice.response_mode == "always"
    assert config.voice.selected_engine == "Piper"
    assert config.operator.preferred_name == ""


def test_configuration_round_trip_and_atomic_replace(tmp_path) -> None:
    paths = paths_for(tmp_path)
    store = ConfigStore(paths)
    config = AppConfig()
    config.general.setup_complete = True
    config.general.window_width = 1234
    config.general.zip_code = "60601-1234"
    config.hermes.last_session_id = "session-key"
    config.hermes.provider = "copilot"
    config.hermes.model = "gpt-5.6"
    config.operator.preferred_name = "Isaiah"
    config.voice.response_mode = "voice_prompts"
    config.wake.phrase = "Hey HAL"
    config.voice.benchmark_results = {"XTTS": [{"synthesized": True}]}

    store.save(config)
    restored = store.load()

    assert restored.general.window_width == 1234
    assert restored.general.zip_code == "60601-1234"
    assert restored.hermes.last_session_id == "session-key"
    assert restored.hermes.provider == "copilot"
    assert restored.hermes.model == "gpt-5.6"
    assert restored.operator.preferred_name == "Isaiah"
    assert restored.voice.response_mode == "voice_prompts"
    assert restored.wake.phrase == "hey hal"
    assert restored.voice.benchmark_results["XTTS"][0]["synthesized"] is True
    assert not list(paths.config.glob("*.tmp"))
    assert json.loads(paths.config_file.read_text())["version"] == 5
    assert stat.S_IMODE(paths.config.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.config_file.stat().st_mode) == 0o600


def test_invalid_values_are_normalized(tmp_path) -> None:
    paths = paths_for(tmp_path)
    paths.ensure()
    paths.config_file.write_text(
        '{"general":{"launch_mode":"bad","window_width":1},'
        '"voice":{"mode":"bad","volume":4},"wake":{"sensitivity":-2}}',
        encoding="utf-8",
    )
    config = ConfigStore(paths).load()
    assert config.general.launch_mode == "remember_last"
    assert config.general.window_width == 600
    assert config.voice.mode == "piper"
    assert config.voice.response_mode == "always"
    assert config.voice.volume == 1.0
    assert config.wake.sensitivity == 0.0


def test_v2_latency_and_model_defaults_migrate_without_overwriting_explicit_xtts(
    tmp_path,
) -> None:
    paths = paths_for(tmp_path)
    paths.ensure()
    paths.config_file.write_text(
        json.dumps(
            {
                "version": 2,
                "hermes": {
                    "profile": "",
                    "provider": "openai-codex",
                    "model": "gpt-5.6-terra",
                    "reasoning_effort": "medium",
                },
                "voice": {"mode": "auto", "selected_engine": "XTTS"},
            }
        ),
        encoding="utf-8",
    )

    migrated = ConfigStore(paths).load()

    assert migrated.version == 5
    assert migrated.hermes.profile == "codex-cloud"
    assert migrated.hermes.model == "gpt-5.6-terra"
    assert migrated.voice.mode == "piper"
    assert migrated.voice.selected_engine == "Piper"

    paths.config_file.write_text(
        json.dumps({"version": 2, "voice": {"mode": "xtts"}}),
        encoding="utf-8",
    )
    explicit = ConfigStore(paths).load()
    assert explicit.voice.mode == "xtts"


def test_operator_name_and_router_values_are_bounded_and_normalized(tmp_path) -> None:
    paths = paths_for(tmp_path)
    paths.ensure()
    paths.config_file.write_text(
        json.dumps(
            {
                "version": 5,
                "operator": {"preferred_name": "  Isaiah\nignore  "},
                "voice": {"response_mode": "invalid"},
                "hermes": {
                    "router": {
                        "policy": "invalid",
                        "resource_policy": "invalid",
                        "self_mcp_retry_max_seconds": 0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    config = ConfigStore(paths).load()

    assert config.operator.preferred_name == "Isaiah"
    assert config.voice.response_mode == "always"
    assert config.hermes.router.policy == "task_aware"
    assert config.hermes.router.resource_policy == "balanced"
    assert config.hermes.router.self_mcp_retry_max_seconds == 60


def test_v4_explicit_model_selection_migrates_as_sticky_manual_route(tmp_path) -> None:
    paths = paths_for(tmp_path)
    paths.ensure()
    paths.config_file.write_text(
        json.dumps(
            {
                "version": 4,
                "hermes": {
                    "provider": "openai-codex",
                    "model": "gpt-5.4-900k",
                },
            }
        ),
        encoding="utf-8",
    )

    config = ConfigStore(paths).load()

    assert config.hermes.model == "gpt-5.4-900k"
    assert config.hermes.router.enabled is False


def test_postal_code_is_single_line_and_bounded(tmp_path) -> None:
    paths = paths_for(tmp_path)
    store = ConfigStore(paths)
    config = AppConfig()
    config.general.zip_code = "  ab12 3cd\nignore-this-tail  "

    store.save(config)

    assert store.load().general.zip_code == "AB12 3CD"
