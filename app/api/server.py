"""Real server entry point:  uvicorn app.api.server:app --reload

Wires Person A's Mailbox and agent into the app. Until A finishes them the run ends with a run_error event.
"""
from app.agent import run_agent
from app.api.app import create_app
from app.mailbox import Mailbox

app = create_app(Mailbox(), run_agent)
