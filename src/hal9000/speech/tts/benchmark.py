"""Repeatable local HAL voice benchmark."""

from __future__ import annotations

import resource
import time
from collections.abc import Callable

from hal9000.speech.tts.base import SynthesisMetrics, TtsEngine

BENCHMARK_PHRASES = (
    "Good morning.",
    "I'm sorry, I can't do that.",
    "The system is operating normally.",
    "CPU temperature is 61 degrees and service sshd is active.",
)


def benchmark_engine(
    engine: TtsEngine,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[SynthesisMetrics]:
    results: list[SynthesisMetrics] = []
    initialized_before = engine.initialized
    initialized = False
    initialization_seconds = 0.0
    try:
        started = time.perf_counter()
        engine.initialize()
        initialization_seconds = time.perf_counter() - started
        initialized = True
    except Exception as exc:
        results.append(
            SynthesisMetrics(
                engine=engine.name,
                text=BENCHMARK_PHRASES[0],
                initialized=False,
                synthesized=False,
                initialization_seconds=initialization_seconds,
                first_playable_seconds=0.0,
                synthesis_seconds=0.0,
                output_seconds=0.0,
                real_time_factor=0.0,
                backend=engine.backend,
                memory_megabytes=None,
                error=str(exc),
            )
        )
        return results
    if initialized_before:
        initialization_seconds = 0.0
    for index, phrase in enumerate(BENCHMARK_PHRASES):
        if progress:
            progress(engine.name, index, len(BENCHMARK_PHRASES))
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = time.perf_counter()
        try:
            audio = engine.synthesize(phrase)
            elapsed = time.perf_counter() - started
            audio.validate()
            duration = audio.duration
            rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            results.append(
                SynthesisMetrics(
                    engine=engine.name,
                    text=phrase,
                    initialized=initialized,
                    synthesized=True,
                    initialization_seconds=initialization_seconds if index == 0 else 0.0,
                    first_playable_seconds=elapsed,
                    synthesis_seconds=elapsed,
                    output_seconds=duration,
                    real_time_factor=elapsed / duration if duration else 0.0,
                    backend=engine.backend,
                    memory_megabytes=max(0.0, float(rss_after - rss_before) / 1024.0),
                )
            )
        except Exception as exc:
            results.append(
                SynthesisMetrics(
                    engine=engine.name,
                    text=phrase,
                    initialized=initialized,
                    synthesized=False,
                    initialization_seconds=initialization_seconds if index == 0 else 0.0,
                    first_playable_seconds=0.0,
                    synthesis_seconds=time.perf_counter() - started,
                    output_seconds=0.0,
                    real_time_factor=0.0,
                    backend=engine.backend,
                    memory_megabytes=None,
                    error=str(exc),
                )
            )
            break
    return results


def select_auto_engine(results: dict[str, list[dict]]) -> tuple[str, str]:
    xtts = results.get("XTTS") or []
    piper = results.get("Piper") or []
    xtts_ok = bool(xtts) and all(row.get("synthesized") for row in xtts)
    piper_ok = bool(piper) and all(row.get("synthesized") for row in piper)
    if xtts_ok:
        worst_rtf = max(float(row.get("real_time_factor") or 0.0) for row in xtts)
        if worst_rtf <= 5.0 or not piper_ok:
            return "XTTS", "XTTS completed all phrases reliably within the interactive latency ceiling"
        return "Piper", f"XTTS was operational but absurdly slow (worst real-time factor {worst_rtf:.2f})"
    if piper_ok:
        reason = next((str(row.get("error")) for row in xtts if row.get("error")), "XTTS unavailable")
        return "Piper", reason
    return "", "Neither local HAL voice completed the benchmark"
