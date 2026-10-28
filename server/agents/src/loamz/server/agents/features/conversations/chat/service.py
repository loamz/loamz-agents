from __future__ import annotations

from collections.abc import AsyncIterator

from loamz.engine.errors import ConflictError, InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.runtime import ChatPayload, Message, Runtime
from loamz.runtime.kernel.tools import ToolRisk
from loamz.runtime.kernel.tools.approval import ApprovalDecision, DurableApproval
from loamz.runtime.kernel.types.content import ChatMessage, Role, TextBlock
from loamz.runtime.kernel.types.identity import Actor
from loamz.runtime.kernel.types.run_log import RunLogKind
from loamz.runtime.kernel.wire import wire_from_log
from loamz.runtime.kernel.wire.streaming import TERMINAL
from loamz.runtime.stores import AgentStore
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.chat.model import (
    ApprovalOut,
    CancelResponse,
    ChatRequest,
    HitlResponse,
    HitlStatus,
)
from loamz.server.agents.features.conversations.threads.service import ThreadsService, threads

INTERRUPTED = "interrupted: the server restarted while it ran"


class ChatService:
    def __init__(self, container: Container, threads_service: ThreadsService) -> None:
        self._c = container
        self._threads = threads_service

    @property
    def _rt(self) -> Runtime:
        return self._c.runtime

    @property
    def _store(self) -> AgentStore:
        return self._c.agent_store

    def tool_catalog(self) -> list[object]:
        return list(self._c.tool_servers.found.values())

    async def _profile(self, caller: Caller, agent_id: str):
        """The caller's own agent of that id, or None (the default assistant, or an id that is not theirs)."""
        if agent_id == "default":
            return None
        from loamz.server.agents.features.directory.agents.service import agents

        try:
            return await self._c.get(agents).get_owned(caller, agent_id)
        except NotFoundError:
            return None

    def _tool_server_tools(self, profile) -> list[object]:
        """The tools an agent has: the code interpreter (with its own files) always, and tool-server tools only where its
        `allowed_tools` names them, never every tool of a server."""
        if profile is None:
            return []
        code = [self._c.code_tool] if self._c.code_tool else []
        named = profile.allowed_tools or ()
        data = [self._c.data_tool_set[n] for n in named if n in self._c.data_tool_set]
        return [*self._c.tool_servers.tools(list(named)), *data, *code]

    async def admit(self, caller: Caller, req: ChatRequest) -> None:
        """Everything that can refuse a message, checked before the response starts streaming, so a refusal is a proper error
        rather than a stream that breaks off."""
        header = await self._threads.require(caller, req.thread_id)
        if header.locked_at is not None:
            raise InvalidRequestError(header.locked_reason or "This conversation is locked.")
        if not req.prompt_text():
            raise InvalidRequestError("Message prompt cannot be empty.")
        self._c.require_model(req.model)
        for run in await self._rt.find_runs(thread_id=req.thread_id, active_only=True):
            # A chat agent is built for each message and lives only in the process that built it, so a run whose agent is gone
            # (the server restarted while it ran) can never finish: close it rather than block the conversation for ever.
            if self._rt.is_live(run.agent):
                raise ConflictError(f"A stream is already running for thread {req.thread_id}.")
            await self._rt.cancel(run.run_id, reason=INTERRUPTED)

    async def chat(self, caller: Caller, req: ChatRequest) -> AsyncIterator[str]:
        """Stream one turn. The caller has already been through `admit`."""
        header = await self._threads.require(caller, req.thread_id)
        raw_prompt = req.prompt_text()

        prompt = raw_prompt
        if req.file_ids:
            from loamz.server.agents.features.workspace.files.service import FilesService

            files_svc = FilesService(self._c)
            for fid in req.file_ids:
                try:
                    data, filename = await files_svc.download(caller, fid)
                    content = data.decode("utf-8", errors="replace")
                    prompt += f"\n\n[Attached file: {filename}]\n{content}"
                except Exception:
                    pass

        agent_id = req.agent_id or header.agent_id or "default"
        sender_actor = Actor(type="user", key=caller.user_id)

        from loamz.runtime.kernel.agents.factory import create_assistant_agent
        from loamz.server.agents.features.directory.agents.service import instructions_block

        profile = await self._profile(caller, agent_id)
        tools = self._tool_server_tools(profile)
        # What this person switched on in the marketplace: skills become instructions; tools are offered only when on.
        from loamz.server.agents.features.people.marketplace.service import marketplace

        market = self._c.get(marketplace)
        # An agent uses only the skills, tools and connectors it was given; the plain assistant uses everything switched on.
        only = set(profile.abilities) if profile else None
        # Skills (the shipped ones switched on, and the person's own): name and description in the prompt, the full instructions
        # loaded when the agent uses one with `use_skill`.
        from loamz.server.agents.features.people.marketplace.service import SKILLS_INTRO
        from loamz.server.agents.features.people.skills.service import UseSkillTool
        from loamz.server.agents.features.people.skills.service import skills as skills_feature

        own = self._c.get(skills_feature)
        skill_lines = [*await market.skill_lines(caller, only), *await own.lines(caller, only)]
        skills = SKILLS_INTRO + "\n".join(skill_lines) if skill_lines else ""
        if skill_lines:

            async def load(name: str) -> str | None:
                return await market.skill_instructions(
                    caller, name, only
                ) or await own.instructions_of(caller, name, only)

            tools = [*tools, UseSkillTool(load)]
        from loamz.server.agents.features.conversations.chat.builtin import current_time, tools_for

        choices = await market.tool_choices(caller, only)
        if "code-interpreter" in choices and self._c.code_tool and self._c.code_tool not in tools:
            tools = [*tools, self._c.code_tool]
        tools = [*tools, *tools_for(self._c, choices)]
        if profile:
            tools = [*tools, current_time()]
        # Tools of the connectors this person signed in to (Notion, Todoist, ...), each called with their own token.
        from loamz.server.agents.features.people.connections.service import connections

        tools = [
            *tools,
            *await self._c.get(connections).tools(
                caller, only, profile.tool_access if profile else None
            ),
        ]
        # Every agent remembers: a `memory` tool to remember and recall what the person shares, and what it already knows as background.
        remembered = ""
        if profile:
            from loamz.server.agents.features.people.memory.service import memory, memory_tool

            people_memory = self._c.get(memory)
            recall = memory_tool(req.thread_id, None, people_memory.store)
            if recall is not None:
                tools = [*tools, recall]
            remembered = await people_memory.user_context_block(caller.tenant_id, caller.user_id)
        if req.helper:
            tools = [
                t for t in tools if getattr(t, "risk", None) == ToolRisk.SAFE
            ]  # a helper only looks things up
        threads = self._c.agent_store.threads
        model_name = req.model or (profile.model if profile else None)
        recall, checkpointer = self._c.conversation_memory(threads, req.thread_id, model_name)
        # The conversation's notes: shown to the agent each turn, kept by a background writer, and the agent's own tool for them.
        from loamz.server.agents.features.conversations.memory.service import (
            GUIDE,
        )
        from loamz.server.agents.features.conversations.memory.service import (
            memory as conversation_notes,
        )

        notes = self._c.get(conversation_notes)
        summarizer = notes.summarizer(caller, checkpointer, threads, req.thread_id, model_name)
        notes_block = await notes.block(caller, req.thread_id)
        if profile and not req.helper:
            # A chat agent (not the plain assistant of a session) can have schedules of its own: it sees them freely, changes need approval.
            from loamz.server.agents.features.automation.schedules.service import schedules
            from loamz.server.agents.features.automation.schedules.tool import schedule_tools
            from loamz.server.agents.features.conversations.chat.helpers import StartHelpersTool

            tools = [
                *tools,
                *schedule_tools(self._c.get(schedules), caller, profile.id),
                StartHelpersTool(self._c, caller, profile.id),
            ]
        agent = create_assistant_agent(
            model_client=self._c.model_client(req.model or (profile.model if profile else None)),
            history=threads,
            summarizer=summarizer,
            tools=[*tools, recall, *([] if req.helper else notes.tools(caller, req.thread_id))],
            system_instructions=(req.system_instructions or "")
            + (instructions_block(profile) if profile else "")
            + skills
            + (f"\n\n{remembered}" if remembered else "")
            + ("" if req.helper else f"\n\n{GUIDE}")
            + (f"\n\n{notes_block}" if notes_block and not req.helper else ""),
            # One actor per conversation (the activity tracker looks for `assistant/<thread id>`). Sharing one actor
            # between conversations would fold a message sent while another run is going into that run.
            session_id=req.thread_id,
            name="assistant",
            # A person approves every call to a tool the operator did not mark read-only; the run sleeps until they decide.
            approval_handler=DurableApproval() if tools else None,
            approval_required_risk=ToolRisk.HIGH if tools else None,
        )
        await self._rt.register(agent, pinned=False)

        message = Message(
            target=agent.id,
            sender=sender_actor,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text=prompt)])
            ),
            correlation_id=req.thread_id,
            # Who this run speaks for: from the verified sign-in, never from the request body or the model.
            metadata={
                "display_text": raw_prompt,
                "tenant_id": caller.tenant_id,
                "user_id": caller.user_id,
                "groups": sorted(caller.groups),
                "thread_id": req.thread_id,
            },
        )
        run_id = await self._rt.submit(
            agent.id,
            message,
            tenant=caller.tenant_id,
            max_retries=0,
            thread_id=req.thread_id,
        )

        async for line in self._relay(run_id):
            yield line
        if profile and not req.helper:
            notes.keep_up(caller, req.thread_id, model_name)

    async def _relay(self, run_id: str, from_seq: int = 0) -> AsyncIterator[str]:
        """A run's events as the console's event stream, from `from_seq`, to the end of the run."""
        async for entry in self._rt.tail(run_id, from_seq=from_seq):
            wire = wire_from_log(entry.kind, entry.payload or {})
            if wire is not None:
                yield f"event: {wire.type}\ndata: {wire.model_dump_json()}\n\n"
            if entry.kind == RunLogKind.RUN_COMPLETED:
                yield 'data: {"type": "run.completed"}\n\n'
                break
            elif entry.kind == RunLogKind.RUN_FAILED:
                err = (entry.payload or {}).get("error", "agent run failed")
                yield f'data: {{"type": "run.failed", "error": "{err}"}}\n\n'
                break
            elif entry.kind == RunLogKind.RUN_CANCELLED:
                yield 'data: {"type": "run.cancelled"}\n\n'
                break
            elif entry.kind in TERMINAL:
                break
        yield "data: [DONE]\n\n"

    async def attach(self, caller: Caller, thread_id: str) -> tuple[str, int]:
        """The run a conversation has in progress and where its stream picks up: after everything already recorded. For a
        console that reloaded while the run waited on a person: what it missed is in the history, and what happens next is
        what it needs. Raises when nothing is running."""
        await self._threads.require(caller, thread_id)
        active = [
            r
            for r in await self._rt.find_runs(thread_id=thread_id, active_only=True)
            if r.tenant == caller.tenant_id
        ]
        if not active:
            raise NotFoundError("No run is in progress for this conversation.")
        run_id = active[-1].run_id
        recorded = await self._rt.read(run_id)
        return run_id, (recorded[-1].seq + 1) if recorded else 0

    async def reconnect(self, run_id: str, from_seq: int) -> AsyncIterator[str]:
        # A run waiting on a person sends nothing, and proxies hold a response's headers until its first byte: so the first thing
        # sent is a ping, and the console (which answers the person only once it is connected) is not left waiting for it.
        yield 'data: {"type": "ping"}\n\n'
        async for line in self._relay(run_id, from_seq):
            yield line

    async def _is_mine(self, caller: Caller, run: object) -> bool:
        """Whether the caller may see this run: it is in their own thread (a tenant admin may see the organization's).
        Without this, anyone in an organization could list and answer another person's approvals."""
        thread_id = getattr(run, "thread_id", None)
        if getattr(run, "tenant", None) != caller.tenant_id or not thread_id:
            return False
        try:
            await self._threads.require(caller, thread_id)
        except NotFoundError:
            return False
        return True

    async def respond_hitl(
        self, caller: Caller, request_id: str, body: HitlResponse
    ) -> dict[str, object]:
        runs = await self._rt.find_runs(active_only=True)
        target_run_id: str | None = None
        for run in runs:
            if await self._is_mine(caller, run):
                pending = await self._rt.pending_approvals(run.run_id)
                if any(p.request_id == request_id for p in pending) and not self._rt.is_live(
                    run.agent
                ):
                    await self._rt.cancel(run.run_id, reason=INTERRUPTED)
                    raise ConflictError(
                        "The server restarted while this waited for you, so it cannot continue. Send your message again."
                    )
                for p in pending:
                    if p.request_id == request_id:
                        target_run_id = run.run_id
                        break
            if target_run_id:
                break

        if not target_run_id:
            raise NotFoundError(f"Approval request {request_id} not found or already decided.")

        action_map = {
            "approve": ApprovalDecision.APPROVED,
            "approved": ApprovalDecision.APPROVED,
            "reject": ApprovalDecision.DENIED,
            "deny": ApprovalDecision.DENIED,
            "denied": ApprovalDecision.DENIED,
            "modify": ApprovalDecision.MODIFIED,
            "modified": ApprovalDecision.MODIFIED,
        }
        decision = action_map.get(body.action.lower())
        if decision is None:
            raise InvalidRequestError(f"Invalid approval decision {body.action}")

        # Invariant I23: Server names the approver
        await self._rt.decide_approval(
            run_id=target_run_id,
            request_id=request_id,
            decision=decision,
            by=caller.user_id,
            reason=body.reason,
            modified_args=body.modified_args,
        )
        return {"status": "ok", "request_id": request_id, "action": body.action}

    async def cancel(self, caller: Caller, thread_id: str) -> CancelResponse:
        await self._threads.require(caller, thread_id)
        active = await self._rt.find_runs(thread_id=thread_id, active_only=True)
        for run in active:
            if run.tenant == caller.tenant_id:
                await self._rt.request_cancel(run.run_id)
        return CancelResponse(thread_id=thread_id, cancelled=True)

    async def hitl_status(self, caller: Caller, thread_id: str) -> HitlStatus:
        await self._threads.require(caller, thread_id)
        active = await self._rt.find_runs(thread_id=thread_id, active_only=True)
        pending_list: list[dict[str, object]] = []
        for run in active:
            if run.tenant == caller.tenant_id:
                if not self._rt.is_live(run.agent):
                    # Its agent lived in a server that has since restarted: nobody can continue it, so nobody is waited for.
                    await self._rt.cancel(run.run_id, reason=INTERRUPTED)
                    continue
                for p in await self._rt.pending_approvals(run.run_id):
                    pending_list.append(
                        {
                            "request_id": p.request_id,
                            "tool_name": p.tool_name,
                            "tool_args": dict(p.args),
                            "risk": p.risk,
                            "summary": p.summary,
                        }
                    )
        status = (
            "waiting"
            if pending_list
            else ("running" if any(self._rt.is_live(r.agent) for r in active) else "idle")
        )
        return HitlStatus(status=status, pending=pending_list)

    async def list_approvals(self, caller: Caller) -> list[ApprovalOut]:
        runs = await self._rt.find_runs(active_only=True)
        approvals: list[ApprovalOut] = []
        for run in runs:
            if await self._is_mine(caller, run):
                for p in await self._rt.pending_approvals(run.run_id):
                    approvals.append(
                        ApprovalOut(
                            request_id=p.request_id,
                            thread_id=run.thread_id or "",
                            run_id=run.run_id,
                            tool_name=p.tool_name,
                            tool_args=dict(p.args),
                        )
                    )
        return approvals

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return 0


def chat(container: Container) -> ChatService:
    return ChatService(container, container.get(threads))
