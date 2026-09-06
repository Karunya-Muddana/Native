"""Tools that produce a file for a person to read, rather than text for the agent.

Each one writes into /sandbox/output/, then reopens what it wrote and reports
the real contents — see the read-back sections. The agent's account of a file it
generated is otherwise a description of its intent, which is not the same thing.
"""

from app.tools.authoring.document import write_document
from app.tools.authoring.presentation import create_presentation
from app.tools.authoring.spreadsheet import create_spreadsheet

__all__ = ["write_document", "create_spreadsheet", "create_presentation"]
