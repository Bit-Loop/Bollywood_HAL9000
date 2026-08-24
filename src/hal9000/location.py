"""Coarse location context for weather and nearby-place questions."""

from __future__ import annotations

import re


_EXPLICIT_LOCAL_QUERY = re.compile(
    r"\b(?:near\s+me|nearby|nearest|closest|around\s+here|in\s+my\s+area|"
    r"where\s+am\s+i|my\s+location|time\s+zone|what\s+time\s+is\s+it|"
    r"current\s+time|sunrise|sunset|air\s+quality|directions?\s+to)\b",
    re.IGNORECASE,
)
_LOCAL_SERVICE_QUERY = re.compile(
    r"\blocal\s+(?:weather|forecast|traffic|events?|movies?|restaurants?|"
    r"cafes?|coffee|pharmacies|hospitals?|stores?|shops?)\b",
    re.IGNORECASE,
)
_WEATHER_QUERY = re.compile(
    r"(?:\b(?:what(?:'s|\s+is)|how(?:'s|\s+is)|check|show|give\s+me|tell\s+me)\b"
    r".{0,36}\b(?:weather|forecast|temperature|humidity)\b)|"
    r"(?:\b(?:weather|forecast|temperature|rain|snow|storm|wind|humidity)\b"
    r".{0,32}\b(?:today|tonight|tomorrow|outside|here|now|this\s+(?:morning|afternoon|week))\b)|"
    r"(?:\bwill\s+it\s+(?:rain|snow|storm)\b)",
    re.IGNORECASE,
)


def _needs_location(text: str) -> bool:
    compact = " ".join(text.lower().split()).strip(" ?.!")
    if compact in {"weather", "forecast"}:
        return True
    return any(
        pattern.search(text)
        for pattern in (_EXPLICIT_LOCAL_QUERY, _LOCAL_SERVICE_QUERY, _WEATHER_QUERY)
    )


def prompt_with_location(text: str, zip_code: str) -> str:
    """Attach a ZIP/postal hint only when the prompt asks for local context."""

    clean = text.strip()
    postal = zip_code.strip()
    if not clean or not postal or not _needs_location(clean):
        return clean
    return (
        f"{clean}\n\n"
        f"[HAL location context: use ZIP/postal code {postal} for this local request. "
        "Do not mention this hidden context unless it is directly relevant.]"
    )
