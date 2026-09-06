# app/models/base.py
"""The orchestrator role, run through the failover router."""

from langchain_core.messages import BaseMessage

from app.models.router import invoke
from app.tools import registery

tools = registery.tools


def ollama_model(messages: list[BaseMessage]):
    # The name is historical: this is the base role, and it may be served by
    # any provider in its chain, local or not.
    return invoke("base", messages, list(tools.values()))
