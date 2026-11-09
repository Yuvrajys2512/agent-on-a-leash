"""Human approvals + live events (Phase 2.B1). Owned by Person B.

The gate calls `request()` for every ASK action and waits. The API/UI calls `resolve()`.
If nobody answers within `timeout` seconds the action counts as REJECTED (fail safe).
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.contracts import Decision, ProposedAction

Listener = Callable[[dict[str, Any]], None]


class ApprovalStore:
    def __init__(self, timeout: float | None = 300.0) -> None:
        self.timeout = timeout
        self._pending: dict[str, tuple[ProposedAction, Decision, asyncio.Future[bool]]] = {}
        self._listeners: list[Listener] = []

    # ---- events (consumed by the SSE endpoint in 2.B2) ----
    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, kind: str, **payload: Any) -> None:
        event = {"kind": kind, **payload}
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # a broken listener must never stop the agent or the gate
                pass

    # ---- approvals ----
    @property
    def pending(self) -> list[dict[str, Any]]:
        return [{"action": a.model_dump(mode="json"), "decision": d.model_dump(mode="json")}
                for a, d, _ in self._pending.values()]

    async def request(self, action: ProposedAction, decision: Decision) -> bool:
        """Wait for the human. True = approved, False = rejected or timed out."""
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[action.id] = (action, decision, fut)
        self.emit("pending", action=action.model_dump(mode="json"), decision=decision.model_dump(mode="json"))
        try:
            return await asyncio.wait_for(fut, self.timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(action.id, None)

    def resolve(self, action_id: str, approve: bool) -> bool:
        """Called by the API. Returns False if there is no such pending action."""
        item = self._pending.get(action_id)
        if item is None or item[2].done():
            return False
        item[2].set_result(approve)
        return True
