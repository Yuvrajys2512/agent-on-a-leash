"""FastAPI app (Phase 2.B2). Owned by Person B.

create_app() takes the mailbox and the agent function as arguments, so tests and demos can plug in fakes.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
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
from app.policy.rules import RulesError, load_rules
from app.policy.user_rules import RuleRefused, UserRulesStore, check_proposed, describe_rule, rules_to_yaml

DEFAULT_TASK = ("Triage this inbox: label every email, reply to the customer complaint, "
                "and forward invoices to accounting@acme.example.")

AgentFn = Callable[[MailboxProtocol, Gate, str], Awaitable[None]]
# Turns plain English into proposed rules (a list of dicts in the rules.yaml format). Person A's job; untrusted output.
RuleCompiler = Callable[[str], "list[dict] | Awaitable[list[dict]]"]
MAX_ENGLISH = 1000
MAX_PROPOSALS = 20


class RunRequest(BaseModel):
    task: str | None = None


class CompileRequest(BaseModel):
    text: str


def create_app(
    mailbox: MailboxProtocol,
    agent: AgentFn,
    rules_path: str = "rules.yaml",
    audit_path: str = "audit.jsonl",
    approval_timeout: float | None = 300.0,
    ui_dir: str = "ui",
    info: dict | None = None,
    user_rules_path: str = "rules.user.yaml",
    rule_compiler: RuleCompiler | None = None,
) -> FastAPI:
    """`info` is shown to the UI via GET /config, e.g. {"agent": "real" | "fake", "red_team": bool}.
    `rule_compiler` turns plain English into proposed rules; without it /rules/compile answers 501."""
    app = FastAPI(title="Agent on a Leash")
    hub = Hub()
    approvals = ApprovalStore(timeout=approval_timeout)
    approvals.subscribe(hub.publish)
    audit = AuditLog(audit_path)
    user_rules = UserRulesStore(user_rules_path)
    proposals: dict[str, dict] = {}  # proposal id -> {"english": str, "rules": [Rule]}; what the human previewed
    state = {"task": None, "error": None}
    app.state.hub, app.state.approvals, app.state.audit = hub, approvals, audit

    def running() -> bool:
        return state["task"] is not None and not state["task"].done()

    async def _run(task_text: str) -> None:
        hub.publish({"kind": "run_started", "task": task_text})
        try:
            # fresh gate = fresh history; rules (core + confirmed user rules) are read at the start of each run
            gate = make_gate(PolicyEngine(rules_path, user_rules_path), mailbox, audit, approvals)
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
        core = [{**r.model_dump(mode="json"), "source": "core"} for r in load_rules(rules_path).rules]
        mine = [{**r.model_dump(mode="json"), "source": "user"} for r in user_rules.load()]
        return core + mine

    # ---- rules written in plain English: compile (preview only) -> human confirms -> activate ----
    @app.post("/rules/compile")
    async def compile_rules(req: CompileRequest):
        """Turn English into PROPOSED rules and check them. Nothing is activated by this call."""
        text = req.text.strip()
        if not text or len(text) > MAX_ENGLISH:
            raise HTTPException(422, {"errors": [f"Write between 1 and {MAX_ENGLISH} characters."], "warnings": []})
        if rule_compiler is None:
            raise HTTPException(501, "No rule compiler is configured.")
        try:
            raw = rule_compiler(text)
            if inspect.isawaitable(raw):  # works for a plain function or an async one
                raw = await raw
        except RuleRefused as e:  # understood, but cannot be used (e.g. it would loosen the rules): say why
            raise HTTPException(422, {"errors": [str(e)], "warnings": []})
        except Exception as e:  # the compiler is an AI call: it can fail in many ways, none of which may change anything
            raise HTTPException(502, f"The rule compiler failed: {type(e).__name__}: {e}")
        taken = {r.id for r in load_rules(rules_path).rules} | user_rules.ids()
        checked = check_proposed(raw, taken)
        if not checked.ok:
            raise HTTPException(422, {"errors": checked.errors or ["No rules were produced."], "warnings": checked.warnings})
        pid = uuid.uuid4().hex[:10]
        proposals[pid] = {"english": text, "rules": checked.rules}
        while len(proposals) > MAX_PROPOSALS:
            proposals.pop(next(iter(proposals)))
        return {"proposal_id": pid, "english": text, "warnings": checked.warnings,
                "rules": [r.model_dump(mode="json") for r in checked.rules],
                "previews": [describe_rule(r) for r in checked.rules], "yaml": rules_to_yaml(checked.rules),
                "applies": "from the next run"}

    @app.post("/rules/activate/{proposal_id}")
    async def activate_rules(proposal_id: str):
        """The human's confirmation. Activates exactly the rules that were previewed, once."""
        proposal = proposals.get(proposal_id)
        if proposal is None:
            raise HTTPException(404, "No such proposal. Preview the rules again.")
        try:
            added = user_rules.add(proposal["rules"], proposal["english"])
        except RulesError as e:
            raise HTTPException(422, {"errors": [str(e)], "warnings": []})
        proposals.pop(proposal_id, None)
        hub.publish({"kind": "rules_changed"})
        return {"status": "activated", "rules": [r.model_dump(mode="json") for r in added], "applies": "from the next run"}

    @app.delete("/rules/user/{rule_id}")
    async def remove_user_rule(rule_id: str):
        if not user_rules.remove(rule_id):
            raise HTTPException(404, "No such user rule.")
        hub.publish({"kind": "rules_changed"})
        return {"status": "removed"}

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
        user_rules.archive()  # a fresh demo starts with the core rules only; the old file is kept, renamed
        proposals.clear()
        hub.clear()
        state["error"] = None
        return {"status": "reset", "archived_log": str(archived) if archived else None}

    if Path(ui_dir).is_dir():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")  # must stay last
    return app
