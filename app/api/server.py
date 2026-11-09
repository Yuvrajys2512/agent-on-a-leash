"""Server entry point.

    uvicorn app.api.server:app                       real AI agent (needs ANTHROPIC_API_KEY in .env)
    AGENT=fake uvicorn app.api.server:app            scripted agent: no AI, no key, same story every time
    AGENT_RED_TEAM=1 uvicorn app.api.server:app      real AI + a SIMULATED compromise after it reads the trick email

PowerShell:  $env:AGENT="fake"; uvicorn app.api.server:app
"""
import os

from dotenv import load_dotenv

from app.api.app import create_app
from app.mailbox import Mailbox

load_dotenv()

if os.getenv("AGENT", "real").lower() == "fake":
    from app.agent.scripted import run_fake_agent as agent
    info = {"agent": "fake", "red_team": False}
else:
    from app.agent import red_team_on, run_agent as agent
    info = {"agent": "real", "red_team": red_team_on()}

from app.api.compiler_adapter import compile_via_agent  # Person A's plain-English compiler, behind an adapter

app = create_app(Mailbox(), agent, info=info, rule_compiler=compile_via_agent)
