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

try:  # Person A's plain-English rule compiler: used automatically once app/agent/rules_compiler.py exists
    from app.agent.rules_compiler import compile_rules as rule_compiler
except ImportError:
    rule_compiler = None

app = create_app(Mailbox(), agent, info=info, rule_compiler=rule_compiler)
