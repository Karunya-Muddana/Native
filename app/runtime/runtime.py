"""app/runtime/graph.py  (or wherever your AgentRuntime currently lives)

Same graph, same tools, same prompt — but one .run() call is now ONE TURN
instead of an infinite input() loop, and conversation state lives in a
checkpointer keyed by session_id instead of in a Python list.
"""

import sqlite3
import threading
import time
from datetime import date
from typing import Iterator

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.config.context_manager import context_manager
from app.config.prompts import RESEARCH_PROMPT, SYSTEM_PROMPT
from app.config.settings import TOOL_MAX_RETRIES, CHECKPOINT_DB
from app.models.base import ollama_model
from app.runtime.digest import SessionDigest, block as digest_block
from app.runtime.sessions import SessionStore
from app.runtime.state import AgentState
from app.tools.registery import tools

tool_node = ToolNode(list(tools.values()))

RECENT_WINDOW = 30

# Research mode reads sources one at a time, and each one costs a search, a
# fetch and a note — so twenty-five sources is well past the step budget a
# normal turn needs. The window is widened too: the protocol depends on the
# model still being able to see the plan it wrote at the start of the run.
MODES = {
    "standard": {"prompt": "", "steps": 40, "window": RECENT_WINDOW},
    "research": {"prompt": RESEARCH_PROMPT, "steps": 220, "window": 60},
}
DEFAULT_MODE = "standard"

# ── stalling ───────────────────────────────────────────────────────────────
# A turn ends when the model stops calling tools. That is right when it has
# answered, and wrong when it has only *described* the call it was about to
# make — "ACT: I will now browse the repository" with no browse_web attached.
# The graph could not tell those apart, so a narrated action ended the run and
# the user saw the agent stop mid-plan with its reasoning still on screen.
#
# The CHECK/PLAN/ACT prompt makes this more likely, not less: the model writes
# ACT as prose and treats having written it as having acted. An empty reply is
# the same failure with nothing left over.
#
# So a reply that announces an action but carries no tool call is treated as a
# stall and pushed back once, up to MAX_STALLS. The bound is what keeps this
# from becoming a loop: after it, the turn ends as it always did.
MAX_STALLS = 3

INTENT = (
    "i will now", "i'll now", "i am now", "let me now", "next, i will",
    "next i will", "i will proceed", "proceeding to", "i am going to",
    "i will start", "i will search", "i will browse", "i will use",
    "i will call", "i will run", "i will write", "my next action",
)

STALL_NUDGE = (
    "You described a step without taking it, so nothing ran and the user is "
    "still waiting.\n\n"
    "If the next step is a tool, call it NOW — the tool call is the action, and "
    "writing about it is not. If you already have what you need, write the final "
    "answer instead. Do not restate the plan."
)


def stalled(message) -> bool:
    """True when a reply with no tool call was clearly not the end of the turn."""
    text = message_text(message).strip()
    if not text:
        return True                 # an empty reply is never a finished answer

    lowered = text.lower()
    # The prompt's own ACT step, written but not carried out.
    if "act:" in lowered[-800:]:
        return True
    return any(phrase in lowered[-400:] for phrase in INTENT)


def mode_settings(mode: str | None) -> dict:
    return MODES.get(str(mode or "").strip().lower(), MODES[DEFAULT_MODE])

# ── one context_manager per session ────────────────────────────────────────
# These share a single on-disk Chroma collection, so long-term memory spans
# conversations by design — asking the agent to remember something in one
# analysis and referring to it in another is expected to work. What the
# per-session instance holds is the write-dedup set and the session id that
# tags each entry, so recall can say which conversation a note came from.
_memories: dict[str, context_manager] = {}
_memories_lock = threading.Lock()

# Tier 2. Built once, shared: it is stateless apart from the database it writes.
_digest = SessionDigest(CHECKPOINT_DB)


def get_memory(session_id: str) -> context_manager:
    with _memories_lock:
        if session_id not in _memories:
            _memories[session_id] = context_manager(session_id)
        return _memories[session_id]


# ── serialization: LangChain message -> plain dict for JSON responses ──────
def message_text(m) -> str:
    """Flatten a message's content to plain text.

    Gemini replies arrive as a list of structured parts — {"type": "text", ...}
    interleaved with signature blobs in `extras`. `.text` concatenates just the
    text parts; `str(m.content)` would dump the whole structure, blobs included.
    """
    try:
        return m.text or ""
    except Exception:
        return str(m.content or "")


def final_answer(messages) -> str:
    """The last thing the MODEL said — never an internal message.

    messages[-1] used to be good enough. It stopped being so once the nudge
    below started appending a SystemMessage: a turn that ended on one would hand
    the user the nudge text itself, which is instruction meant for the model.
    """
    last = next((m for m in reversed(messages) if m.type == "ai"), None)
    text = message_text(last).strip() if last is not None else ""
    if text:
        return text
    return (
        "The run ended without an answer — the model kept describing its next "
        "step instead of taking it. Ask again, or say 'continue' to pick up "
        "from where it reached."
    )


def serialize_message(m) -> dict:
    return {
        "id": getattr(m, "id", None),
        "role": m.type,                                  # human / ai / tool / system
        "content": message_text(m),
        "name": getattr(m, "name", None),                # tool name on ToolMessage
        "tool_calls": [
            {"id": c["id"], "name": c["name"], "args": c["args"]}
            for c in (getattr(m, "tool_calls", None) or [])
        ],
    }


def log_context(messages, truncated: bool, tiers: dict | None = None):
    total_chars = sum(len(str(m.content or "")) for m in messages)
    approx_tokens = total_chars // 4

    counts = {}
    for m in messages:
        counts[m.type] = counts.get(m.type, 0) + 1
    breakdown = ", ".join(f"{k}={v}" for k, v in counts.items())

    mode = "TRUNCATED" if truncated else "full"
    print(f"[ctx] {len(messages)} msgs ({breakdown}) | ~{approx_tokens} tokens | {mode}")

    # The three tiers, named, so a run's context can be read off the log rather
    # than inferred from the message count.
    if tiers is not None:
        working = tiers["working"]
        total = tiers["total"]
        summary_chars = tiers["summary_chars"]
        t2 = f"{summary_chars} chars covering {tiers['covered']} msg(s)" if summary_chars else "empty"
        t3 = "hit" if tiers["recalled"] else "none"
        print(f"[ctx] tier1 working set : {working} of {total} msg(s), verbatim")
        print(f"[ctx] tier2 session rec : {t2}")
        print(f"[ctx] tier3 long-term   : {t3}")

    for i, m in enumerate(messages):
        content = str(m.content or "").replace("\n", " ")
        preview = content[:70] + ("…" if len(content) > 70 else "")
        marker = ""
        if getattr(m, "tool_calls", None):
            marker = f" [calls: {', '.join(c['name'] for c in m.tool_calls)}]"
        elif m.type == "tool":
            marker = f" [from: {getattr(m, 'name', '?')}]"
        print(f"[ctx]   {i:>2} {m.type:<6}{marker} | {preview}")


# ── nodes ──────────────────────────────────────────────────────────────────
# Nodes now take (state, config). config carries configurable.thread_id,
# which is the session id.

def call_model(state: AgentState, config):
    session_id = config["configurable"]["thread_id"]
    memory = get_memory(session_id)

    # The mode is per-turn, carried on the run config rather than stored in
    # state: switching modes mid-conversation must change this turn only, and a
    # resumed session must not inherit a mode chosen days ago.
    settings = mode_settings(config["configurable"].get("mode"))
    window = settings["window"]

    all_messages = state["messages"]
    truncated = len(all_messages) > window

    if truncated:
        recent = all_messages[-window:]
        # Never start the window on an orphaned tool result.
        while recent and recent[0].type == "tool":
            recent = recent[1:]
    else:
        recent = all_messages

    # Tier 2 — fold anything that just left the working set into this session's
    # running record. Only does real work when enough has accumulated, so most
    # turns pay nothing for it.
    summary = _digest.update(session_id, all_messages, window)

    # Recall runs on EVERY turn, not only once a conversation grows past the
    # window. It used to be inside the `truncated` branch, which meant a fresh
    # conversation — the only place cross-conversation recall actually matters —
    # never queried long-term memory at all.
    in_context = {message_text(m) for m in recent}
    recalled = memory.recall(message_text(all_messages[-1]), exclude=in_context)

    # The system prompt is not stored in state — it's prepended at call time,
    # so a resumed session never carries a stale copy of it.
    # Today's date is prepended, not baked into the prompt text: without it the
    # model resolves a date like "23-9-26" against its training cutoff and
    # searches the wrong year, which looks like a tool failure but isn't.
    today = date.today()
    prompt = f"Today's date is {today:%A, %d %B %Y} ({today:%Y-%m-%d}).\n\n" + SYSTEM_PROMPT
    # The mode's protocol goes last so it has the final word where it contradicts
    # the general instructions — which, in research mode, it is meant to.
    if settings["prompt"]:
        prompt += "\n" + settings["prompt"]
    # The three tiers, in the order the model should trust them. The session's
    # own record comes before cross-session recall on purpose: what happened
    # here is fact, what merely resembles it is a hint.
    preamble = [SystemMessage(content=prompt)]
    if summary:
        preamble.append(SystemMessage(content=digest_block(summary)))
    if recalled:
        preamble.append(SystemMessage(content=recalled))

    messages = preamble + recent

    covered, _ = _digest.read(session_id)
    log_context(messages, truncated, {
        "working": len(recent),
        "total": len(all_messages),
        "summary_chars": len(summary or ""),
        "covered": covered,
        "recalled": bool(recalled),
    })

    response = ollama_model(messages)

    for msg in all_messages[-3:]:
        memory.add_message(msg)
    memory.add_message(response)

    print("Model response:", response.content)

    if response.tool_calls:
        for call in response.tool_calls:
            print(f"  -> Tool call: {call['name']}({call['args']})")

    return {"messages": [response]}


def tools_with_logging(state: AgentState):
    start = time.perf_counter()

    last_error = None
    for attempt in range(TOOL_MAX_RETRIES):
        try:
            result = tool_node.invoke(state)
            break
        except Exception as e:
            last_error = e
            print(f"  !! Tool node raised on attempt {attempt + 1}/{TOOL_MAX_RETRIES}: {e}")
            time.sleep(1)
    else:
        elapsed = time.perf_counter() - start
        print(f"  <- Tool node failed after {TOOL_MAX_RETRIES} attempts in {elapsed:.2f}s")
        # One ToolMessage per pending call, otherwise the next model call is
        # structurally invalid (a tool_call with no matching result).
        return {
            "messages": [
                ToolMessage(
                    content=f"Tool execution failed after {TOOL_MAX_RETRIES} attempts: {last_error}",
                    tool_call_id=call["id"],
                    name=call["name"],
                )
                for call in state["messages"][-1].tool_calls
            ]
        }

    elapsed = time.perf_counter() - start

    result["stalls"] = 0        # it acted; the stall counter starts over

    for msg in result["messages"]:
        tool_name = getattr(msg, "name", "unknown_tool")
        content = str(getattr(msg, "content", ""))
        status = "ERROR" if content.startswith("Error:") else "ok"
        print(f"  <- Tool result: {tool_name} [{status}] ran in {elapsed:.2f}s")

    return result


def nudge(state: AgentState):
    """Tell the model it stopped short, and count it."""
    count = state.get("stalls", 0) + 1
    print(f"  !! Stalled: described an action without calling it (nudge {count}/{MAX_STALLS})")
    return {"messages": [SystemMessage(content=STALL_NUDGE)], "stalls": count}


def tool_call_check(state: AgentState):
    """Where to go after the model speaks.

    A tool call means work to do. No tool call usually means the turn is over —
    unless the model only narrated the step, in which case it is pushed back
    into the loop rather than left hanging mid-plan.
    """
    # Not messages[-1]: the nudge below appends a SystemMessage, so the tail of
    # the conversation is not always the thing the model just said.
    last = next((m for m in reversed(state["messages"]) if m.type == "ai"), None)
    if last is None:
        return END
    if last.tool_calls:
        return "tools"
    if state.get("stalls", 0) < MAX_STALLS and stalled(last):
        return "nudge"
    return END


# ── runtime ────────────────────────────────────────────────────────────────
class AgentRuntime:
    """Build once at app startup, share across requests.

        runtime = AgentRuntime()
        result = runtime.run("what's in the sandbox?", session_id=None)
    """

    def __init__(self, db_path: str = CHECKPOINT_DB, max_steps: int = 40):
        self.max_steps = max_steps

        conn = sqlite3.connect(db_path, check_same_thread=False)
        # Same file is also opened by SessionStore. WAL lets the two connections
        # read/write concurrently; busy_timeout makes a writer wait for a lock
        # instead of raising "database is locked".
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        self.checkpointer = SqliteSaver(conn)
        self.checkpointer.setup()

        self.sessions = SessionStore(db_path)

        # One lock per session: two requests on the same thread_id would
        # otherwise interleave writes into the same checkpoint.
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

        graph = StateGraph(AgentState)
        graph.add_node("call_model", call_model)
        graph.add_node("tools", tools_with_logging)
        graph.add_node("nudge", nudge)

        graph.add_edge(START, "call_model")
        graph.add_conditional_edges(
            "call_model",
            tool_call_check,
            {"tools": "tools", "nudge": "nudge", END: END},
        )
        graph.add_edge("tools", "call_model")
        graph.add_edge("nudge", "call_model")

        self.graph = graph.compile(checkpointer=self.checkpointer)

    # -- helpers ------------------------------------------------------------
    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.Lock())

    def _config(self, session_id: str, mode: str | None = None) -> dict:
        settings = mode_settings(mode)
        return {
            "configurable": {"thread_id": session_id, "mode": mode or DEFAULT_MODE},
            # A mode that reads twenty-five sources needs a step budget to match;
            # the configured max_steps stays the floor for every other turn.
            "recursion_limit": max(self.max_steps, settings["steps"]),
        }

    def _resolve(self, session_id: str | None) -> str:
        if session_id is None:
            return self.sessions.create()
        return self.sessions.ensure(session_id)

    # -- public API ---------------------------------------------------------
    def new_session(self, title: str | None = None) -> str:
        return self.sessions.create(title)

    def run(self, message: str, session_id: str | None = None,
            mode: str | None = None) -> dict:
        """One turn. Blocks until the agent stops calling tools."""
        session_id = self._resolve(session_id)

        with self._lock_for(session_id):
            before = len(self.history(session_id))
            state = self.graph.invoke(
                # stalls is per-turn: the checkpointer would otherwise carry a
                # spent counter into the next turn and disable the guard.
                {"messages": [HumanMessage(content=message)], "stalls": 0},
                self._config(session_id, mode),
            )
            self.sessions.touch(session_id, first_message=message)

        messages = state["messages"]
        return {
            "session_id": session_id,
            "answer": final_answer(messages),
            # Everything produced by this turn, tool calls included — but not the
            # nudge, which is plumbing the user has no reason to read.
            "steps": [
                serialize_message(m) for m in messages[before:] if m.type != "system"
            ],
        }

    def stream(self, message: str, session_id: str | None = None,
               mode: str | None = None) -> Iterator[dict]:
        """Same turn, yielded step by step — feed this to SSE.

        Two stream modes at once. `updates` gives the completed message at the
        end of each node, which is what the run trace is built from and what
        stays authoritative. `messages` gives the model's tokens as it produces
        them, which is the only reason the answer appears to be written rather
        than to arrive.

        The token events are deliberately advisory: every one of them is
        superseded by the `message` event for the same node. A client that
        ignores `token` entirely still renders a correct, complete transcript.
        """
        session_id = self._resolve(session_id)

        with self._lock_for(session_id):
            yield {"event": "session", "session_id": session_id, "mode": mode or DEFAULT_MODE}
            for kind, payload in self.graph.stream(
                # stalls is per-turn: the checkpointer would otherwise carry a
                # spent counter into the next turn and disable the guard.
                {"messages": [HumanMessage(content=message)], "stalls": 0},
                self._config(session_id, mode),
                stream_mode=["updates", "messages"],
            ):
                if kind == "messages":
                    token = self._token(payload)
                    if token:
                        yield {"event": "token", "text": token}
                    continue

                for node, update in payload.items():
                    for m in update.get("messages", []):
                        if m.type == "system":
                            continue          # the nudge: internal, not a step
                        yield {
                            "event": "message",
                            "node": node,
                            "message": serialize_message(m),
                        }
            self.sessions.touch(session_id, first_message=message)
            yield {"event": "done", "session_id": session_id}

    @staticmethod
    def _token(payload) -> str:
        """The text of one model token, or "" for anything not worth sending.

        Filtered on the node, because a tool that runs its own model — the
        coding subagent does — would otherwise pour its private reasoning into
        the answer the user is reading. Only the orchestrator speaks here.
        """
        try:
            chunk, meta = payload
        except (TypeError, ValueError):
            return ""
        if (meta or {}).get("langgraph_node") != "call_model":
            return ""

        text = message_text(chunk)
        # A tool-call chunk carries its arguments, not prose. Those are the
        # trace's business and must not be typed into the answer.
        if not text or getattr(chunk, "tool_calls", None) or getattr(chunk, "tool_call_chunks", None):
            return ""
        return text

    def history(self, session_id: str) -> list[dict]:
        snapshot = self.graph.get_state(self._config(session_id))
        return [serialize_message(m) for m in snapshot.values.get("messages", [])]

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[dict]:
        return self.sessions.list(limit, offset)

    def delete_session(self, session_id: str) -> None:
        try:
            self.checkpointer.delete_thread(session_id)   # langgraph >= 0.2.x
        except AttributeError:
            pass
        _memories.pop(session_id, None)
        self.sessions.delete(session_id)