"""An agent that lists `rich_ui` is taught to write rich pages (OpenUI) in its replies; one that does not is not told."""

from __future__ import annotations

from types import SimpleNamespace

from loamz.server.agents.features.directory.agents.service import (
    RICH_UI,
    instructions_block,
    rich_ui_prompt,
)


def profile(allowed):
    return SimpleNamespace(
        name="Trader", role="Trades", instructions="Be brief.", allowed_tools=allowed
    )


def test_listing_the_capability_adds_the_openui_prompt_to_the_instructions():
    text = instructions_block(profile(["data_sql", RICH_UI]))
    assert "```openui" in text and "ImageBlock" in text and "Be brief." in text


def test_an_agent_that_does_not_list_it_is_not_told_how_to_write_rich_pages():
    assert "```openui" not in instructions_block(profile(["data_sql"]))
    assert "```openui" not in instructions_block(
        profile(None)
    )  # all tools is not an opt-in to rich pages


def test_the_prompt_is_openuis_with_our_rules():
    prompt = rich_ui_prompt()
    for part in ("Card", "ImageBlock", "Carousel", "BarChart", "FollowUpBlock", "TagBlock"):
        assert part in prompt
    assert "Never invent figures" in prompt and "realistic/plausible data" not in prompt
