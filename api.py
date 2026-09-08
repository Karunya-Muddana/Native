"""api.py — run with: uvicorn api:app --host 127.0.0.1 --port 8000

Endpoints are `def`, not `async def`, on purpose: the model call and the tool
sandbox are blocking, so FastAPI runs them in its threadpool and the event
loop stays free.
"""

import json
import re
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.config import models as model_config
from app.runtime import runtime as runtime_modes
from app.runtime.runtime import AgentRuntime

ROOT = Path(__file__).parent
FRONTEND = ROOT / "frontend" / "index.html"

# The three directories the agent can actually reach, surfaced to the UI so you
# can see what it has to work with before asking it anything.
WORKSPACE = {
    "input": ROOT / "sandbox_input",
    "output": ROOT / "sandbox_output",
    "knowledge": ROOT / "knowledge_base",
}

app = FastAPI(title="Native by K")
runtime = AgentRuntime()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    # "standard" or "research". Per-turn, not per-session: the mode the user
    # had selected when they pressed send is the mode that turn runs in.
    mode: str | None = None


class SessionRequest(BaseModel):
    title: str | None = None


@app.post("/sessions")
def create_session(body: SessionRequest):
    return {"session_id": runtime.new_session(body.title)}


@app.get("/sessions")
def list_sessions(limit: int = 50, offset: int = 0):
    return {"sessions": runtime.list_sessions(limit, offset)}


@app.get("/sessions/{session_id}/messages")
def get_messages(session_id: str):
    if runtime.sessions.get(session_id) is None:
        raise HTTPException(404, "unknown session")
    return {"session_id": session_id, "messages": runtime.history(session_id)}


@app.patch("/sessions/{session_id}")
def rename_session(session_id: str, body: SessionRequest):
    if runtime.sessions.get(session_id) is None:
        raise HTTPException(404, "unknown session")
    if not body.title or not body.title.strip():
        raise HTTPException(400, "title cannot be empty")
    runtime.sessions.rename(session_id, body.title)
    return {"session_id": session_id, "title": body.title.strip()[:120]}


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    runtime.delete_session(session_id)
    return {"deleted": session_id}


@app.delete("/history")
def clear_history():
    """Erase every stored conversation and both vector indexes.

    Separate from DELETE /sessions/{id}, which removes one analysis: this is the
    whole record — the checkpoint database, the session list, the recalled-memory
    collection in Chroma and the knowledge base index. Sandbox files stay, and so
    do the knowledge base documents themselves; only their index goes.
    """
    from app.tools import workspace as workspace_tools

    return workspace_tools.clear_history()


@app.post("/chat")
def chat(body: ChatRequest):
    """Blocking: returns the final answer plus every step of the turn."""
    return runtime.run(body.message, body.session_id, body.mode)


@app.post("/chat/stream")
def chat_stream(body: ChatRequest):
    """Server-sent events: one `data:` line per model/tool step."""

    def events():
        # An exception raised inside a StreamingResponse generator kills the
        # connection with no response body, so the UI can only say "network
        # error" — which is what a dropped model connection looked like. Turn it
        # into a final event instead, so the turn ends with a stated reason and
        # the steps already streamed stay on screen.
        try:
            for item in runtime.stream(body.message, body.session_id, body.mode):
                yield f"data: {json.dumps(item)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'error': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── models ────────────────────────────────────────────────────────────────
# Local models only. Cloud providers are browsed live through their own APIs;
# this list exists because Ollama publishes no search endpoint.
# A short, opinionated catalogue rather than a search over the whole registry:
# Ollama publishes no search API, and a free-text field that silently returns
# nothing for a typo is worse than a list someone curated. Anything not here can
# still be installed by typing its exact tag.
#
# `caps` is what the registry advertises and is shown before installing; after
# installation the real capabilities come from the model itself, and those are
# the ones that decide eligibility. Where the two disagree, the model wins.
LIBRARY = [
    {"name": "qwen3:14b", "size": "9.3 GB", "caps": ["tools", "thinking"],
     "note": "Strong general orchestrator. Reasons, and calls tools reliably."},
    {"name": "qwen3:8b", "size": "5.2 GB", "caps": ["tools", "thinking"],
     "note": "The same family one size down — a good base on 8 GB of VRAM."},
    {"name": "qwen3:4b", "size": "2.5 GB", "caps": ["tools", "thinking"],
     "note": "Small and quick. Workable base model on modest hardware."},
    {"name": "qwen2.5:14b", "size": "9.0 GB", "caps": ["tools"],
     "note": "No reasoning trace, dependable tool calling."},
    {"name": "llama3.1:8b", "size": "4.9 GB", "caps": ["tools"],
     "note": "Well-tested tool caller, widely deployed."},
    {"name": "mistral-nemo:12b", "size": "7.1 GB", "caps": ["tools"],
     "note": "Long context, solid instruction following."},

    {"name": "hhao/qwen2.5-coder-tools:7b", "size": "4.7 GB", "caps": ["tools"],
     "note": "Coder tuned for tool use — the default for the coder role."},
    {"name": "qwen2.5-coder:14b", "size": "9.0 GB", "caps": ["tools"],
     "note": "Bigger coder. Better at unfamiliar algorithms and real debugging."},

    {"name": "qwen3-vl:8b", "size": "6.0 GB", "caps": ["vision", "tools", "thinking"],
     "note": "Reads diagrams and scans well. The stronger vision option."},
    {"name": "qwen3-vl:4b", "size": "3.3 GB", "caps": ["vision", "tools", "thinking"],
     "note": "The default vision model. Handles P&IDs and gauge photographs."},
    {"name": "llama3.2-vision:11b", "size": "7.8 GB", "caps": ["vision"],
     "note": "Vision only — cannot call tools, so it fits no other role."},
    {"name": "minicpm-v:8b", "size": "5.5 GB", "caps": ["vision"],
     "note": "Compact vision model, good at dense document images."},

    {"name": "deepseek-r1:14b", "size": "9.0 GB", "caps": ["thinking"],
     "note": "Deep reasoner. Ollama does not list tool support for the 14B "
             "distill, so it cannot orchestrate — see the README."},
    {"name": "nomic-embed-text", "size": "0.3 GB", "caps": ["embedding"],
     "note": "Embeddings for the knowledge base and conversation memory. "
             "Required — not selectable for any role."},
]


class ChainUpdate(BaseModel):
    """A role's ordered failover chain, best first."""
    role: str
    chain: list[str]


class ModelName(BaseModel):
    name: str


@app.get("/models")
def get_models():
    """Roles, what fills them, and everything installed with its capabilities."""
    return {**model_config.status(), "library": LIBRARY}


@app.put("/models")
def put_models(body: ChainUpdate):
    from app.models import router
    try:
        chains = model_config.set_chain(body.role, body.chain)
    except ValueError as e:
        # A capability mismatch is the user asking for something impossible,
        # not a server fault — say which model and which capability.
        raise HTTPException(422, str(e))
    router.clear_clients()      # a stale binding must never outlive the change
    return {"roles": chains}


@app.post("/models/refresh")
def refresh_models():
    """Re-query every provider. Free tiers add and retire models constantly."""
    from app.config import providers
    from app.models import router
    providers.invalidate()
    router.clear_clients()
    return model_config.status()


@app.post("/models/unload")
def unload_models():
    return {"unloaded": model_config.unload_all()}


@app.delete("/models/{name:path}")
def delete_model(name: str):
    import ollama
    try:
        ollama.delete(name)
    except Exception as e:
        raise HTTPException(404, f"could not remove {name}: {e}")
    model_config.invalidate()
    return {"removed": name}


@app.post("/models/pull")
def pull_model(body: ModelName):
    """Stream a pull as server-sent events, so the UI can show real progress.

    Ollama reports total and completed bytes per layer; those are forwarded
    as-is rather than reduced to a spinner, because a 9 GB download with no
    numbers on it is indistinguishable from one that has stalled.
    """
    import ollama

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "no model name given")

    def events():
        try:
            for part in ollama.pull(name, stream=True):
                yield f"data: {json.dumps(dict(part))}\n\n"
            model_config.invalidate()
            caps = model_config.capabilities(name)
            yield f"data: {json.dumps({'status': 'done', 'name': name, 'capabilities': caps})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'error': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/modes")
def modes():
    """The run modes the UI can offer, described by the server that runs them."""
    return {
        "default": runtime_modes.DEFAULT_MODE,
        "modes": [
            {
                "id": "standard",
                "label": "Standard",
                "note": "Answers directly, using tools as needed.",
            },
            {
                "id": "research",
                "label": "Research",
                "note": "Reads many sources one at a time, takes notes on each, "
                        "and answers from the notebook with citations. Slow.",
            },
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


MAX_UPLOAD = 64 * 1024 * 1024
ALLOWED_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
    ".xlsx", ".xls", ".csv", ".md", ".txt", ".json", ".py", ".log",
}


def _safe_name(raw: str) -> str:
    """Strip any path from a client-supplied filename and keep it filesystem-legal."""
    name = Path(raw or "").name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(". ")
    return name[:120] or "upload"


@app.post("/files/upload")
async def upload_file(file: UploadFile = File(...)):
    """Land a file in the input sandbox, where every session's tools can reach it.

    Uploading makes a file *available* to the workspace; it does not attach it
    to any conversation. That stays the user's choice, per request.
    """
    name = _safe_name(file.filename)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, f"{suffix or 'that file type'} isn't supported here")

    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file is larger than 64 MB")
    if not data:
        raise HTTPException(400, "that file is empty")

    target = WORKSPACE["input"]
    target.mkdir(parents=True, exist_ok=True)

    # Never silently overwrite an existing file — suffix it instead.
    dest, stem, n = target / name, Path(name).stem, 1
    while dest.exists():
        dest = target / f"{stem} ({n}){suffix}"
        n += 1

    dest.write_bytes(data)
    return {"name": dest.name, "size": len(data), "location": "input"}


# Types the UI can render itself. Everything else stays a download rather than
# something the browser will try to interpret.
INLINE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}


@app.get("/files/{location}/{name}")
def get_file(location: str, name: str):
    """Serve one sandbox file so the UI can show it without leaving the app.

    The name is reduced to its basename before it is used, so a path in the
    request cannot walk out of the sandbox, and the resolved path is checked to
    be inside the directory it claims to be in — the two together mean neither
    a crafted name nor a symlink already sitting in the folder can escape.
    """
    directory = WORKSPACE.get(location)
    if directory is None:
        raise HTTPException(404, f"unknown location '{location}'")

    path = (directory / Path(name).name).resolve()
    try:
        path.relative_to(directory.resolve())
    except ValueError:
        raise HTTPException(403, "that path is outside the workspace")
    if not path.is_file():
        raise HTTPException(404, f"no file named '{Path(name).name}' in {location}")

    suffix = path.suffix.lower()
    media = INLINE_TYPES.get(suffix)
    return FileResponse(
        path,
        media_type=media or "application/octet-stream",
        # An SVG is a document that can carry script, so it is never rendered
        # inline; everything else on the list is inert pixels.
        content_disposition_type="inline" if media and suffix != ".svg" else "attachment",
        headers={"Cache-Control": "no-store"},   # the agent rewrites these files
    )


@app.get("/workspace")
def workspace():
    """Filenames the agent can read or write, per directory. Names and sizes only."""
    out = {}
    for key, directory in WORKSPACE.items():
        files = []
        if directory.is_dir():
            for p in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
                if p.is_file() and not p.name.startswith("."):
                    files.append({"name": p.name, "size": p.stat().st_size})
        out[key] = files
    return out


@app.get("/", response_class=HTMLResponse)
def index():
    """The Native UI. Served same-origin so fetch + SSE need no CORS."""
    return FRONTEND.read_text(encoding="utf-8")