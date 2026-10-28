from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from loamz.engine.errors import ConflictError, InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.kernel.types.ids import new_id
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.people.skills.model import (
    MAX_SKILLS_PER_USER,
    SkillIn,
    SkillOut,
    SkillPatch,
    UploadResult,
)
from loamz.server.agents.features.people.skills.parse import SkillRefused, read_upload, scan_text
from loamz.server.agents.features.people.skills.store import SkillStore

PREFIX = "skill:"
"""A person's own skill is an ability of an agent under this prefix, so it never collides with a catalog id."""


class SkillsService:
    """A person's own skills (SKILL.md). The assistant sees each one's name and description; the instructions load when it uses the skill."""

    def __init__(self, store: SkillStore) -> None:
        self._store = store

    async def list(self, caller: Caller) -> list[SkillOut]:
        return await self._store.list(caller.tenant_id, caller.user_id)

    async def get(self, caller: Caller, skill_id: str) -> SkillOut:
        found = await self._store.get(
            caller.tenant_id, caller.user_id, skill_id.removeprefix(PREFIX)
        )
        if found is None:
            raise NotFoundError("That skill does not exist.")
        return found

    async def _room(self, caller: Caller, extra: int = 1) -> None:
        if len(await self.list(caller)) + extra > MAX_SKILLS_PER_USER:
            raise ConflictError(
                f"You can have {MAX_SKILLS_PER_USER} skills. Delete one to add another."
            )

    async def create(self, caller: Caller, body: SkillIn) -> SkillOut:
        await self._room(caller)
        findings = scan_text("instructions", f"{body.description}\n{body.instructions}")
        if findings:
            raise InvalidRequestError(f"This skill {findings[0].problem}.")
        skill = SkillOut(
            id=new_id(),
            name=body.name.strip(),
            description=body.description.strip(),
            instructions=body.instructions.strip(),
            created_at=datetime.now(UTC),
        )
        await self._store.put(caller.tenant_id, caller.user_id, skill)
        return skill

    async def upload(self, caller: Caller, filename: str, data: bytes) -> UploadResult:
        """Scan an uploaded file and, when it is clean, add its skills. A refusal says why, file by file; nothing is added."""
        try:
            parsed = read_upload(filename, data)
        except SkillRefused as refused:
            return UploadResult(findings=refused.findings)
        await self._room(caller, len(parsed))
        made = []
        for name, description, instructions in parsed:
            made.append(
                await self.create(
                    caller, SkillIn(name=name, description=description, instructions=instructions)
                )
            )
        return UploadResult(created=made)

    async def change(self, caller: Caller, skill_id: str, body: SkillPatch) -> SkillOut:
        current = await self.get(caller, skill_id)
        update = body.model_dump(exclude_none=True)
        changed = current.model_copy(
            update={k: v.strip() if isinstance(v, str) else v for k, v in update.items()}
        )
        findings = scan_text("instructions", f"{changed.description}\n{changed.instructions}")
        if findings:
            raise InvalidRequestError(f"This skill {findings[0].problem}.")
        await self._store.put(caller.tenant_id, caller.user_id, changed)
        return changed

    async def delete(self, caller: Caller, skill_id: str) -> None:
        await self._store.delete(caller.tenant_id, caller.user_id, skill_id.removeprefix(PREFIX))

    async def lines(self, caller: Caller, only: set[str] | None = None) -> list[str]:
        """One line per skill of the person's own that is on: its name and description. With `only` (an agent's abilities), just those."""
        skills = [
            s
            for s in await self.list(caller)
            if s.enabled and (only is None or f"{PREFIX}{s.id}" in only)
        ]
        return [f"- {s.name}: {s.description}" for s in skills]

    async def instructions_of(
        self, caller: Caller, name: str, only: set[str] | None = None
    ) -> str | None:
        wanted = name.strip().lower()
        for s in await self.list(caller):
            if (
                s.enabled
                and s.name.lower() == wanted
                and (only is None or f"{PREFIX}{s.id}" in only)
            ):
                return s.instructions
        return None

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


class UseSkillTool:
    """Loads the full instructions of one of the person's skills, by name: their own or one Loamz ships."""

    name = "use_skill"
    risk = ToolRisk.SAFE
    idempotent = True
    description = (
        "Load the full instructions of one of the person's skills by its name, then follow them."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The skill's name, as listed."}},
        "required": ["name"],
        "additionalProperties": False,
    }

    def __init__(self, resolve: Callable[[str], Awaitable[str | None]]) -> None:
        self._resolve = resolve

    async def execute(
        self, *, ctx: object = None, name: str = "", **_: object
    ) -> ToolExecutionResult:  # type: ignore[override]
        body = await self._resolve(name)
        if body is None:
            return ToolExecutionResult(
                content=[TextBlock(text=f"No skill named {name!r}.")], is_error=True
            )
        return ToolExecutionResult(content=[TextBlock(text=body)])


def skills(container: Container) -> SkillsService:
    """Provider: how the container builds this feature's service."""
    return SkillsService(SkillStore(container.database))
