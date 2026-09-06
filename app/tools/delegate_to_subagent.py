from langchain_core.tools import tool
from app.tools.sub_agents.coder import CoderAgent

@tool
def delegate_to_coding_model(task: str) -> str:
    """
    Delegate a coding task to a specialist coding subagent. Unlike writing code
    yourself, this subagent can actually run and verify its own code (via its own
    sandboxed execution) before returning, and can read source/config files and
    PDFs if needed. Use this for non-trivial coding tasks — writing, debugging, or
    fixing real code — where correctness matters more than speed, rather than
    writing code directly yourself.

    Give it a clear, self-contained task description — it does not see this
    conversation's history, only the string you pass here. Include any filenames,
    expected behavior, or constraints it needs, since it cannot ask you follow-up
    questions.

    Returns the subagent's final answer as plain text, including the verified code
    and a summary of what was tested. Treat this as a trustworthy, already-checked
    result — you do not need to re-verify it yourself.
    """
    return CoderAgent().run(task)