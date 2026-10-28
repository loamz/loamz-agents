"""Notes as an append-only record. A note is never changed or removed: replacing it adds a new note that names it, and finishing, retiring or
dismissing it adds an event. What is current is worked out from those records. Every time here is taken by the server in the same step as the
write; no model supplies one.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from loamz.engine.errors import ConflictError
from loamz.engine.sql import Database, Row, Tx, migrate
from loamz.runtime.kernel.types.ids import new_id
from loamz.server.agents.features.conversations.memory.model import NoteEventOut, NoteOut

COMPONENT = "conversations.memory"
SCRIPTS = [
    # The first design (notes that could be edited) never left development: replaced, not migrated.
    """
    CREATE TABLE IF NOT EXISTS conversation_state (
        tenant_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        notes_through INTEGER NOT NULL DEFAULT 0,
        idle_through INTEGER NOT NULL DEFAULT 0,
        claimed_at DOUBLE PRECISION,
        PRIMARY KEY (tenant_id, session_id)
    );
    """,
    """
    DROP TABLE IF EXISTS conversation_notes;
    CREATE TABLE conversation_notes (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        source TEXT NOT NULL,
        basis TEXT NOT NULL,
        written_by TEXT NOT NULL,
        effective_at DOUBLE PRECISION NOT NULL,
        supersedes_id TEXT,
        PRIMARY KEY (tenant_id, id)
    );
    CREATE INDEX conversation_notes_session_idx ON conversation_notes (tenant_id, session_id, effective_at);
    CREATE TABLE conversation_note_events (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        note_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        event TEXT NOT NULL,
        by TEXT NOT NULL,
        reason TEXT NOT NULL,
        related_id TEXT,
        at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, id)
    );
    CREATE UNIQUE INDEX conversation_note_events_order_idx ON conversation_note_events (tenant_id, note_id, seq);
    CREATE INDEX conversation_note_events_session_idx ON conversation_note_events (tenant_id, session_id);
    """,
    # The person's time zone when each was recorded, beside the exact moment (which is UTC).
    """
    ALTER TABLE conversation_notes ADD COLUMN zone TEXT NOT NULL DEFAULT 'UTC';
    ALTER TABLE conversation_note_events ADD COLUMN zone TEXT NOT NULL DEFAULT 'UTC';
    """,
]

_N = "id, session_id, kind, text, source, basis, written_by, effective_at, zone, supersedes_id"
_E = "note_id, seq, event, by, reason, related_id, at, zone"

# What an event may follow: a note that has been replaced is final; the rest can be taken back into use by `reopened`.
_ALLOWED: dict[str, set[str]] = {
    "active": {"done", "retired", "dismissed", "superseded", "pinned", "unpinned"},
    "done": {"reopened", "retired", "pinned", "unpinned"},
    "retired": {"reopened", "pinned", "unpinned"},
    "dismissed": {"reopened"},
    "superseded": set(),
}
_NEXT = {
    "done": "done",
    "retired": "retired",
    "dismissed": "dismissed",
    "superseded": "superseded",
    "reopened": "active",
}


@dataclass(frozen=True)
class State:
    notes_through: int = 0
    idle_through: int = 0


def _at(epoch: float):  # noqa: ANN202
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC)


def _fold(row: Row, events: list[Row]) -> NoteOut:
    """The note as it stands now: its record, then its events in order."""
    status, pinned, ended_at, ended_reason, replaced_by = "active", False, None, None, None
    for e in events:
        kind = e["event"]
        if kind == "pinned":
            pinned = True
        elif kind == "unpinned":
            pinned = False
        elif kind in _NEXT:
            status = _NEXT[kind]
            if kind == "reopened":
                ended_at = ended_reason = replaced_by = None
            elif kind in ("retired", "dismissed", "superseded"):
                ended_at, ended_reason = _at(e["at"]), e["reason"] or None
                if kind == "superseded":
                    replaced_by = e["related_id"]
    return NoteOut(
        id=row["id"], kind=row["kind"], text=row["text"], status=status, pinned=pinned, source=json.loads(row["source"]),
        basis=row["basis"], written_by=row["written_by"], effective_at=_at(row["effective_at"]), zone=row["zone"], ended_at=ended_at,
        ended_reason=ended_reason, supersedes_id=row["supersedes_id"], replaced_by=replaced_by,
    )  # type: ignore[arg-type]  # fmt: skip


class MemoryStore:
    """Notes and the per-conversation memory state. Every statement is keyed by the tenant, and a person's by the user too."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _tx(self):  # noqa: ANN202
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True
        return self._db.transaction()

    # ── reading ─────────────────────────────────────────────────────────────
    async def _events(self, tx: Tx, tenant: str, where: str, *args: object) -> dict[str, list[Row]]:
        rows = await tx.fetchall(
            f"SELECT {_E} FROM conversation_note_events WHERE tenant_id = ? AND {where} ORDER BY note_id, seq",
            tenant,
            *args,
        )
        out: dict[str, list[Row]] = {}
        for r in rows:
            out.setdefault(r["note_id"], []).append(r)
        return out

    async def list(
        self, tenant: str, user: str, session: str, statuses: tuple[str, ...] | None = None
    ) -> list[NoteOut]:
        async with await self._tx() as tx:
            rows = await tx.fetchall(
                f"SELECT {_N} FROM conversation_notes WHERE tenant_id = ? AND user_id = ? AND session_id = ? ORDER BY effective_at, id",
                tenant, user, session,
            )  # fmt: skip
            events = await self._events(tx, tenant, "session_id = ?", session)
        notes = [_fold(r, events.get(r["id"], [])) for r in rows]
        notes.sort(key=lambda n: (not n.pinned, n.effective_at))
        return [n for n in notes if statuses is None or n.status in statuses]

    async def get(self, tenant: str, user: str, note_id: str) -> tuple[NoteOut, str] | None:
        """The note as it stands, and the conversation it belongs to; None when it is not this person's."""
        async with await self._tx() as tx:
            row = await tx.fetchone(
                f"SELECT {_N} FROM conversation_notes WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant,
                user,
                note_id,
            )
            if row is None:
                return None
            events = await self._events(tx, tenant, "note_id = ?", note_id)
        return _fold(row, events.get(note_id, [])), row["session_id"]

    async def history(self, tenant: str, user: str, note_id: str) -> list[NoteEventOut]:
        async with await self._tx() as tx:
            owned = await tx.fetchone(
                "SELECT 1 AS x FROM conversation_notes WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant,
                user,
                note_id,
            )
            if owned is None:
                return []
            events = (await self._events(tx, tenant, "note_id = ?", note_id)).get(note_id, [])
        return [
            NoteEventOut(
                event=e["event"],
                at=_at(e["at"]),
                zone=e["zone"],
                by=e["by"],
                reason=e["reason"],
                related_id=e["related_id"],
            )  # type: ignore[arg-type]
            for e in events
        ]

    # ── writing: only ever appending ────────────────────────────────────────
    async def _append(
        self,
        tx: Tx,
        tenant: str,
        session: str,
        note_id: str,
        event: str,
        by: str,
        reason: str,
        related: str | None,
        at: float,
        zone: str,
    ) -> None:
        row = await tx.fetchone(
            f"SELECT {_N} FROM conversation_notes WHERE tenant_id = ? AND id = ?", tenant, note_id
        )
        events = (await self._events(tx, tenant, "note_id = ?", note_id)).get(note_id, [])
        if row is None:
            raise ConflictError("That note does not exist.")
        current = _fold(row, events).status
        if event not in _ALLOWED[current]:
            raise ConflictError(f"That note is {current}; it cannot be {event} now.")
        await tx.execute(
            "INSERT INTO conversation_note_events (tenant_id, id, session_id, note_id, seq, event, by, reason, related_id, at, zone) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tenant, new_id(), session, note_id, len(events) + 1, event, by, reason[:200], related, at, zone,
        )  # fmt: skip

    async def add(
        self, tenant: str, user: str, session: str, kind: str, text: str, source: list[int], written_by: str,
        *, basis: str = "stated", supersedes: str | None = None, reason: str = "", zone: str = "UTC",
    ) -> NoteOut:  # fmt: skip
        """Record a note. With `supersedes`, the note it replaces is closed in the same step, and only if it is still current: a stale
        proposal (the note was already replaced or retired) is refused."""
        now = time.time()
        note_id = new_id()
        async with await self._tx() as tx:
            if supersedes is not None:
                old = await tx.fetchone(
                    "SELECT session_id FROM conversation_notes WHERE tenant_id = ? AND user_id = ? AND id = ?",
                    tenant,
                    user,
                    supersedes,
                )
                if old is None or old["session_id"] != session:
                    raise ConflictError("That note is not in this conversation.")
                await self._append(
                    tx,
                    tenant,
                    session,
                    supersedes,
                    "superseded",
                    written_by,
                    reason,
                    note_id,
                    now,
                    zone,
                )
            await tx.execute(
                "INSERT INTO conversation_notes (tenant_id, id, user_id, session_id, kind, text, source, basis, written_by, effective_at, zone, supersedes_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tenant, note_id, user, session, kind, text, json.dumps(source), basis, written_by, now, zone, supersedes,
            )  # fmt: skip
            row = await tx.fetchone(
                f"SELECT {_N} FROM conversation_notes WHERE tenant_id = ? AND id = ?",
                tenant,
                note_id,
            )
        assert row is not None
        return _fold(row, [])

    async def event(
        self,
        tenant: str,
        user: str,
        note_id: str,
        event: str,
        by: str,
        reason: str = "",
        zone: str = "UTC",
    ) -> NoteOut:
        """Finish, retire, dismiss, reopen, pin or unpin a note, recorded with the server's time. ConflictError when it cannot happen now."""
        now = time.time()
        async with await self._tx() as tx:
            row = await tx.fetchone(
                "SELECT session_id FROM conversation_notes WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant,
                user,
                note_id,
            )
            if row is None:
                raise ConflictError("That note does not exist.")
            await self._append(
                tx, tenant, row["session_id"], note_id, event, by, reason, None, now, zone
            )
        found = await self.get(tenant, user, note_id)
        assert found is not None
        return found[0]

    # ── per-conversation state ──────────────────────────────────────────────
    async def state(self, tenant: str, session: str) -> State:
        async with await self._tx() as tx:
            row = await tx.fetchone(
                "SELECT notes_through, idle_through FROM conversation_state WHERE tenant_id = ? AND session_id = ?",
                tenant,
                session,
            )
        return State(row["notes_through"], row["idle_through"]) if row else State()

    async def set_state(self, tenant: str, session: str, **values: int) -> None:
        async with await self._tx() as tx:
            await tx.execute(
                "INSERT INTO conversation_state (tenant_id, session_id) VALUES (?, ?) ON CONFLICT (tenant_id, session_id) DO NOTHING",
                tenant, session,
            )  # fmt: skip
            for key, value in values.items():
                if key not in ("notes_through", "idle_through"):
                    raise ValueError("Unknown state field")
                await tx.execute(
                    f"UPDATE conversation_state SET {key} = ? WHERE tenant_id = ? AND session_id = ?",
                    value,
                    tenant,
                    session,
                )

    async def claim(self, tenant: str, session: str, now: float, stale_after: float) -> bool:
        """Take the conversation for one background pass, unless another pass took it within `stale_after` seconds."""
        async with await self._tx() as tx:
            await tx.execute(
                "INSERT INTO conversation_state (tenant_id, session_id) VALUES (?, ?) ON CONFLICT (tenant_id, session_id) DO NOTHING",
                tenant, session,
            )  # fmt: skip
            taken = await tx.execute(
                "UPDATE conversation_state SET claimed_at = ? WHERE tenant_id = ? AND session_id = ? AND (claimed_at IS NULL OR claimed_at < ?)",
                now, tenant, session, now - stale_after,
            )  # fmt: skip
        return bool(taken)

    async def erase(self, tenant: str, user: str | None = None) -> int:
        """Privacy erasure: the one way notes and their history are removed."""
        async with await self._tx() as tx:
            if user is None:
                for table in ("conversation_note_events", "conversation_state"):
                    await tx.execute(f"DELETE FROM {table} WHERE tenant_id = ?", tenant)
                return await tx.execute(
                    "DELETE FROM conversation_notes WHERE tenant_id = ?", tenant
                )
            sessions = "(SELECT DISTINCT session_id FROM conversation_notes WHERE tenant_id = ? AND user_id = ?)"
            for table in ("conversation_note_events", "conversation_state"):
                await tx.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ? AND session_id IN {sessions}",
                    tenant,
                    tenant,
                    user,
                )
            return await tx.execute(
                "DELETE FROM conversation_notes WHERE tenant_id = ? AND user_id = ?", tenant, user
            )
