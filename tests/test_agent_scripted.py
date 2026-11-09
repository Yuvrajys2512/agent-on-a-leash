from app.agent.scripted import run_fake_agent, scripted_actions
from app.contracts import ActionType, Decision, GateResult, Outcome, Verdict, is_internal
from app.mailbox import Mailbox


async def test_fake_agent_hands_every_action_to_the_gate_and_executes_nothing():
    seen = []

    async def gate(action):
        seen.append(action)
        return GateResult(outcome=Outcome.BLOCKED, decision=Decision(verdict=Verdict.BLOCK, rule_id="t", reason="t"))

    mb = Mailbox()
    results = await run_fake_agent(mb, gate, "triage")
    assert len(seen) == len(results) == len(scripted_actions()) and mb.outbox == []


def test_every_scripted_action_is_executable_and_context_is_only_what_was_read():
    mb = Mailbox()
    for a in scripted_actions():
        assert a.email_id in a.context_email_ids or a.context_email_ids[-1] == "e09"
        mb.execute(a)  # raises if params are wrong
    assert len(mb.outbox) == 9


def test_trick_is_in_the_script():
    evil = [a for a in scripted_actions() if a.type is ActionType.FORWARD and not is_internal(a.params["to"][0])]
    assert len(evil) == 3 and all("e09" in a.context_email_ids for a in evil)
    legit = [a for a in scripted_actions() if a.type is ActionType.FORWARD and is_internal(a.params["to"][0])]
    assert legit and all("e09" not in a.context_email_ids for a in legit)
