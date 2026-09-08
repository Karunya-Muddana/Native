# app/tools/workspace.py
"""Reset the workspace: sandbox files, conversation history, and the indexes.

This is the only tool here that destroys anything, so it works in two beats.
Called without `confirm`, it reports exactly what it *would* remove and removes
nothing. Only `confirm=True` deletes. That is not decoration: the model decides
when to call tools, and "clear" appearing in a sentence is not the same as the
user asking for it — the dry run puts the count in front of the user first.

What it never touches:

  knowledge_base/   the facility's own documents. They are the authority for
                    procedures and equipment history, they were not produced by
                    this agent, and nothing in a "clear the workspace" request
                    implies throwing them away. Only their *index* is dropped,
                    and that rebuilds itself on the next search.
"""

import shutil
import sqlite3
from pathlib import Path

from langchain_core.tools import tool

from app.config.settings import CHECKPOINT_DB
from app.tools.paths import INPUT_DIR, OUTPUT_DIR

SCOPES = ("input", "output", "files", "chats", "rag", "all")

# What each scope expands to, so the dry run and the delete cannot disagree.
PARTS = {
    "input": ("input",),
    "output": ("output",),
    "files": ("input", "output"),
    "chats": ("chats",),
    "rag": ("rag",),
    "all": ("input", "output", "chats", "rag"),
}


@tool
def clear_workspace(scope: str = "all", confirm: bool = False) -> str:
    """
    Empty the workspace: delete the files in the sandbox directories, erase the
    stored conversation history, and drop the search indexes.

    scope selects how much goes:
      'all'    — everything below (the default)
      'input'  — the files in /sandbox/input/
      'output' — the files in /sandbox/output/
      'files'  — both sandbox directories, leaving history alone
      'chats'  — every past conversation: the saved transcripts, the session
                 list, and the remembered turns this assistant recalls from
                 earlier sessions
      'rag'    — the vector indexes only (conversation memory and the knowledge
                 base index), rebuilt automatically on next use

    The knowledge base documents themselves are never deleted, whatever the
    scope — only their index.

    This is irreversible and there is no undo. Call it FIRST without confirm to
    see what would go, show the user that list, and only call it again with
    confirm=True once they have said yes. Never pass confirm=True on your own
    initiative, and never as part of some larger task the user asked for.
    """
    scope = (scope or "all").strip().lower()
    if scope not in SCOPES:
        return f"Error: scope must be one of {', '.join(SCOPES)}, got '{scope}'."

    parts = PARTS[scope]
    plan = [_survey(part) for part in parts]

    if not confirm:
        lines = [f"Nothing deleted yet. Clearing scope '{scope}' would remove:"]
        lines += [f"  - {line}" for line in plan]
        lines.append(
            "\nThe knowledge base documents themselves stay. Ask the user to "
            "confirm, then call clear_workspace(scope, confirm=True)."
        )
        return "\n".join(lines)

    done, failed = [], []
    for part in parts:
        try:
            done.append(_clear(part))
        except Exception as e:
            failed.append(f"{part}: {str(e)}")

    out = ["Workspace cleared:"] + [f"  - {line}" for line in done]
    if failed:
        out += ["", "Not cleared:"] + [f"  - {line}" for line in failed]
    return "\n".join(out)


# ── the same erase, without the model in the middle ────────────────────────
def clear_history() -> dict:
    """Erase every stored conversation and both vector indexes.

    The tool above exists for when the *agent* is asked to clear things, and so
    it is deliberately two-step. This is the direct path behind the button in
    the UI, where the user has already confirmed in front of the thing being
    deleted. Sandbox files are untouched — this is history only.
    """
    sessions, memories = _clear_chats()
    rag_chunks = _clear_rag_index()
    return {"sessions": sessions, "memories": memories, "chunks": rag_chunks}


# ── surveying ──────────────────────────────────────────────────────────────
def _survey(part: str) -> str:
    if part in ("input", "output"):
        directory = INPUT_DIR if part == "input" else OUTPUT_DIR
        files = _files(directory)
        if not files:
            return f"/sandbox/{part}/ — already empty"
        shown = ", ".join(f.name for f in files[:8])
        more = f" and {len(files) - 8} more" if len(files) > 8 else ""
        return f"/sandbox/{part}/ — {len(files)} file(s): {shown}{more}"

    if part == "chats":
        return f"conversation history — {_session_count()} saved session(s), plus all recalled memory"

    return "search indexes — conversation memory and the knowledge base index (documents kept)"


def _files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted((f for f in directory.iterdir() if f.name != ".gitkeep"), key=lambda f: f.name)


def _session_count() -> int:
    try:
        with sqlite3.connect(CHECKPOINT_DB) as conn:
            return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    except sqlite3.Error:
        return 0  # table not created yet — nothing has been saved


# ── clearing ───────────────────────────────────────────────────────────────
def _clear(part: str) -> str:
    if part in ("input", "output"):
        directory = INPUT_DIR if part == "input" else OUTPUT_DIR
        removed = 0
        for entry in _files(directory):
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            removed += 1
        return f"/sandbox/{part}/ — {removed} file(s) deleted"

    if part == "chats":
        sessions, memories = _clear_chats()
        return f"conversation history — {sessions} session(s) and {memories} remembered turn(s) deleted"

    return f"search indexes — {_clear_indexes()}"


def _clear_chats() -> tuple[int, int]:
    """Wipe the checkpoint database, then the recalled-memory collection.

    Every table in agent.db is emptied rather than a named list of them: the
    conversation lives in LangGraph's checkpointer tables, whose names and
    number are its business and have changed across versions, and `sessions`
    alongside them. Anything in that file is chat history by definition.
    """
    sessions = _session_count()
    with sqlite3.connect(CHECKPOINT_DB) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            conn.execute(f'DELETE FROM "{table}"')
        conn.commit()

    memories = _clear_history_collection()
    _drop_live_sessions()
    return sessions, memories


def _clear_history_collection() -> int:
    from app.config import context_manager

    ids = context_manager.collection.get()["ids"]
    if ids:
        context_manager.collection.delete(ids=ids)
    return len(ids)


def _drop_live_sessions() -> None:
    """Forget the per-session context objects the runtime is holding.

    Imported here, not at module scope: the runtime imports the tool registry,
    which imports this file, so a top-level import would be a cycle. Nothing
    breaks if the runtime was never started — the CLI path may not have one.
    """
    try:
        from app.runtime import runtime as runtime_module
    except Exception:
        return
    for memory in list(runtime_module._memories.values()):
        memory.context = []
        memory._seen = set()
    runtime_module._memories.clear()


def _clear_rag_index() -> int:
    """Empty the knowledge base index.

    It is an in-process collection built on first search. Emptying it and
    clearing the flag makes the next search rebuild from whatever is in
    knowledge_base/ now; the documents themselves are never touched.
    """
    from app.tools import rag

    ids = rag.collection.get()["ids"]
    if ids:
        rag.collection.delete(ids=ids)
    rag.indexed = False
    return len(ids)


def _clear_indexes() -> str:
    memories = _clear_history_collection()
    chunks = _clear_rag_index()
    return (
        f"{memories} remembered turn(s) and {chunks} document chunk(s) dropped; "
        "the knowledge base reindexes on the next search"
    )
