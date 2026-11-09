"""Event hub for the live feed (Phase 2.B2). Owned by Person B.

Keeps the events of the current run so a browser that connects late still sees everything,
then streams new ones as Server-Sent Events.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

MAX_EVENTS = 2000


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


class Hub:
    def __init__(self, keepalive: float = 15.0) -> None:
        self.events: list[dict[str, Any]] = []
        self._queues: set[asyncio.Queue] = set()
        self.keepalive = keepalive

    def publish(self, event: dict[str, Any]) -> None:
        event = {"seq": len(self.events), **event}
        self.events.append(event)
        del self.events[:-MAX_EVENTS]
        for q in list(self._queues):
            q.put_nowait(event)

    def clear(self) -> None:
        self.events.clear()
        self.publish({"kind": "reset"})

    async def stream(self) -> AsyncIterator[str]:
        q: asyncio.Queue = asyncio.Queue()
        replay = list(self.events)
        self._queues.add(q)  # no await between copy and register, so no event is missed or doubled
        try:
            for e in replay:
                yield sse(e)
            while True:
                try:
                    yield sse(await asyncio.wait_for(q.get(), self.keepalive))
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._queues.discard(q)
