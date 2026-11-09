# Agent on a Leash — instructions for Claude Code

Read PLAN.md first. It is the source of truth for phases, ownership and contracts.

- Two people work in parallel: **Person A** (friend) owns `app/mailbox/`, `app/agent/`, `data/`. **Person B** owns `app/policy/`, `app/audit/`, `app/api/`, `app/gate.py`, `ui/`, `rules.yaml`.
- Only edit files your person owns. `app/contracts.py` is shared: never change it without telling the user (needs both people's approval).
- Do ONE sub-step at a time (e.g. "Phase 1, step 1.B2"), run its verification, then stop and report.
- Branch names: `a/<phase>-<topic>` or `b/<phase>-<topic>`. Never push to `main` directly; open a PR. `git pull --rebase origin main` before starting.
- Safety logic lives in code (policy engine), never only in the LLM prompt. Unknown/erroring rule -> ASK or BLOCK, never ALLOW.
- Mock inbox only. No real email or network sends.
- Never commit `.env` or `audit.jsonl`.
