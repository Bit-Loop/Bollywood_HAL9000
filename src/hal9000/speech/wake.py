"""Sherpa-ONNX open-vocabulary wake word implementation."""

from __future__ import annotations

import logging
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np

SHERPA_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
    "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2"
)
SHERPA_MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"


def ensure_sherpa_model(
    cache_root: Path,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    target = cache_root / SHERPA_MODEL_NAME
    if (target / "tokens.txt").exists():
        return target
    cache_root.mkdir(parents=True, exist_ok=True)
    archive = cache_root / f"{SHERPA_MODEL_NAME}.tar.bz2"
    partial = archive.with_suffix(archive.suffix + ".part")
    try:
        request = urllib.request.Request(SHERPA_MODEL_URL, headers={"User-Agent": "HAL9000/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            while chunk := response.read(1024 * 256):
                output.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)
        partial.replace(archive)
        with tarfile.open(archive, "r:bz2") as bundle:
            bundle.extractall(cache_root, filter="data")
    finally:
        partial.unlink(missing_ok=True)
    archive.unlink(missing_ok=True)
    if not (target / "tokens.txt").exists():
        raise RuntimeError(f"Sherpa wake model extraction failed: {target}")
    return target


class SherpaWakeWord:
    sample_rate = 16_000
    frame_length = 1_280

    def __init__(
        self,
        phrase: str,
        sensitivity: float,
        model_dir: Path,
    ) -> None:
        import sherpa_onnx
        from sherpa_onnx import text2token

        phrase = (phrase or "hey hal").strip().lower()
        if not (model_dir / "tokens.txt").exists():
            raise RuntimeError(f"Sherpa model is incomplete: {model_dir}")
        acoustic_phrases = [phrase]
        if phrase == "hey hal":
            acoustic_phrases.extend(("hey hall", "hey hell"))
        tokenized_phrases = text2token(
            [candidate.upper() for candidate in acoustic_phrases],
            tokens=str(model_dir / "tokens.txt"),
            tokens_type="bpe",
            bpe_model=str(model_dir / "bpe.model"),
        )
        keyword = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix="hal9000-kws-",
            delete=False,
            encoding="utf-8",
        )
        display = phrase.upper().replace(" ", "_")
        for tokenized in tokenized_phrases:
            keyword.write(" ".join(tokenized) + f" @{display}\n")
        keyword.close()
        self._keyword_file = Path(keyword.name)
        # Sherpa's threshold is a minimum detection probability: lower values
        # are more sensitive.  Keep the user-facing slider intuitive while
        # pairing it with the keyword score used by the upstream examples.
        sensitivity = min(1.0, max(0.0, sensitivity))
        threshold = 0.25 - (0.25 * sensitivity)
        threshold = min(0.25, max(0.01, threshold))
        keyword_score = 1.5 + (2.5 * sensitivity)

        def model_file(pattern: str, *, exclude: str = "") -> str:
            matches = sorted(
                path for path in model_dir.glob(pattern) if not exclude or exclude not in path.name
            )
            if not matches:
                raise RuntimeError(f"Sherpa model file missing: {pattern}")
            return str(matches[0])

        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(model_dir / "tokens.txt"),
            encoder=model_file("encoder-*int8.onnx"),
            decoder=model_file("decoder-*.onnx", exclude="int8"),
            joiner=model_file("joiner-*int8.onnx"),
            keywords_file=str(self._keyword_file),
            keywords_score=keyword_score,
            keywords_threshold=threshold,
            num_threads=1,
        )
        self._stream = self._spotter.create_stream()

    def process(self, pcm: np.ndarray) -> bool:
        samples = np.asarray(pcm, dtype=np.float32) / 32768.0
        self._stream.accept_waveform(self.sample_rate, samples)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            if self._spotter.get_result(self._stream):
                self.reset()
                return True
        return False

    def reset(self) -> None:
        self._stream = self._spotter.create_stream()

    def close(self) -> None:
        self._keyword_file.unlink(missing_ok=True)
