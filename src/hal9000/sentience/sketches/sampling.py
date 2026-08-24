"""Deterministic bounded representative samples."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from hal9000.sentience.models import Severity

_SEVERITY_RANK = {
    Severity.DEBUG: 0,
    Severity.INFO: 1,
    Severity.NOTICE: 2,
    Severity.WARNING: 3,
    Severity.ERROR: 4,
    Severity.CRITICAL: 5,
}


@dataclass(frozen=True, slots=True)
class RepresentativeSample:
    kind: str
    ordinal: int
    observed_at: str
    severity: Severity
    value: Any


class BoundedRepresentativeSampler:
    """First/latest/highest plus bottom-k deterministic uniform exemplars."""

    def __init__(self, uniform_capacity: int, namespace: str) -> None:
        if uniform_capacity < 0:
            raise ValueError("sample capacity must not be negative")
        self.capacity = uniform_capacity
        self.namespace = namespace
        self.count = 0
        self.first: RepresentativeSample | None = None
        self.latest: RepresentativeSample | None = None
        self.highest: RepresentativeSample | None = None
        self._uniform: list[tuple[int, RepresentativeSample]] = []

    def update(self, value: Any, observed_at: str, severity: Severity) -> None:
        ordinal = self.count
        self.count += 1
        sample = RepresentativeSample("uniform", ordinal, observed_at, Severity(severity), value)
        if self.first is None:
            self.first = RepresentativeSample("first", ordinal, observed_at, Severity(severity), value)
        self.latest = RepresentativeSample("latest", ordinal, observed_at, Severity(severity), value)
        if self.highest is None or _SEVERITY_RANK[Severity(severity)] > _SEVERITY_RANK[self.highest.severity]:
            self.highest = RepresentativeSample("highest", ordinal, observed_at, Severity(severity), value)
        if self.capacity == 0:
            return
        score = int.from_bytes(
            hashlib.blake2b(
                f"{self.namespace}:{ordinal}".encode(), digest_size=8
            ).digest(),
            "big",
        )
        self._uniform.append((score, sample))
        self._uniform.sort(key=lambda item: item[0])
        del self._uniform[self.capacity :]

    def samples(self) -> tuple[RepresentativeSample, ...]:
        fixed = [sample for sample in (self.first, self.latest, self.highest) if sample is not None]
        uniform = [sample for _score, sample in self._uniform]
        return tuple(fixed + uniform)

    def restore(
        self,
        total_count: int,
        samples: tuple[RepresentativeSample, ...],
    ) -> None:
        """Hydrate bounded committed exemplars after a process restart."""

        self.count = max(0, int(total_count))
        self.first = next((item for item in samples if item.kind == "first"), None)
        self.latest = next((item for item in samples if item.kind == "latest"), None)
        self.highest = next((item for item in samples if item.kind == "highest"), None)
        uniform = [item for item in samples if item.kind == "uniform"][: self.capacity]
        self._uniform = [
            (
                int.from_bytes(
                    hashlib.blake2b(
                        f"{self.namespace}:{item.ordinal}".encode(), digest_size=8
                    ).digest(),
                    "big",
                ),
                item,
            )
            for item in uniform
        ]
        self._uniform.sort(key=lambda item: item[0])
