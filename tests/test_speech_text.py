from __future__ import annotations

from hal9000.speech.text import SpeechChunker, markdown_to_speech


def test_markdown_to_speech_removes_formatting_urls_and_code_blocks() -> None:
    source = """## **Status**

* The **primary** service is named `system_health`.
* Read the [operator guide](https://example.com/guide), not https://example.com/raw.
* Do not read https://en.wikipedia.org/wiki/Function_(mathematics), ever.
* Never say `rm -rf /tmp/cache` aloud.

```python
print("this code must not be spoken")
```

Load is 42% & stable.
"""

    spoken = markdown_to_speech(source)

    assert spoken == (
        "Status. The primary service is named. "
        "Read the operator guide, not. Do not read, ever. Never say aloud. "
        "Load is 42 percent and stable."
    )
    assert "asterisk" not in spoken.lower()
    assert "http" not in spoken.lower()
    assert "wikipedia" not in spoken.lower()
    assert "mathematics" not in spoken.lower()
    assert "tmp" not in spoken.lower()
    assert "cache" not in spoken.lower()
    assert "print" not in spoken.lower()
    assert not any(symbol in spoken for symbol in "*_`#[]{}|")


def test_speech_chunker_releases_complete_sentences_while_streaming() -> None:
    chunker = SpeechChunker()

    assert chunker.feed("**Good") == []
    assert chunker.feed(" morning.** The system is") == ["Good morning."]
    assert chunker.feed(" operating normally! Remaining") == [
        "The system is operating normally!"
    ]
    assert chunker.finish() == ["Remaining."]


def test_speech_chunker_does_not_read_fenced_code_or_split_decimals() -> None:
    chunker = SpeechChunker()

    assert chunker.feed("Version 3.14 is ready. Here is the probe:\n```sh\n") == [
        "Version 3.14 is ready.",
        "Here is the probe.",
    ]
    assert chunker.feed("printf '* raw *'\n```\nAll clear.") == ["All clear."]
    assert chunker.finish() == []


def test_speech_chunker_releases_temperature_units_without_waiting_for_completion() -> None:
    chunker = SpeechChunker()

    assert chunker.feed("It is 72°F.") == ["It is 72 degrees F."]
    assert chunker.finish() == []


def test_markdown_to_speech_drops_indented_code_and_unterminated_urls() -> None:
    source = """Use the safe explanation.

    curl https://api.example.com/v1/items?id=42

Reference: https://example.com/a_(b)?x=1&y=2
Done.
"""

    spoken = markdown_to_speech(source)

    assert spoken == "Use the safe explanation. Reference. Done."
    assert "curl" not in spoken.lower()
    assert "example" not in spoken.lower()

    nested_link = markdown_to_speech(
        "See [math reference](https://en.wikipedia.org/wiki/Function_(mathematics))."
    )
    assert nested_link == "See math reference."
    assert "(" not in nested_link and ")" not in nested_link


def test_speech_chunker_releases_short_phrases_before_large_blocks_accumulate() -> None:
    chunker = SpeechChunker()
    long_thought = " ".join(["measured"] * 24)

    chunks = chunker.feed(long_thought)

    assert chunks
    assert len(chunks[0]) <= 180
