"""Conservative model-independent token/byte budget accounting."""

from __future__ import annotations

import math
from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


@dataclass(slots=True)
class TokenBudget:
    requested: int
    used: int = 0

    def __post_init__(self) -> None:
        if self.requested <= 0:
            raise ValueError("token budget must be positive")

    @property
    def remaining(self) -> int:
        return max(0, self.requested - self.used)

    def take(self, text: str, *, fixed_overhead: int = 8) -> tuple[str, bool]:
        available = self.remaining - fixed_overhead
        if available <= 0:
            return "", True
        cost = estimate_tokens(text)
        if cost <= available:
            self.used += cost + fixed_overhead
            return text, False
        byte_limit = max(0, available * 4)
        encoded = text.encode("utf-8")[:byte_limit]
        clipped = encoded.decode("utf-8", errors="ignore").rstrip()
        if clipped:
            clipped += "…"
        self.used += estimate_tokens(clipped) + fixed_overhead
        return clipped, True
