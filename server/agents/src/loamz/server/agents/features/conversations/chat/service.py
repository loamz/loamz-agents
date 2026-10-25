from __future__ import annotations

from collections.abc import AsyncIterator

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.runtime import ChatPayload, Message, Runtime
from loamz.engine.agents.kernel.tools import ToolRisk
from loamz.engine.agents.kernel.tools.approval import ApprovalDecision, DurableApproval
from loamz.engine.agents.kernel.types.content import ChatMessage, Role, TextBlock
from loamz.engine.agents.kernel.types.identity import Actor
from loamz.engine.agents.kernel.types.run_log import RunLogKind
from loamz.engine.agents.kernel.wire import wire_from_log
from loamz.engine.agents.kernel.wire.streaming import TERMINAL
from loamz.engine.agents.stores import AgentStore
from loamz.engine.errors import ConflictError, InvalidRequestError, NotFoundError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.chat.model import (
    ApprovalOut,
    CancelResponse,
    ChatRequest,
    HitlResponse,
    HitlStatus,
)
from loamz.server.agents.features.conversations.threads.service import ThreadsService, threads


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
        """Tool-server tools an agent has, only those its `allowed_tools` names: never every tool of a server."""
        if profile is None or not profile.allowed_tools:
            return []
        data = [
            self._c.data_tool_set[n] for n in profile.allowed_tools if n in self._c.data_tool_set
        ]
        code = (
            [self._c.code_tool]
            if "code_interpreter" in profile.allowed_tools and self._c.code_tool
            else []
        )
        return [*self._c.tool_servers.tools(profile.allowed_tools), *data, *code]

    async def admit(self, caller: Caller, req: ChatRequest) -> None:
        """Everything that can refuse a message, checked before the response starts streaming, so a refusal is a proper error
        rather than a stream that breaks off."""
        header = await self._threads.require(caller, req.thread_id)
        if header.locked_at is not None:
            raise InvalidRequestError(header.locked_reason or "This conversation is locked.")
        if not req.prompt_text():
            raise InvalidRequestError("Message prompt cannot be empty.")
        for run in await self._rt.find_runs(thread_id=req.thread_id, active_only=True):
            # A chat agent is built for each message and lives only in the process that built it, so a run whose agent is gone
            # (the server restarted while it ran) can never finish: close it rather than block the conversation for ever.
            if self._rt.is_live(run.agent):
                raise ConflictError(f"A stream is already running for thread {req.thread_id}.")
            await self._rt.cancel(
                run.run_id, reason="interrupted: the server restarted while it ran"
            )

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

        from loamz.engine.agents.kernel.agents.factory import create_assistant_agent
        from loamz.server.agents.features.directory.agents.service import instructions_block

        profile = await self._profile(caller, agent_id)
        tools = self._tool_server_tools(profile)
        agent = create_assistant_agent(
            model_client=self._c.model_client(req.model or (profile.model if profile else None)),
            history=self._c.agent_store.threads,
            tools=tools or None,
            system_instructions=(req.system_instructions or "")
            + (instructions_block(profile) if profile else ""),
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

        async for entry in self._rt.tail(run_id):
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

    async def respond_hitl(
        self, caller: Caller, request_id: str, body: HitlResponse
    ) -> dict[str, object]:
        runs = await self._rt.find_runs(active_only=True)
        target_run_id: str | None = None
        for run in runs:
            if run.tenant == caller.tenant_id:
                pending = await self._rt.pending_approvals(run.run_id)
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
        status = "waiting" if pending_list else ("running" if active else "idle")
        return HitlStatus(status=status, pending=pending_list)

    async def list_approvals(self, caller: Caller) -> list[ApprovalOut]:
        runs = await self._rt.find_runs(active_only=True)
        approvals: list[ApprovalOut] = []
        for run in runs:
            if run.tenant == caller.tenant_id:
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
