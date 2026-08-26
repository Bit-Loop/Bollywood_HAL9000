from __future__ import annotations

import numpy as np

from hal9000.config import AppConfig, ConfigStore
from hal9000.controller import HalController
from hal9000.paths import AppPaths
from hal9000.speech.tts.base import AudioBuffer
from hal9000.state import HalState


def build_controller(tmp_path, config: AppConfig | None = None) -> HalController:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    paths.ensure()
    config = config or AppConfig()
    config.general.setup_complete = True
    return HalController(
        paths,
        ConfigStore(paths),
        config,
        tmp_path,
        services_enabled=False,
    )


def test_disabling_machine_self_does_not_register_its_hermes_mcp(qtbot, tmp_path) -> None:
    config = AppConfig()
    config.sentience.enabled = False
    controller = build_controller(tmp_path, config)
    assert controller.hermes._self_mcp_enabled is False
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_self_mcp_degradation_is_nonfatal_to_chat_state(qtbot, tmp_path) -> None:
    controller = build_controller(tmp_path)

    controller.hermes.selfMcpDegraded.emit(
        "HAL self MCP failed its Hermes probe: connection closed"
    )

    assert controller.state_machine.current != HalState.ERROR
    assert any("self MCP" in message for message in controller.recentErrors)
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


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


def test_voice_response_modes_gate_ordinary_tts_without_disabling_preview(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    spoken: list[str] = []
    monkeypatch.setattr(controller.tts, "speak", spoken.append)

    controller.config.voice.response_mode = "text_only"
    controller._assistant_started("typed")
    controller._assistant_delta("Typed response.")
    controller._assistant_completed("Typed response.")
    assert spoken == []

    controller.testVoice("Piper")
    assert controller._speech_pending == 1

    controller.config.voice.response_mode = "voice_prompts"
    controller._voice_conversation = True
    controller._assistant_started("voice")
    controller._assistant_delta("Voice response.")
    controller._assistant_completed("Voice response.")
    assert spoken == ["Voice response."]
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_manual_microphone_marks_only_that_turn_as_voice_prompt(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    monkeypatch.setattr(controller.audio, "startRecording", lambda: None)
    controller.openManual()
    controller.config.voice.response_mode = "voice_prompts"

    controller.toggleManualMic()

    assert controller._capture_origin == "manual"
    assert controller._speech_allowed() is True
    controller._assistant_complete_received = True
    controller._speech_turn_finished = False
    controller._maybe_finish_speech()
    assert controller._capture_origin == ""
    assert controller._speech_allowed() is False

    controller._voice_conversation = True
    controller._capture_origin = "manual"
    controller.closeManual()
    controller._speech_turn_finished = False
    controller._maybe_finish_speech()
    assert controller._capture_origin == ""
    assert controller._speech_allowed() is False
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_cancelling_voice_turn_does_not_mark_next_typed_turn_as_voice(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    monkeypatch.setattr(controller.hermes, "cancel", lambda: None)
    controller.config.voice.response_mode = "voice_prompts"
    controller._voice_conversation = True
    controller._capture_origin = "manual"

    controller.stopGeneration()

    assert controller._capture_origin == ""
    assert controller._speech_allowed() is False
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_live_volume_preview_does_not_persist_until_commit(qtbot, tmp_path, monkeypatch) -> None:
    controller = build_controller(tmp_path)
    saves: list[float] = []
    monkeypatch.setattr(
        controller.config_store,
        "save",
        lambda config: saves.append(config.voice.volume),
    )

    for value in (0.2, 0.3, 0.4, 0.5):
        controller.previewSetting("voice.volume", value)

    assert controller.playback.volume == 0.5
    assert saves == []

    controller.commitSetting("voice.volume")
    assert saves == [0.5]
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


def test_exact_critical_machine_degradation_cancels_active_task_once(
    qtbot, tmp_path, monkeypatch
) -> None:
    controller = build_controller(tmp_path)
    cancelled: list[bool] = []
    notices: list[str] = []
    monkeypatch.setattr(controller.hermes, "cancel", lambda: cancelled.append(True))
    controller.notification.connect(notices.append)
    controller._active_machine_task_id = "task-critical"
    controller._transition(HalState.THINKING, "test active task")

    controller._stop_for_machine_degradation("another-task")
    controller._stop_for_machine_degradation("task-critical")
    controller._stop_for_machine_degradation("task-critical")

    assert cancelled == [True]
    assert controller.state == HalState.ERROR.value
    assert notices == [
        "HAL checkpointed this task because an exact required capability was lost"
    ]
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

    assert controller.hermesModels[0]["provider"] == ""
    assert controller.hermesModels[0]["model"] == ""
    assert controller.hermesModels[1]["provider"] == "openai-codex"
    assert controller.hermesModels[1]["model"] == "gpt-5.6-sol"
    assert controller.hermesModels[1]["available"] is False
    assert controller.hermesModels[1]["authenticated"] is False
    assert controller.hermesModelIndex == 0
    assert controller.hermesModels[2]["provider"] == "copilot"
    assert "gpt-5.6-sol" in controller.hermesModelLabel
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)


def test_observed_fallback_does_not_replace_sticky_manual_model(qtbot, tmp_path) -> None:
    controller = build_controller(tmp_path)
    controller.config.hermes.router.enabled = False
    controller.config.hermes.provider = "openai-codex"
    controller.config.hermes.model = "gpt-5.6-sol"

    controller._hermes_active_model_changed("ollama", "qwen-local")
    controller._hermes_active_reasoning_changed("low")

    assert "qwen-local" in controller.hermesModelLabel
    assert "LOW" in controller.hermesModelLabel
    assert controller.config.hermes.provider == "openai-codex"
    assert controller.config.hermes.model == "gpt-5.6-sol"
    controller.shutdown()
    controller.deleteLater()
    qtbot.wait(20)
