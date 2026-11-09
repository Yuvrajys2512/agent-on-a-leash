"""Server entry point.

    uvicorn app.api.server:app                 real AI agent (needs ANTHROPIC_API_KEY in .env)
    AGENT=fake uvicorn app.api.server:app      Person A's scripted agent: no AI, no key, same trick email

PowerShell:  $env:AGENT="fake"; uvicorn app.api.server:app
"""
import os

from dotenv import load_dotenv

from app.api.app import create_app
from app.mailbox import Mailbox

load_dotenv()

if os.getenv("AGENT", "real").lower() == "fake":
    from app.agent.scripted import run_fake_agent as agent
else:
    from app.agent import run_agent as agent

app = create_app(Mailbox(), agent)
