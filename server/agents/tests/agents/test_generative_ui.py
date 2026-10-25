"""An agent that lists `generative_ui` is taught to draw UI in its replies (json-render); one that does not is not told."""

from __future__ import annotations

from types import SimpleNamespace

from loamz.server.agents.features.directory.agents.service import (
    GENERATIVE_UI,
    generative_ui_prompt,
    instructions_block,
)


def profile(allowed):
    return SimpleNamespace(
        name="Trader", role="Trades", instructions="Be brief.", allowed_tools=allowed
    )


def test_listing_the_capability_adds_the_ui_prompt_to_the_instructions():
    text = instructions_block(profile(["data_sql", GENERATIVE_UI]))
    assert "```spec" in text and "Metric" in text and "Be brief." in text


def test_an_agent_that_does_not_list_it_is_not_told_how_to_draw_ui():
    assert "```spec" not in instructions_block(profile(["data_sql"]))
    assert "```spec" not in instructions_block(profile(None))  # all tools is not an opt-in to UI


def test_the_prompt_names_the_catalog_the_console_draws():
    prompt = generative_ui_prompt()
    for part in ("Card", "Stack", "Grid", "Table", "Badge", "Progress", "Metric", "Button"):
        assert part in prompt
    assert "className" not in prompt
