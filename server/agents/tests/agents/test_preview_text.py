"""A one-line preview of a message: a rich page the reply drew is called a card, not shown as its source."""

from loamz.server.agents.features.text import one_line


def test_a_drawn_ui_is_a_card_in_the_preview():
    reply = 'Here is the plan.\n\n```openui\n{"version":"v0.9","createSurface":{}}\n{"version":"v0.9"}\n```\nDo you approve?'
    assert one_line(reply, 140) == "Here is the plan. [card] Do you approve?"


def test_a_fence_still_being_written_is_a_card_too():
    assert one_line('Here:\n```openui\n{"version":"v0.9","crea', 140) == "Here: [card]"


def test_plain_text_is_collapsed_and_cut():
    assert one_line("a   b\n\nc", 140) == "a b c"
    assert one_line("x" * 20, 10) == "x" * 10 + "…"
    assert (
        one_line("a ```python\nprint(1)\n``` b", 140) == "a ```python print(1) ``` b"
    )  # only a UI fence is replaced


def test_a_rich_answer_is_a_card_too():
    assert (
        one_line(
            'Here is the briefing.\n```openui\nroot = Card([a])\na = TextContent("x")\n```', 140
        )
        == "Here is the briefing. [card]"
    )
