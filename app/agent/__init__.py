"""Person A: the agent. It only PROPOSES actions and hands each to the injected gate. Phase 0 stub."""
from __future__ import annotations

from app.contracts import Gate
from app.mailbox import Mailbox


async def run_agent(mailbox: Mailbox, gate: Gate, task: str) -> None:
    raise NotImplementedError("Phase 2.A1 (scripted fake agent) / 2.A3 (LLM loop)")
