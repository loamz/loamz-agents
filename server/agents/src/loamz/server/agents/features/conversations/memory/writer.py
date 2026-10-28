"""The notes writer: reads what was said since it last looked and keeps the conversation's notes up to date, in the background."""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from loamz.runtime.kernel.models import ChatModel, GenerationOptions
from loamz.runtime.kernel.types.content import ChatMessage, Role, TextBlock
from loamz.server.agents.features.conversations.memory.model import (
    KINDS,
    MAX_NOTE_CHARS,
    MAX_REASON_CHARS,
    NoteOut,
)

logger = logging.getLogger(__name__)

SYSTEM = f"""You keep the notes of a long conversation between a person and an AI agent: the few things that must not be forgotten, so \
anyone picking the conversation up later can see them at a glance. You are given the notes so far (with ids) and new messages (with numbers). \
Answer with one JSON object and nothing else:
{{"add": [{{"kind": "...", "text": "...", "source": [12], "basis": "stated"}}],
  "supersede": [{{"id": "...", "kind": "...", "text": "...", "source": [15], "basis": "stated", "reason": "..."}}],
  "retire": [{{"id": "...", "reason": "..."}}],
  "done": ["id"]}}
Kinds: instruction (what the person wants done always or never, their preferences), decision, fact (names, numbers, amounts, references, addresses, \
exactly as said), task (something still to do; later `done` when finished), date (deadlines, appointments), file (a file shared or made, and what is in it), \
person (who is who).
Rules:
- Notes are a permanent record. You can never delete or edit one. To correct a note, `supersede` it with the new text and a short reason; to say a note no \
longer applies without replacing it (a workaround that is no longer needed, a plan that was dropped), `retire` it with a reason. The system records when.
- Never write any date or time of your own for a note. Dates inside a note's text are only dates the conversation talks about.
- `basis` is "stated" when the person said it, "inferred" when you are concluding it from what was said. Prefer stating only what was said.
- Write each note as one short plain sentence (at most {MAX_NOTE_CHARS} characters) in the language of the conversation, copying exact values. `source` is the \
numbers of the messages it came from.
- Do not repeat a note that already exists, and never write one listed as dismissed. Greetings, filler and passing remarks are not notes. Most passes add \
nothing or only a few notes: when nothing is worth noting, answer {{}}."""


class _Add(BaseModel):
    kind: str
    text: str
    source: list[int] = Field(default_factory=list)
    basis: str = "stated"


class _Supersede(_Add):
    id: str
    reason: str = ""


class _Retire(BaseModel):
    id: str
    reason: str = ""


class Changes(BaseModel):
    """What the writer proposes. It has no time, status or delete: the application checks each proposal and records it."""

    add: list[_Add] = Field(default_factory=list)
    supersede: list[_Supersede] = Field(default_factory=list)
    retire: list[_Retire] = Field(default_factory=list)
    done: list[str] = Field(default_factory=list)

    def clean(self) -> Changes:
        """Only known kinds, short plain text, at most a handful per pass."""

        def fit(item):  # noqa: ANN001, ANN202
            text = " ".join(item.text.split())[:MAX_NOTE_CHARS]
            if not text or item.kind not in KINDS:
                return None
            basis = item.basis if item.basis in ("stated", "inferred") else "stated"
            update = {
                "text": text,
                "basis": basis,
                "source": sorted({n for n in item.source if n > 0})[:10],
            }
            if hasattr(item, "reason"):
                update["reason"] = " ".join(item.reason.split())[:MAX_REASON_CHARS]
            return item.model_copy(update=update)

        def reason(item: _Retire) -> _Retire:
            return item.model_copy(
                update={"reason": " ".join(item.reason.split())[:MAX_REASON_CHARS]}
            )

        return Changes(
            add=[x for x in map(fit, self.add) if x][:15],
            supersede=[x for x in map(fit, self.supersede) if x][:15],
            retire=[reason(r) for r in self.retire][:15],
            done=self.done[:15],
        )


def _json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    return (fenced.group(1) if fenced else text).strip()


class NotesWriter:
    def __init__(self, model: ChatModel) -> None:
        self._model = model

    async def run(self, notes: list[NoteOut], dismissed: list[str], lines: list[str]) -> Changes:
        """What to change in `notes` given the new `lines` (`#12 person: …`). Malformed answers change nothing."""
        known = (
            "\n".join(f"[{n.id}] ({n.kind}, {n.status}) {n.text}" for n in notes) or "(none yet)"
        )
        gone = "\n".join(f"- {t}" for t in dismissed[-30:]) or "(none)"
        prompt = (
            f"Notes so far:\n{known}\n\nDismissed by the person (never write these again):\n{gone}\n\nNew messages:\n"
            + "\n".join(lines)
        )
        response = await self._model.generate(
            [ChatMessage(role=Role.USER, content=(TextBlock(text=prompt),))],
            options=GenerationOptions(system_instructions=SYSTEM, temperature=0.1),
        )
        try:
            return Changes.model_validate(json.loads(_json(response.text) or "{}")).clean()
        except ValueError, ValidationError:
            logger.warning("The notes writer's answer was not usable; no notes changed")
            return Changes()
