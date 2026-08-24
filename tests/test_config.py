from __future__ import annotations

import json

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


def test_configuration_round_trip_and_atomic_replace(tmp_path) -> None:
    paths = paths_for(tmp_path)
    store = ConfigStore(paths)
    config = AppConfig()
    config.general.setup_complete = True
    config.general.window_width = 1234
    config.hermes.last_session_id = "session-key"
    config.wake.phrase = "Hey HAL"
    config.voice.benchmark_results = {"XTTS": [{"synthesized": True}]}

    store.save(config)
    restored = store.load()

    assert restored.general.window_width == 1234
    assert restored.hermes.last_session_id == "session-key"
    assert restored.wake.phrase == "hey hal"
    assert restored.voice.benchmark_results["XTTS"][0]["synthesized"] is True
    assert not list(paths.config.glob("*.tmp"))
    assert json.loads(paths.config_file.read_text())["version"] == 1


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
    assert config.voice.mode == "auto"
    assert config.voice.volume == 1.0
    assert config.wake.sensitivity == 0.0
