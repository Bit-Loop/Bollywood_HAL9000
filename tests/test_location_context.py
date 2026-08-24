from __future__ import annotations

from hal9000.location import prompt_with_location


def test_location_context_is_added_only_to_location_sensitive_prompts() -> None:
    enriched = prompt_with_location("Will it rain this afternoon?", " 60601 ")
    assert enriched.startswith("Will it rain this afternoon?")
    assert "60601" in enriched
    assert "HAL location context" in enriched

    nearby = prompt_with_location("Where is the nearest pharmacy?", "60601")
    assert "60601" in nearby

    assert prompt_with_location("Explain quicksort.", "60601") == "Explain quicksort."
    assert prompt_with_location("Explain local variables.", "60601") == "Explain local variables."
    assert prompt_with_location("What is time complexity?", "60601") == "What is time complexity?"
    assert (
        prompt_with_location("Move the file to a new location.", "60601")
        == "Move the file to a new location."
    )
    assert prompt_with_location("What is nearby?", "") == "What is nearby?"
