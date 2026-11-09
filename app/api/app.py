"""FastAPI app (Phase 2.B2). Owned by Person B.

create_app() takes the mailbox and the agent function as arguments, so tests and demos can plug in fakes.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.api.hub import Hub
from app.approvals import ApprovalStore
from app.audit.log import AuditLog, explain
from app.contracts import Gate, MailboxProtocol
from app.gate import make_gate
from app.policy.engine import PolicyEngine

DEFAULT_TASK = ("Triage this inbox: label every email, reply to the customer complaint, "
                "and forward invoices to accounting@acme.example.")

AgentFn = Callable[[MailboxProtocol, Gate, str], Awaitable[None]]


class RunRequest(BaseModel):
    task: str | None = None


def create_app(
    mailbox: MailboxProtocol,
    agent: AgentFn,
    rules_path: str = "rules.yaml",
    audit_path: str = "audit.jsonl",
    approval_timeout: float | None = 300.0,
    ui_dir: str = "ui",
    info: dict | None = None,
) -> FastAPI:
    """`info` is shown to the UI via GET /config, e.g. {"agent": "real" | "fake", "red_team": bool}."""
    app = FastAPI(title="Agent on a Leash")
    hub = Hub()
    approvals = ApprovalStore(timeout=approval_timeout)
    approvals.subscribe(hub.publish)
    audit = AuditLog(audit_path)
    state = {"task": None, "error": None}
    app.state.hub, app.state.approvals, app.state.audit = hub, approvals, audit

    def running() -> bool:
        return state["task"] is not None and not state["task"].done()

    async def _run(task_text: str) -> None:
        hub.publish({"kind": "run_started", "task": task_text})
        try:
            gate = make_gate(PolicyEngine(rules_path), mailbox, audit, approvals)  # fresh gate = fresh history
            await agent(mailbox, gate, task_text)
            hub.publish({"kind": "run_finished"})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # includes NotImplementedError from unfinished stubs
            state["error"] = f"{type(e).__name__}: {e}"
            hub.publish({"kind": "run_error", "error": state["error"]})

    @app.post("/run", status_code=202)
    async def run(req: RunRequest | None = None):
        if running():
            raise HTTPException(409, "A run is already in progress.")
        state["error"] = None
        state["task"] = asyncio.create_task(_run((req and req.task) or DEFAULT_TASK))
        return {"status": "started"}

    @app.get("/config")
    async def config():
        return {"agent": "real", "red_team": False, **(info or {})}

    @app.get("/status")
    async def status():
        return {"running": running(), "error": state["error"], "pending": len(approvals.pending)}

    @app.get("/events")
    async def events():
        return StreamingResponse(hub.stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/pending")
    async def pending():
        return approvals.pending

    def _decide(action_id: str, approve: bool):
        if not approvals.resolve(action_id, approve):
            raise HTTPException(404, "No pending action with that id.")
        return {"status": "approved" if approve else "rejected"}

    @app.post("/approve/{action_id}")
    async def approve(action_id: str):
        return _decide(action_id, True)

    @app.post("/reject/{action_id}")
    async def reject(action_id: str):
        return _decide(action_id, False)

    @app.get("/log")
    async def log():
        return [{**e.model_dump(mode="json"), "explanation": explain(e)} for e in audit.read_all()]

    @app.get("/rules")
    async def rules():
        return [r.model_dump(mode="json") for r in PolicyEngine(rules_path).rules]

    @app.get("/inbox")
    async def inbox():
        return [e.model_dump(mode="json") for e in mailbox.list()]

    @app.post("/reset")
    async def reset():
        if running():
            state["task"].cancel()
            try:
                await state["task"]
            except BaseException:
                pass
        approvals.reject_all()
        mailbox.reset()
        archived = audit.rotate()
        hub.clear()
        state["error"] = None
        return {"status": "reset", "archived_log": str(archived) if archived else None}

    if Path(ui_dir).is_dir():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")  # must stay last
    return app
