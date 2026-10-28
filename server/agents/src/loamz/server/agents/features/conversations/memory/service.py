from __future__ import annotations

import asyncio
import logging
from typing import Any

from loamz.engine.errors import ConflictError, InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.ports.threads import ThreadStore
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.toolkit.conversation_history import flatten, search_messages
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.memory.model import (
    KINDS,
    MAX_ACTIVE_NOTES,
    NoteHistory,
    NoteIn,
    NoteList,
    NoteOut,
    NotePatch,
    SearchHit,
    SearchOut,
    SummaryOut,
)
from loamz.server.agents.features.conversations.memory.store import MemoryStore
from loamz.server.agents.features.conversations.memory.writer import NotesWriter
from loamz.server.agents.features.conversations.threads.service import ThreadsService, threads

logger = logging.getLogger(__name__)

BLOCK_CHARS = 8_000
"""About 2,000 tokens: the notes shown to the agent each turn."""
MIN_NEW = 8
"""Fewest new messages worth a notes pass."""
KEEP_UP_EVERY = 16
"""After a turn, the notes are brought up to date in the background once this many messages are new."""
WRITER_LINES = 60
LINE_CHARS = 500

_HEADINGS = (
    ("instruction", "Instructions"),
    ("task", "Open tasks"),
    ("decision", "Decisions"),
    ("date", "Dates"),
    ("fact", "Facts"),
    ("file", "Files"),
    ("person", "People"),
)


GUIDE = (
    "Keeping track of this conversation: when the person asks you to remember something, or gives an instruction that should hold from now on "
    "(a preference, a rule, a deadline, a decision), keep it with the notes tool (action add) so it is not lost, and say you did. "
    "When a detail from earlier matters and it is not in front of you, look it up with conversation_history instead of guessing."
)


def _local(moment, zone: str):  # noqa: ANN001, ANN202
    """A recorded moment as the day it was in the zone the person had then."""
    from zoneinfo import ZoneInfo

    try:
        return moment.astimezone(ZoneInfo(zone))
    except Exception:  # noqa: BLE001
        return moment


def _who(role: object) -> str:
    return {"user": "person", "assistant": "agent", "tool": "tool"}.get(
        str(getattr(role, "value", role)), ""
    )


class MemoryService:
    def __init__(self, container: Container, store: MemoryStore, threads_: ThreadsService) -> None:
        self._c = container
        self._store = store
        self._threads = threads_
        self._writing: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def keep_up(self, caller: Caller, session_id: str, model: str | None) -> None:
        """After a turn: if enough was said since the notes were last written, update them in the background (one pass per conversation at a time)."""
        if session_id in self._writing:
            return
        self._writing.add(session_id)

        async def write() -> None:
            try:
                await self.update_notes(
                    caller, session_id, self._c.agent_store.threads, model, min_new=KEEP_UP_EVERY
                )
            except Exception:  # noqa: BLE001 - the chat must not suffer for it; the idle pass tries again
                logger.exception("Keeping the notes up to date failed")
            finally:
                self._writing.discard(session_id)

        task = asyncio.ensure_future(write())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── what a person sees and does ─────────────────────────────────────────
    async def notes(self, caller: Caller, thread_id: str, *, history: bool = False) -> NoteList:
        """The notes in force; with `history` every note ever recorded, replaced and retired ones too (with when and why)."""
        await self._threads.require(caller, thread_id)
        statuses = None if history else ("active", "done")
        return NoteList(
            items=await self._store.list(caller.tenant_id, caller.user_id, thread_id, statuses)
        )

    async def add(self, caller: Caller, thread_id: str, body: NoteIn) -> NoteOut:
        await self._threads.require(caller, thread_id)
        return await self._add_checked(caller, thread_id, body.kind, body.text, [], "person")

    async def _zone(self, caller: Caller) -> str:
        """The zone to note beside a record. Never a reason to lose a note: UTC if the preference cannot be read."""
        from loamz.server.agents.features.people.preferences.service import preferences

        try:
            return await self._c.get(preferences).zone(caller)
        except Exception:  # noqa: BLE001
            return "UTC"

    async def _mine(self, caller: Caller, note_id: str) -> tuple[NoteOut, str]:
        found = await self._store.get(caller.tenant_id, caller.user_id, note_id)
        if found is None:
            raise NotFoundError("Note not found")
        return found

    async def patch(self, caller: Caller, note_id: str, body: NotePatch) -> NoteOut:
        """A person's change. New text records a new note that replaces the old one; finishing, retiring or dismissing records an event.
        Nothing is overwritten, and the time is the server's."""
        note, session = await self._mine(caller, note_id)
        who, reason = (caller.tenant_id, caller.user_id), (body.reason or "").strip()
        zone = await self._zone(caller)
        try:
            if body.text is not None and " ".join(body.text.split()) != note.text:
                note = await self._add_checked(
                    caller,
                    session,
                    note.kind,
                    body.text,
                    note.source,
                    "person",
                    supersedes=note.id,
                    reason=reason or "Edited by the person",
                )
            if body.status is not None and body.status != note.status:
                event = {
                    "active": "reopened",
                    "done": "done",
                    "retired": "retired",
                    "dismissed": "dismissed",
                }[body.status]
                note = await self._store.event(*who, note.id, event, "person", reason, zone=zone)
            if body.pinned is not None and body.pinned != note.pinned:
                note = await self._store.event(
                    *who, note.id, "pinned" if body.pinned else "unpinned", "person", zone=zone
                )
        except ConflictError as why:
            raise InvalidRequestError(str(why)) from why
        return note

    async def history(self, caller: Caller, note_id: str) -> NoteHistory:
        """Everything that happened to a note, in order, with when."""
        note, _ = await self._mine(caller, note_id)
        return NoteHistory(
            note=note, events=await self._store.history(caller.tenant_id, caller.user_id, note_id)
        )

    async def search(
        self,
        caller: Caller,
        thread_id: str,
        q: str,
        *,
        from_date: str = "",
        to_date: str = "",
        who: str = "",
        limit: int = 30,
    ) -> SearchOut:
        """Everything ever said in the conversation (the originals, not the summary), newest matches first."""
        await self._threads.require(caller, thread_id)
        if not q.strip() and not (from_date or to_date):
            raise InvalidRequestError("Say what to search for, or give a date range.")
        nodes = await self._c.agent_store.threads.session_nodes(thread_id)
        hits = search_messages(nodes, q, from_date=from_date, to_date=to_date, who=who)
        shown = list(reversed(hits))[: max(1, min(limit, 100))]
        return SearchOut(items=[SearchHit(**h) for h in shown], total=len(hits))  # type: ignore[arg-type]

    async def summary(self, caller: Caller, thread_id: str) -> SummaryOut | None:
        """The latest summary of the conversation, or None while the agent still has every message in front of it."""
        await self._threads.require(caller, thread_id)
        history = self._c.agent_store.threads
        checkpoints = await history.list_checkpoints(thread_id)
        if not checkpoints:
            return None
        latest = max(checkpoints, key=lambda c: c.created_at)
        return SummaryOut(
            covers=int(latest.state.get("covers_to_message", 0)),
            total=len(await history.session_nodes(thread_id)),
            text=latest.summary,
            at=latest.created_at,
            rebuilt_from_originals=bool(latest.state.get("rebuilt_from_originals")),
        )

    # ── what an agent gets ──────────────────────────────────────────────────
    async def block(self, caller: Caller, session_id: str) -> str:
        """The conversation's notes as a short block for the agent's instructions (empty when there are none)."""
        notes = await self._store.list(
            caller.tenant_id, caller.user_id, session_id, ("active", "done")
        )
        live = [n for n in notes if n.status == "active"]
        if not live:
            return ""
        out = ["Notes for this conversation (kept up to date for you; the person can change them):"]
        used = len(out[0])
        for kind, heading in _HEADINGS:
            group = sorted((n for n in live if n.kind == kind), key=lambda n: not n.pinned)
            if not group:
                continue
            lines = [f"{heading}:"]
            for n in group:
                line = (
                    f"- {n.text} (noted {_local(n.effective_at, n.zone):%-d %b %Y})"
                    + (f" [#{', #'.join(str(s) for s in n.source[:3])}]" if n.source else "")
                    + (" [inferred, not stated by the person]" if n.basis == "inferred" else "")
                )
                if used + len(line) > BLOCK_CHARS:
                    lines.append("- (more notes: use the notes tool)")
                    used = BLOCK_CHARS
                    break
                lines.append(line)
                used += len(line) + 1
            out.extend(lines)
            if used >= BLOCK_CHARS:
                break
        return "\n".join(out)

    def tools(self, caller: Caller, session_id: str) -> list[Any]:
        return [NotesTool(self, caller, session_id)]

    def summarizer(
        self, caller: Caller, inner: Any, history: ThreadStore, session_id: str, model: str | None
    ) -> Any:
        """A summarizer that, whenever it writes a summary, also brings the notes up to date."""
        return _WithNotes(self, caller, inner, history, session_id, model)

    # ── the background writer ───────────────────────────────────────────────
    async def update_notes(
        self,
        caller: Caller,
        session_id: str,
        history: ThreadStore,
        model: str | None = None,
        *,
        min_new: int = MIN_NEW,
    ) -> int:
        """Read what was said since the last pass and change the notes; how many changed. Quietly nothing for a short stretch."""
        state = await self._store.state(caller.tenant_id, session_id)
        nodes = await history.session_nodes(session_id)
        if len(nodes) - state.notes_through < min_new:
            return 0
        lines: list[str] = []
        for number, node in enumerate(nodes, start=1):
            if number <= state.notes_through or node.payload.metadata.get("is_checkpoint_summary"):
                continue
            text = " ".join(flatten(node.payload).split())
            if text:
                lines.append(f"#{number} {_who(node.payload.role)}: {text[:LINE_CHARS]}")
        cfg = self._c.settings.context
        writer = NotesWriter(
            self._c.chat_model(cfg.summary_model or model or self._c.settings.llm.model)
        )
        known = await self._store.list(
            caller.tenant_id, caller.user_id, session_id
        )  # all of them, history included
        live = [n for n in known if n.status in ("active", "done")]
        gone = [n.text for n in known if n.status == "dismissed"]  # the person's "never write this"
        changes = await writer.run(live, gone, lines[-WRITER_LINES:])
        by_id = {n.id: n for n in live}
        count = 0
        # The writer only proposes. Each proposal is checked here and recorded with the server's time; one that no longer fits (the note was
        # already replaced or retired meanwhile) or that touches what the person wrote or pinned is not applied.
        for item in changes.supersede:
            old = by_id.get(item.id)
            if old is None or old.pinned or old.written_by == "person" or old.status != "active":
                continue
            try:
                await self._add_checked(
                    caller,
                    session_id,
                    item.kind,
                    item.text,
                    item.source,
                    "writer",
                    basis=item.basis,
                    supersedes=old.id,
                    reason=item.reason,
                )
                count += 1
            except ConflictError, InvalidRequestError:
                continue
        for item in changes.add:
            if any(n.text.casefold() == item.text.casefold() for n in live):
                continue
            try:
                await self._add_checked(
                    caller,
                    session_id,
                    item.kind,
                    item.text,
                    item.source,
                    "writer",
                    basis=item.basis,
                )
                count += 1
            except InvalidRequestError:
                continue
        for note_id in changes.done:
            note = by_id.get(note_id)
            if note is not None and note.kind == "task" and note.status == "active":
                count += await self._try_event(
                    caller, note_id, "done", "writer", "The messages show it finished."
                )
        for item in changes.retire:
            note = by_id.get(item.id)
            if (
                note is not None
                and not note.pinned
                and note.written_by != "person"
                and note.status != "superseded"
            ):
                count += await self._try_event(caller, item.id, "retired", "writer", item.reason)
        await self._store.set_state(caller.tenant_id, session_id, notes_through=len(nodes))
        return count

    async def _add_checked(
        self, caller: Caller, session_id: str, kind: str, text: str, source: list[int], written_by: str,
        *, basis: str = "stated", supersedes: str | None = None, reason: str = "",
    ) -> NoteOut:  # fmt: skip
        if supersedes is None:
            active = await self._store.list(
                caller.tenant_id, caller.user_id, session_id, ("active",)
            )
            if len(active) >= MAX_ACTIVE_NOTES:
                raise InvalidRequestError(
                    f"A conversation keeps at most {MAX_ACTIVE_NOTES} notes in force. Finish or retire some first."
                )
        return await self._store.add(
            caller.tenant_id, caller.user_id, session_id, kind, " ".join(text.split()), source, written_by,
            basis=basis, supersedes=supersedes, reason=reason, zone=await self._zone(caller),
        )  # fmt: skip

    async def _try_event(
        self, caller: Caller, note_id: str, event: str, by: str, reason: str
    ) -> int:
        try:
            await self._store.event(
                caller.tenant_id,
                caller.user_id,
                note_id,
                event,
                by,
                reason,
                zone=await self._zone(caller),
            )
            return 1
        except ConflictError:
            return 0

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


class _WithNotes:
    def __init__(
        self,
        service: MemoryService,
        caller: Caller,
        inner: Any,
        history: ThreadStore,
        session_id: str,
        model: str | None,
    ) -> None:
        self._service, self._caller, self._inner = service, caller, inner
        self._history, self._session, self._model = history, session_id, model

    async def create_checkpoint(self, context: Any) -> Any:
        checkpoint = await self._inner.create_checkpoint(context)
        if checkpoint is not None:
            try:
                await self._service.update_notes(
                    self._caller, self._session, self._history, self._model, min_new=1
                )
            except Exception:  # noqa: BLE001 - the summary is already made; notes can wait for the next pass
                logger.exception("The notes writer failed; the summary is unaffected")
        return checkpoint


class NotesTool:
    """The agent's own hand on the conversation's notes. It can look them up, record a new one, replace one with a corrected note, finish a
    task, or retire a note that no longer applies. It cannot delete or rewrite one, and it never sets a time: the server does."""

    name = "notes"
    risk = ToolRisk.SAFE
    idempotent = False
    description = (
        "The notes of this conversation: its important points (instructions, decisions, facts, tasks, dates). Notes are a permanent record: "
        "nothing is deleted or edited. 'list' shows the notes in force with their ids; 'add' records a new one when the person asks you to "
        "remember something; 'replace' records a corrected version of a note when something changed (the old one stays in the history); "
        "'done' marks a task finished; 'retire' records that a note no longer applies, with the reason."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "add", "replace", "done", "retire"]},
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "For add and replace, and to narrow list.",
            },
            "text": {
                "type": "string",
                "description": "The note, one short sentence. For add and replace.",
            },
            "id": {"type": "string", "description": "A note's id. For replace, done and retire."},
            "reason": {
                "type": "string",
                "description": "Why, in a few words. For replace and retire.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, service: MemoryService, caller: Caller, session_id: str) -> None:
        self._service, self._caller, self._session = service, caller, session_id

    async def execute(
        self,
        *,
        ctx: object = None,
        action: str = "",
        kind: str = "",
        text: str = "",
        id: str = "",
        reason: str = "",
        **_: object,
    ) -> ToolExecutionResult:  # noqa: A002
        service, store, who = self._service, self._service._store, self._caller  # noqa: SLF001
        try:
            if action == "list":
                notes = [
                    n
                    for n in await store.list(
                        who.tenant_id, who.user_id, self._session, ("active", "done")
                    )
                    if not kind or n.kind == kind
                ]
                body = (
                    "\n".join(
                        f"[{n.id}] ({n.kind}{', done' if n.status == 'done' else ''}) {n.text} (noted {_local(n.effective_at, n.zone):%-d %b %Y})"
                        for n in notes
                    )
                    or "No notes yet."
                )
            elif action == "add":
                if kind not in KINDS or not text.strip():
                    return _fail("Give a kind and the note's text.")
                made = await service._add_checked(who, self._session, kind, text, [], "agent")  # noqa: SLF001
                body = f"Noted [{made.id}]."
            else:
                found = await store.get(who.tenant_id, who.user_id, id)
                if found is None or found[1] != self._session:
                    return _fail("There is no such note in this conversation.")
                note = found[0]
                if action == "replace":
                    if not text.strip():
                        return _fail("Give the corrected text.")
                    made = await service._add_checked(
                        who,
                        self._session,
                        kind if kind in KINDS else note.kind,
                        text,
                        [],
                        "agent",
                        supersedes=note.id,
                        reason=reason or "Corrected by the agent",
                    )  # noqa: SLF001
                    body = (
                        f"Replaced. The new note is [{made.id}]; the old one stays in the history."
                    )
                elif action == "done":
                    await store.event(
                        who.tenant_id,
                        who.user_id,
                        id,
                        "done",
                        "agent",
                        reason,
                        zone=await service._zone(who),
                    )  # noqa: SLF001
                    body = "Done."
                elif action == "retire":
                    if note.pinned or note.written_by == "person":
                        return _fail(
                            "The person wrote or pinned that note, so only they can retire it. Tell them if it looks out of date."
                        )
                    await store.event(
                        who.tenant_id,
                        who.user_id,
                        id,
                        "retired",
                        "agent",
                        reason or "No longer applies",
                        zone=await service._zone(who),  # noqa: SLF001
                    )
                    body = "Retired. It stays in the history."
                else:
                    return _fail("Use list, add, replace, done or retire.")
        except (InvalidRequestError, ConflictError) as why:
            return _fail(str(why))
        return ToolExecutionResult(content=[TextBlock(text=body)])


def _fail(text: str) -> ToolExecutionResult:
    return ToolExecutionResult(content=[TextBlock(text=text)], is_error=True)


def memory(container: Container) -> MemoryService:
    return MemoryService(container, MemoryStore(container.database), container.get(threads))


__all__ = ["MemoryService", "NotesTool", "memory"]
