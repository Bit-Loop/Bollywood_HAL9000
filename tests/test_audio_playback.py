from __future__ import annotations

import threading

import numpy as np

from hal9000.audio.playback import AudioPlayback
from hal9000.speech.tts.base import AudioBuffer


class RecordingPlayback(AudioPlayback):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release_first = threading.Event()
        self.played: list[str] = []

    def _play_buffer_sync(self, audio: AudioBuffer) -> None:
        self.played.append(audio.engine)
        if len(self.played) == 1:
            self.started.set()
            assert self.release_first.wait(2)


def audio(name: str) -> AudioBuffer:
    return AudioBuffer(np.ones(256, dtype=np.float32), 16_000, name)


def test_playback_queues_synthesized_chunks_without_interrupting(qtbot) -> None:
    playback = RecordingPlayback()
    finished: list[bool] = []
    playback.finished.connect(lambda: finished.append(True))

    playback.play(audio("first"))
    assert playback.started.wait(1)
    playback.play(audio("second"))
    playback.play(audio("third"))
    playback.release_first.set()

    qtbot.waitUntil(lambda: not playback.playing, timeout=2000)
    assert playback.played == ["first", "second", "third"]
    assert finished == [True]

    # A later streamed sentence reuses the idle worker and begins a new batch.
    playback.play(audio("fourth"))
    qtbot.waitUntil(
        lambda: playback.played == ["first", "second", "third", "fourth"]
        and not playback.playing,
        timeout=2000,
    )
    assert finished == [True, True]
    playback.stop()
