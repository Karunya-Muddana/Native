# app/models/coder_model.py
"""The coding subagent's role, run through the failover router."""

from langchain_core.messages import BaseMessage

from app.models.router import invoke
from app.tools import io, sandbox, sandbox_files

CODING_TOOLS = {
    sandbox.python.python_runner.name: sandbox.python.python_runner,
    io.read_pdf.name: io.read_pdf,
    io.read_text_file.name: io.read_text_file,
    sandbox_files.list_sandbox_files.name: sandbox_files.list_sandbox_files,
}


def coding_model(messages: list[BaseMessage]):
    return invoke("coder", messages, list(CODING_TOOLS.values()))


coder_model = coding_model
