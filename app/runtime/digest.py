# app/runtime/digest.py
"""Tier 2 — what this session did before the window, kept as a rolling digest.

Context here is three tiers, and this file is the one that was missing:

  1. WORKING SET   the last N messages, verbatim, tool calls still paired with
                   their results. Exact, expensive, and short.
  2. SESSION DIGEST  (this file) everything in THIS session that has scrolled
                   out of the working set, folded into a running summary.
                   Lossy, cheap, and complete — nothing is ever dropped, it is
                   only compressed.
  3. LONG-TERM     Chroma, across every session on the machine. Associative:
                   it surfaces what resembles the current question, and it
                   knows nothing about what merely happened recently.

Without tier 2 the gap between 1 and 3 is a hole. Tier 3 is queried by
similarity against the latest message, so the twelfth source of a research run
comes back only if it happens to resemble the last thing said; the fact that the
run has already covered it does not surface at all. That is how a long run
starts repeating searches it already did, and how it writes a final answer from
the last few pages plus a vague memory of the rest.

The digest is built incrementally. Each time messages fall out of the working
set, the newly-evicted ones are folded into the existing summary — the old
summary and the new messages go in, one replacement summary comes out. So the
cost is proportional to what just fell out, not to the length of the session,
and a run of two hundred steps costs the same per fold as a run of ten.

It is stored in agent.db beside the checkpoints, which means it is per-session,
survives a restart, and is erased by the same reset that clears the chats —
history lives in one file by design.
"""

import sqlite3
import threading
import time

from app.models.router import invoke

# Fold only when enough has accumulated. Summarising one evicted message at a
# time would cost a model call per step and produce a summary of a summary of a
# summary, which is how detail actually gets lost.
MIN_FOLD = 6

MAX_DIGEST_CHARS = 3_500     # the digest is a briefing, not a transcript
MAX_TOOL_CHARS = 400         # a tool result's shape matters; its bulk does not
MAX_TURN_CHARS = 1_200

FOLD_PROMPT = """You maintain the running record of an agent session. It is the
only thing that survives once older messages scroll out of the context window,
so what you leave out is genuinely lost.

Rewrite the EXISTING RECORD so it also covers the NEW MESSAGES. Return the
rewritten record and nothing else — no preamble, no commentary.

Keep, always:
  - what the user actually asked for, in their own terms, including any
    constraint or preference they stated
  - findings, with the numbers, names and URLs attached to them. "Found several
    sources" is worthless; "[S3] daily.dev lists 12 AI newsletters" is not
  - files written, and where they were written to
  - what has already been tried, so it is not tried again — including searches
    that returned nothing useful
  - questions raised and still unanswered
  - decisions made, and the reason given for them

Drop: pleasantries, restated plans, reasoning that led nowhere, and the bulk of
tool output once its result is recorded.

Write compact prose or short bullets under headings. Stay under {limit}
characters. Prefer dropping older detail over losing anything from the NEW
MESSAGES, but never drop the user's original request.

EXISTING RECORD:
{existing}

NEW MESSAGES:
{new}"""

_lock = threading.Lock()


class SessionDigest:
    """The rolling record for every session, stored in `db_path`."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure(self):
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS digest (
                       session_id TEXT PRIMARY KEY,
                       covered    INTEGER NOT NULL DEFAULT 0,
                       summary    TEXT    NOT NULL DEFAULT '',
                       updated    REAL    NOT NULL DEFAULT 0
                   )"""
            )
            conn.commit()

    # -- storage ------------------------------------------------------------
    def read(self, session_id: str) -> tuple[int, str]:
        """(how many head messages are folded in, the summary)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT covered, summary FROM digest WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return (row[0], row[1]) if row else (0, "")

    def write(self, session_id: str, covered: int, summary: str):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO digest (session_id, covered, summary, updated)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       covered = excluded.covered,
                       summary = excluded.summary,
                       updated = excluded.updated""",
                (session_id, covered, summary, time.time()),
            )
            conn.commit()

    def forget(self, session_id: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM digest WHERE session_id = ?", (session_id,))
            conn.commit()

    # -- the fold -----------------------------------------------------------
    def update(self, session_id: str, messages: list, keep: int) -> str:
        """Fold anything newly outside the working set in, and return the digest.

        `keep` is the size of the working set — the tail that is still being
        sent verbatim. Everything before it is this tier's responsibility.

        Never raises. A session whose digest cannot be built is worse off than
        one that can, but far better off than one whose turn died trying.
        """
        covered, summary = self.read(session_id)
        evicted = max(0, len(messages) - keep)

        if evicted <= covered or evicted - covered < MIN_FOLD:
            return summary                    # nothing new, or not enough yet

        fresh = messages[covered:evicted]
        try:
            summary = self._fold(summary, fresh)
        except Exception as e:                                    # noqa: BLE001
            print(f"  !! Digest fold failed ({type(e).__name__}: {e}); keeping the old one")
            return summary

        self.write(session_id, evicted, summary)
        print(f"  ~~ Digest: folded {len(fresh)} evicted message(s), "
              f"{evicted} covered, {len(summary)} chars")
        return summary

    def _fold(self, existing: str, fresh: list) -> str:
        rendered = "\n".join(render(m) for m in fresh if render(m))
        prompt = FOLD_PROMPT.format(
            limit=MAX_DIGEST_CHARS,
            existing=existing or "(nothing yet — this is the start of the session)",
            new=rendered or "(no readable content)",
        )
        # No tools bound: this call summarises, it does not act. Handing it the
        # tool list would let a summariser start browsing the web.
        from langchain_core.messages import HumanMessage

        with _lock:                       # one fold at a time; they are not cheap
            reply = invoke("base", [HumanMessage(content=prompt)], None)

        text = _text(reply).strip()
        if not text:
            return existing
        return text[:MAX_DIGEST_CHARS]


def render(m) -> str:
    """One evicted message, compact enough to summarise many at once."""
    text = _text(m).strip()

    if m.type == "tool":
        name = getattr(m, "name", "tool")
        body = text[:MAX_TOOL_CHARS] + ("…" if len(text) > MAX_TOOL_CHARS else "")
        return f"[{name} returned] {body}"

    if getattr(m, "tool_calls", None):
        calls = ", ".join(
            f"{c['name']}({_args(c.get('args'))})" for c in m.tool_calls
        )
        said = f" — said: {text[:300]}" if text else ""
        return f"[assistant called {calls}]{said}"

    if not text:
        return ""

    body = text[:MAX_TURN_CHARS] + ("…" if len(text) > MAX_TURN_CHARS else "")
    who = {"human": "USER", "ai": "ASSISTANT", "system": "SYSTEM"}.get(m.type, m.type)
    return f"{who}: {body}"


def _args(args) -> str:
    if not isinstance(args, dict):
        return ""
    parts = []
    for key, value in list(args.items())[:3]:
        rendered = str(value)
        parts.append(f"{key}={rendered[:60]}")
    return ", ".join(parts)


def _text(m) -> str:
    try:
        return m.text or ""
    except Exception:                                             # noqa: BLE001
        return str(getattr(m, "content", "") or "")


def block(summary: str) -> str:
    """The digest as it is handed to the model."""
    return (
        "SESSION RECORD — what has already happened in THIS conversation, "
        "before the messages shown below. It is a compressed record of real "
        "events in this session: treat it as true and as yours.\n"
        "Do not redo work it says is done, do not re-run a search it says was "
        "already tried, and do not ask the user for something it already "
        "records them telling you. Never mention this section.\n\n"
        + summary
    )
