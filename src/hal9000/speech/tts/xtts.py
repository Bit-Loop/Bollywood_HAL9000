"""Proper Coqui XTTS-v2 inference for the HAL fine-tuned checkpoint."""

from __future__ import annotations

import gc
import inspect
import logging
from pathlib import Path

import numpy as np
from huggingface_hub import snapshot_download

from hal9000.speech.tts.base import AudioBuffer, TtsEngine


class XttsHalEngine(TtsEngine):
    name = "XTTS"
    repo_id = "CoderCowMoo/XTTS-v2.0-HAL-9000"

    def __init__(self, cache_dir: Path, prefer_cuda: bool = True) -> None:
        self.cache_dir = cache_dir
        self.prefer_cuda = prefer_cuda
        self._model = None
        self._config = None
        self._conditioning = None
        self._backend = "pending"

    @property
    def initialized(self) -> bool:
        return self._model is not None

    @property
    def backend(self) -> str:
        return self._backend

    def interactive_cuda_available(
        self, minimum_free_bytes: int = 3 * 1024**3
    ) -> tuple[bool, str]:
        """Return whether XTTS can use CUDA without crowding the live desktop."""

        if not self.prefer_cuda:
            return False, "XTTS CUDA is disabled"
        try:
            import torch

            if not torch.cuda.is_available():
                return False, "CUDA is unavailable"
            free_bytes, _total_bytes = torch.cuda.mem_get_info()
        except Exception as exc:
            return False, f"CUDA capacity probe failed: {exc}"
        free_gib = free_bytes / 1024**3
        required_gib = minimum_free_bytes / 1024**3
        if free_bytes < minimum_free_bytes:
            return (
                False,
                f"XTTS has {free_gib:.1f} GiB free CUDA memory; "
                f"interactive mode requires {required_gib:.1f} GiB",
            )
        return True, f"XTTS has {free_gib:.1f} GiB free CUDA memory"

    def initialize(self, progress=None) -> None:
        if self._model is not None:
            return
        if progress:
            progress("downloading", 0.0)
        model_dir = Path(
            snapshot_download(
                repo_id=self.repo_id,
                cache_dir=str(self.cache_dir),
                allow_patterns=[
                    "best_model.pth",
                    "config.json",
                    "vocab.json",
                    "HAL9000_XTTSV2_FT.wav",
                    "HAL9000_Voice_noise_reduced-enhanced-85p.wav",
                ],
            )
        )
        if progress:
            progress("loading", 0.78)
        import torch
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts

        config = XttsConfig()
        config.load_json(str(model_dir / "config.json"))
        model = Xtts.init_from_config(config)
        load_parameters = inspect.signature(model.load_checkpoint).parameters
        load_kwargs = {"eval": True, "use_deepspeed": False}
        if "checkpoint_path" in load_parameters:
            load_kwargs.update(
                checkpoint_path=str(model_dir / "best_model.pth"),
                vocab_path=str(model_dir / "vocab.json"),
            )
            model.load_checkpoint(config, **load_kwargs)
        else:
            model.load_checkpoint(config, checkpoint_dir=str(model_dir), **load_kwargs)
        use_cuda = self.prefer_cuda and torch.cuda.is_available()
        if use_cuda:
            try:
                free_bytes, _total_bytes = torch.cuda.mem_get_info()
                if free_bytes < 3 * 1024**3:
                    logging.getLogger("hal9000.tts").warning(
                        "XTTS is using CPU because CUDA has only %.1f GiB free",
                        free_bytes / 1024**3,
                    )
                    use_cuda = False
            except RuntimeError as exc:
                logging.getLogger("hal9000.tts").warning(
                    "XTTS could not inspect CUDA memory; using CPU: %s", exc
                )
                use_cuda = False
        device = "cuda" if use_cuda else "cpu"
        model.to(device)
        reference = model_dir / "HAL9000_XTTSV2_FT.wav"
        if not reference.exists():
            reference = model_dir / "HAL9000_Voice_noise_reduced-enhanced-85p.wav"
        conditioning = model.get_conditioning_latents(audio_path=[str(reference)])
        self._config = config
        self._model = model
        self._conditioning = conditioning
        self._backend = "CUDA" if use_cuda else "CPU"
        if progress:
            progress("ready", 1.0)

    def synthesize(self, text: str, rate: float = 1.0) -> AudioBuffer:
        if self._model is None or self._config is None or self._conditioning is None:
            self.initialize()
        gpt_cond_latent, speaker_embedding = self._conditioning
        speed = max(0.5, min(2.0, rate))
        output = self._model.inference(
            text=text,
            language="en",
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
            temperature=0.65,
            length_penalty=1.0,
            repetition_penalty=4.5,
            top_k=50,
            top_p=0.85,
            speed=speed,
            # HAL already streams sentence-sized chunks. Coqui's secondary
            # splitter adds latency and pulls in optional language packages.
            enable_text_splitting=False,
        )
        samples = np.asarray(output.get("wav"), dtype=np.float32).reshape(-1)
        sample_rate = int(getattr(self._config.audio, "output_sample_rate", 24_000))
        buffer = AudioBuffer(samples, sample_rate, self.name)
        buffer.validate()
        return buffer

    def unload(self) -> None:
        self._model = None
        self._config = None
        self._conditioning = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            return
