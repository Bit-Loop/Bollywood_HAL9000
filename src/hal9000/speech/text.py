"""Convert streamed assistant Markdown into calm, speakable sentence chunks."""

from __future__ import annotations

import re
import unicodedata


_FENCED_CODE = re.compile(r"```[^\n]*\n?.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_AUTOLINK = re.compile(r"<((?:https?://|mailto:)[^>]+)>", re.IGNORECASE)
_RAW_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_EVIDENCE_HANDLE = re.compile(
    r"\[(?:memory|audio|visual|probe|action|verification|interruption|evidence):[^\]]+\]",
    re.IGNORECASE,
)
_INDENTED_CODE = re.compile(r"(?m)^(?:\t| {4,})\S[^\n]*(?:\n|$)")
_HTML = re.compile(r"<[^>]+>")
_LIST_PREFIX = re.compile(r"(?m)^\s*(?:[-+*•]\s+|\d+[.)]\s+)")
_HEADING_PREFIX = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_QUOTE_PREFIX = re.compile(r"(?m)^\s*>+\s?")
_TABLE_RULE = re.compile(r"(?m)^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$")
_ABBREVIATIONS = {
    "dr",
    "mr",
    "mrs",
    "ms",
    "prof",
    "sr",
    "jr",
    "st",
    "vs",
    "etc",
    "e.g",
    "i.e",
}


def _url_without_noise(match: re.Match[str]) -> str:
    value = match.group(0)
    trailing = ""
    while value and value[-1] in ".,;:!?":
        trailing = value[-1] + trailing
        value = value[:-1]
    return trailing


def markdown_to_speech(text: str) -> str:
    """Return a spoken rendering without Markdown syntax or machine-only text."""

    if not text:
        return ""
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    value = _FENCED_CODE.sub(" ", value)
    value = _INDENTED_CODE.sub(" ", value)
    value = _EVIDENCE_HANDLE.sub(" ", value)
    value = _IMAGE.sub(lambda match: match.group(1), value)
    value = _LINK.sub(lambda match: match.group(1), value)
    value = _AUTOLINK.sub(lambda match: _url_without_noise(match), value)
    value = _RAW_URL.sub(_url_without_noise, value)
    value = _INLINE_CODE.sub(" ", value)
    value = _HTML.sub(" ", value)
    value = _TABLE_RULE.sub(" ", value)
    value = _HEADING_PREFIX.sub("", value)
    value = _QUOTE_PREFIX.sub("", value)
    value = _LIST_PREFIX.sub("", value)

    value = value.replace("&", " and ")
    value = re.sub(r"(?<=\d)\s*%", " percent", value)
    value = value.replace("°", " degrees ")
    value = value.replace("/", " ")
    value = value.replace("_", " ")
    value = value.replace("(", ", ").replace(")", ", ")
    value = re.sub(r"[*~`#$^=+<>\\|{}\[\]]+", "", value)

    lines: list[str] = []
    for raw_line in value.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" -\t")
        if not line:
            continue
        if line.endswith((":", ";")):
            line = line[:-1].rstrip() + "."
        elif line[-1] not in ".,!?":
            line += "."
        lines.append(line)
    value = " ".join(lines)

    # Symbols in the Unicode "symbol" classes are usually emoji, dingbats, or
    # UI glyphs. They carry visual meaning but produce poor literal TTS output.
    value = "".join(
        character
        for character in value
        if not unicodedata.category(character).startswith(("So", "Sk"))
    )
    value = re.sub(r"\s+([,.;!?])", r"\1", value)
    value = re.sub(r"([,;:])\s*([.!?])", r"\2", value)
    value = re.sub(r"([.!?]){2,}", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


class SpeechChunker:
    """Buffer Hermes deltas and release complete speakable thoughts early."""

    def __init__(self, max_chars: int = 160) -> None:
        self.max_chars = max(80, int(max_chars))
        self._buffer = ""

    def reset(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        if delta:
            self._buffer += delta
        chunks: list[str] = []
        while self._buffer:
            boundary = self._find_boundary(self._buffer)
            if boundary <= 0:
                break
            raw, self._buffer = self._buffer[:boundary], self._buffer[boundary:]
            spoken = markdown_to_speech(raw)
            if spoken:
                chunks.append(spoken)
        return chunks

    def finish(self) -> list[str]:
        raw, self._buffer = self._buffer, ""
        spoken = markdown_to_speech(raw)
        return [spoken] if spoken else []

    def _find_boundary(self, value: str) -> int:
        in_fence = False
        last_space = -1
        index = 0
        while index < len(value):
            if value.startswith("```", index):
                in_fence = not in_fence
                index += 3
                continue
            character = value[index]
            if character.isspace() and not in_fence:
                last_space = index
            if in_fence:
                index += 1
                continue

            if character == "\n":
                remainder = value[index + 1 :]
                previous = value[:index].rstrip()
                if remainder.startswith("```") or remainder.startswith("\n"):
                    return index + 1
                if previous.endswith((":", ";")):
                    return index + 1

            if character in ".!?":
                if self._is_decimal(value, index) or self._is_abbreviation(value, index):
                    index += 1
                    continue
                end = index + 1
                while end < len(value) and value[end] in "\"')]}*_~`":
                    end += 1
                if end == len(value) or value[end].isspace():
                    return end

            if index >= self.max_chars and last_space >= self.max_chars // 2:
                return last_space + 1
            index += 1
        return 0

    @staticmethod
    def _is_decimal(value: str, index: int) -> bool:
        return (
            value[index] == "."
            and index > 0
            and index + 1 < len(value)
            and value[index - 1].isdigit()
            and value[index + 1].isdigit()
        )

    @staticmethod
    def _is_abbreviation(value: str, index: int) -> bool:
        if value[index] != ".":
            return False
        prefix = value[:index]
        match = re.search(r"([A-Za-z](?:[A-Za-z.]*)?)$", prefix)
        if not match:
            return False
        token = match.group(1).lower().rstrip(".")
        if token in _ABBREVIATIONS:
            return True
        # A unit such as 72 degrees F ends a sentence; do not mistake its
        # single-letter temperature suffix for a person's initial.
        token_start = match.start(1)
        if token_start > 0 and prefix[token_start - 1] == "°":
            return False
        return len(token) == 1 and token.isalpha()
