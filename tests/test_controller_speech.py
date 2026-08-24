from __future__ import annotations

import numpy as np

from hal9000.config import AppConfig, ConfigStore
from hal9000.controller import HalController
from hal9000.paths import AppPaths
from hal9000.speech.tts.base import AudioBuffer


def build_controller(tmp_path) -> HalController:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    paths.ensure()
    config = AppConfig()
    config.general.setup_complete = True
    return HalController(
        paths,
        ConfigStore(paths),
        config,
        tmp_path,
        services_enabled=False,
    )


def test_controller_starts_tts_on_first_complete_streamed_sentence(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    spoken: list[str] = []
    monkeypatch.setattr(controller.tts, "speak", spoken.append)

    controller._assistant_started("message")
    controller._assistant_delta("**Good morning.** The second sentence")
    assert spoken == ["Good morning."]

    controller._assistant_delta(" is ready")
    controller._assistant_completed("**Good morning.** The second sentence is ready")
    assert spoken == ["Good morning.", "The second sentence is ready."]
    assert "**" in controller.conversations.snapshot()[-1]["text"]

    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_stop_voice_suppresses_later_chunks_from_the_same_turn(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    spoken: list[str] = []
    monkeypatch.setattr(controller.tts, "speak", spoken.append)

    controller._assistant_started("message")
    controller._assistant_delta("First sentence. ")
    assert spoken == ["First sentence."]

    controller.stopSpeech()
    controller._assistant_delta("**This must stay silent.**")
    controller._assistant_completed("First sentence. **This must stay silent.**")

    assert spoken == ["First sentence."]
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_stop_voice_rejects_audio_already_queued_from_the_worker(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    played: list[AudioBuffer] = []
    monkeypatch.setattr(controller.playback, "play", played.append)
    controller._assistant_started("message")
    stale_generation = controller.tts.speechGeneration
    audio = AudioBuffer(np.ones(800, dtype=np.float32), 16_000, "Piper")

    controller.stopSpeech()
    controller._synthesis_ready(audio, stale_generation)

    assert played == []
    assert controller.speakerLevel == 0.0
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_streamed_interim_is_not_spoken_twice_and_distinct_final_is_spoken(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    spoken: list[str] = []
    monkeypatch.setattr(controller.tts, "speak", spoken.append)

    controller._assistant_started("message")
    controller._assistant_delta("Working note.")
    controller._assistant_interim("Working note.", True)
    controller._assistant_completed("Final answer.", False)

    assert spoken == ["Working note.", "Final answer."]
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_unstreamed_preview_is_spoken_once_when_completion_repeats_it(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    spoken: list[str] = []
    monkeypatch.setattr(controller.tts, "speak", spoken.append)

    controller._assistant_started("message")
    controller._assistant_interim("Provisional answer.", False)
    controller._assistant_completed("Provisional answer.", True)

    assert spoken == ["Provisional answer."]
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_controller_keeps_zip_context_out_of_visible_transcript(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    controller.config.general.zip_code = "60601"
    submitted: list[str] = []
    monkeypatch.setattr(controller.hermes, "sendPrompt", submitted.append)

    controller.sendText("What is the weather today?")

    assert controller.conversations.snapshot()[-1]["text"] == "What is the weather today?"
    assert "60601" in submitted[-1]
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_controller_builds_picker_from_hermes_inventory_and_keeps_sol_current(
    qtbot, tmp_path
) -> None:
    controller = build_controller(tmp_path)

    controller._set_hermes_model_options(
        {
            "provider": "openai-codex",
            "model": "gpt-5.6-sol",
            "providers": [
                {
                    "slug": "copilot",
                    "name": "GitHub Copilot",
                    "models": ["gpt-5.4", "gpt-5.3-codex"],
                }
            ],
        }
    )

    assert controller.hermesModels[0] == {
        "label": "openai-codex  //  gpt-5.6-sol",
        "provider": "openai-codex",
        "model": "gpt-5.6-sol",
    }
    assert controller.hermesModelIndex == 0
    assert controller.hermesModels[1]["provider"] == "copilot"
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)
