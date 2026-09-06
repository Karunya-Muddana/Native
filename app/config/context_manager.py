from langchain_core.messages import (
    BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage,
)
import chromadb
import hashlib
import ollama

from app.config.settings import RAG_MODEL

db_client = chromadb.PersistentClient(path="./chroma")
collection = db_client.get_or_create_collection(name="history")

RECENT_WINDOW = 20
MAX_EMBED_CHARS = 4000
RECALL_RESULTS = 4
SNIPPET_CHARS = 320


def embed_text(text: str) -> list[float]:
    return ollama.embeddings(model=RAG_MODEL, prompt=text[:MAX_EMBED_CHARS])["embedding"]


def message_text(message: BaseMessage) -> str:
    """Flatten structured content (e.g. Gemini's list of parts) to plain text."""
    try:
        return message.text or ""
    except Exception:
        return str(message.content or "")


def _entry_id(message_type: str, text: str) -> str:
    """A stable id derived from the content itself.

    Messages are rehydrated from the checkpointer as fresh objects on every
    request, so anything keyed on object identity re-adds the same turn over
    and over. Hashing the content means a re-add is an upsert, not a duplicate.
    """
    return hashlib.sha256(f"{message_type}\x00{text}".encode("utf-8")).hexdigest()


def add_message_to_db(message: BaseMessage, session_id: str | None = None):
    # Only durable conversation turns go to long-term memory.
    # Tool results are task-local, large, and re-fetchable — storing them
    # pollutes retrieval and risks surfacing stale results as current fact.
    if message.type not in ("human", "ai"):
        return
    if getattr(message, "tool_calls", None):
        return

    text = message_text(message)
    if not text.strip():
        return

    collection.upsert(
        ids=[_entry_id(message.type, text)],
        metadatas=[{
            "content": text,
            "type": message.type,
            "session_id": session_id or "",
        }],
        embeddings=[embed_text(text)],
    )


def retrieval(message: BaseMessage, exclude: set[str]) -> list[BaseMessage]:
    query = message_text(message)
    if not query.strip():
        return []

    results = collection.query(
        query_embeddings=[embed_text(query)],
        n_results=5,
    )

    out = []
    for meta in results["metadatas"][0]:
        content = meta["content"]
        if content in exclude:
            continue
        if meta["type"] == "human":
            out.append(HumanMessage(content=content))
        elif meta["type"] == "ai":
            out.append(AIMessage(content=content))
    return out


def recall_block(
    query: str,
    exclude: set[str],
    current_session: str | None = None,
) -> str | None:
    """Recalled turns rendered as one labelled note, or None if nothing fits.

    Deliberately NOT returned as HumanMessage/AIMessage objects: replaying an
    exchange from another conversation as if it were this one makes the model
    believe it already said those things here. Presenting it as an explicit,
    clearly-labelled note keeps the live transcript honest.
    """
    if not query.strip():
        return None

    try:
        results = collection.query(
            query_embeddings=[embed_text(query)],
            n_results=RECALL_RESULTS,
        )
    except Exception:
        return None                      # memory is an enhancement, never fatal

    metas = (results.get("metadatas") or [[]])[0]
    said, replied = [], []
    for meta in metas:
        content = (meta.get("content") or "").strip()
        if not content or content in exclude:
            continue
        if len(content) > SNIPPET_CHARS:
            content = content[:SNIPPET_CHARS].rstrip() + "…"
        content = " ".join(content.split())

        same = current_session and meta.get("session_id") == current_session
        where = "earlier in this conversation" if same else "an earlier conversation"
        if meta.get("type") == "human":
            said.append(f"- ({where}) The user told you: {content}")
        else:
            replied.append(f"- ({where}) You answered: {content}")

    if not said and not replied:
        return None

    # The user's own statements come first, and are ranked above the assistant's
    # past replies on purpose. Every failed answer is stored too, so a previous
    # "I don't know" is sitting in memory next to the fact that answers it — and
    # without this ordering the model recalls its own refusal and repeats it.
    return (
        "MEMORY — recalled from this user's earlier conversations on this "
        "machine. These did NOT happen in the conversation below; never claim "
        "otherwise, and do not mention this section.\n"
        "What the user told you about themselves or their work is authoritative "
        "and still applies. Your own earlier answers are NOT authoritative — "
        "some were wrong or were given before the user supplied the fact. Where "
        "the two conflict, trust the user. If nothing here bears on the current "
        "question, ignore it silently.\n" + "\n".join(said + replied)
    )


def clear_db():
    ids = collection.get()["ids"]
    if ids:
        collection.delete(ids=ids)
    print("Database context cleared.")


class context_manager:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id
        self.context: list[BaseMessage] = []
        self._seen: set[str] = set()

    def add_message(self, message: BaseMessage):
        # Keyed by content, not id(): the same turn arrives as a new object on
        # every request once it is replayed from the checkpointer.
        text = message_text(message)
        if not text.strip():
            return
        key = _entry_id(message.type, text)
        if key in self._seen:
            return
        self._seen.add(key)
        self.context.append(message)
        add_message_to_db(message, self.session_id)

    def recall(self, query: str, exclude: set[str] | None = None) -> str | None:
        return recall_block(query, exclude or set(), self.session_id)

    def clear_db_context(self):
        clear_db()
        self.context = []
        self._seen = set()

    def get_context_RAG(self, message: BaseMessage, exclude: set[str] | None = None) -> list[BaseMessage]:
        return retrieval(message, exclude or set())

    def build_context(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """
        Keep the system prompt and a contiguous recent window intact, so tool
        calls stay paired with their results. Prepend older recalled turns
        only as background.
        """
        if len(messages) <= RECENT_WINDOW:
            return messages

        system = [m for m in messages[:1] if m.type == "system"]
        recent = messages[-RECENT_WINDOW:]

        # Don't slice through a tool call/result pair
        while recent and recent[0].type == "tool":
            recent = recent[1:]

        in_recent = {str(m.content) for m in recent}
        recalled = retrieval(messages[-1], exclude=in_recent)

        return system + recalled + recent