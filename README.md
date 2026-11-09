# Agent on a Leash

An AI agent works a mock inbox; every action goes through allow / ask / block rules, human approval, and an audit log.

See [PLAN.md](PLAN.md) for the phased plan and team split. Setup instructions come in Phase 6.

## Quick start
```
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # then fill in ANTHROPIC_API_KEY
```
