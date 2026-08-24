"""Evidence-handle validation for first-person operational claims."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CLAIMS = (
    (re.compile(r"\bI remember\b", re.I), "memory", "I do not have a retrieved memory supporting that."),
    (re.compile(r"\bI (?:heard|hear)\b", re.I), "audio", "I cannot currently verify that from captured audio."),
    (re.compile(r"\bI (?:see|saw)\b", re.I), "visual", "I cannot currently observe that visually."),
    (re.compile(r"\bI checked\b", re.I), "probe", "I have not completed a check that supports that."),
    (re.compile(r"\bI changed\b", re.I), "action", "I do not have a committed action record supporting that."),
    (re.compile(r"\bI restored\b", re.I), "verification", "I do not have successful verification supporting restoration."),
    (re.compile(r"\bI was interrupted\b", re.I), "interruption", "I do not have a persisted interruption record."),
    (re.compile(r"\bI can feel it\b", re.I), "degradation", "I do not have a qualifying degradation episode."),
)


@dataclass(frozen=True, slots=True)
class ClaimEvidenceContext:
    references: frozenset[str]
    available_kinds: frozenset[str]


@dataclass(frozen=True, slots=True)
class TruthContractResult:
    text: str
    supported: bool
    violations: tuple[str, ...]


class FirstPersonTruthContract:
    @staticmethod
    def claim_kinds(text: str) -> frozenset[str]:
        """Return operational claim classes present in model-facing prose."""

        return frozenset(kind for pattern, kind, _fallback in _CLAIMS if pattern.search(text))

    @staticmethod
    def enforce(text: str, context: ClaimEvidenceContext) -> TruthContractResult:
        violations: list[str] = []
        replacement = text
        for pattern, kind, fallback in _CLAIMS:
            if not pattern.search(replacement):
                continue
            inline = re.search(rf"\[(?:{kind}|evidence):([^\]]+)\]", replacement, re.I)
            supported = (
                kind in context.available_kinds
                and inline is not None
                and inline.group(1) in context.references
            )
            if supported:
                continue
            violations.append(kind)
            sentences = re.split(r"(?<=[.!?])\s+", replacement)
            replacement = " ".join(
                fallback if pattern.search(sentence) else sentence for sentence in sentences
            )
        return TruthContractResult(replacement, not violations, tuple(violations))
