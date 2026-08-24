"""HAL XTTS and Piper speech synthesis engines."""

from hal9000.speech.tts.base import AudioBuffer, SynthesisMetrics
from hal9000.speech.tts.manager import TtsManager

__all__ = ["AudioBuffer", "SynthesisMetrics", "TtsManager"]
