# app/config/models.py
"""Which model fills which role — as an ordered chain, not a single choice.

Three roles: `base` orchestrates, `coder` is the delegated subagent, `vision`
reads images. `base` and `coder` must be able to call tools, because an
orchestrator that cannot invoke one can do nothing; `vision` must be able to
see. Where a provider reports capabilities those are used; where it does not,
they are inferred and marked unverified.

The part that matters for a system meant to cost nothing: **a role is a list.**
Free tiers rate-limit, and they rate-limit exactly when you are leaning on them.
A single free model as a hard dependency is an assistant that stops working at
two in the afternoon. So each role holds an ordered chain, the first link that
answers wins, and a quota error moves to the next rather than failing the turn.
Local Ollama sits at the end of every chain: slowest and smallest, and the only
link that still works when the network is gone or every tier is spent.

Residency applies only to the local link. Cloud models hold no VRAM, so
acquiring one releases whatever Ollama was holding — which is right anyway,
since a local model doing nothing should not keep 9 GB reserved.
"""

import json
import threading

from app.config import providers
from app.config.settings import BASE_DIR

CONFIG_PATH = BASE_DIR / "app" / "data" / "models.json"

ROLES = ("base", "coder", "vision", "image")
REQUIRED = {"base": "tools", "coder": "tools", "vision": "vision", "image": "imagegen"}

ROLE_NOTE = {
    "base": "Orchestrates the run: reads the request, chooses tools, writes the answer.",
    "coder": "Writes and debugs code when the base model delegates to it.",
    "vision": "Looks at images — diagrams, scans, photographs.",
    "image": "Draws images: sketches, diagrams, illustrations for a report.",
}

# Best free option first. Anything unreachable is skipped at call time, so
# naming a model here costs nothing if that provider is never set up.
DEFAULT_CHAINS = {
    "base": [
        "groq::openai/gpt-oss-120b",
        "gemini::gemini-2.5-flash",
        "groq::qwen/qwen3.8-27b",
        "ollama::qwen3:4b",
    ],
    "coder": [
        "groq::openai/gpt-oss-120b",
        "gemini::gemini-2.5-flash",
        "ollama::hhao/qwen2.5-coder-tools:7b",
    ],
    "vision": [
        "gemini::gemini-2.5-flash",
        "ollama::qwen3-vl:4b",
    ],
    # No local link: nothing in Ollama's catalogue generates images, so this
    # chain is the one that genuinely stops working offline.
    "image": [
        "gemini::gemini-2.5-flash-image",
    ],
}

KEEP_ALIVE = "120s"

_lock = threading.Lock()
_chains: dict | None = None
_resident: str | None = None


# ── selection ──────────────────────────────────────────────────────────────
def chains() -> dict:
    global _chains
    if _chains is None:
        _chains = {r: list(v) for r, v in DEFAULT_CHAINS.items()}
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for role in ROLES:
                value = saved.get(role)
                # A bare string is the older one-model-per-role format, and a
                # name without a provider in it meant Ollama.
                if isinstance(value, str) and value.strip():
                    value = [value.strip()]
                if isinstance(value, list) and value:
                    _chains[role] = [_qualify(str(v)) for v in value if str(v).strip()]
        except (OSError, ValueError):
            pass
    return {r: list(v) for r, v in _chains.items()}


def _qualify(model_id: str) -> str:
    provider, model = providers.split_id(model_id)
    return providers.make_id(provider, model)


def set_chain(role: str, chain: list[str]) -> dict:
    """Replace one role's chain, refusing models that cannot do the job."""
    global _chains
    if role not in ROLES:
        raise ValueError(f"unknown role '{role}'")
    chain = [_qualify(str(c).strip()) for c in chain if str(c).strip()]
    if not chain:
        raise ValueError(f"the {role} chain cannot be empty")

    need = REQUIRED[role]
    problems = []
    for model_id in chain:
        rec = providers.find(model_id)
        if rec is None:
            problems.append(f"{model_id} is not available")
        elif not rec[need]:
            problems.append(f"{model_id} does not support {need}")
    if problems:
        raise ValueError("; ".join(problems))

    current = chains()
    current[role] = chain
    with _lock:
        _chains = current
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return {r: list(v) for r, v in current.items()}


def candidates(role: str) -> list[str]:
    """The chain, filtered to what is reachable and able right now."""
    need = REQUIRED[role]
    out = []
    for model_id in chains()[role]:
        rec = providers.find(model_id)
        if rec and rec[need]:
            out.append(model_id)
    return out


def eligible(role: str) -> list[dict]:
    """Every known model that could hold this role."""
    need = REQUIRED[role]
    return [m for m in providers.all_models() if m[need]]


# ── residency (local models only) ──────────────────────────────────────────
def acquire(model_id: str) -> None:
    """Hold at most one local model at a time.

    A cloud model occupies nothing here, so switching to one is a good moment
    to hand the card back rather than leaving a local model parked in it.
    """
    global _resident
    provider, _ = providers.split_id(model_id)
    local = model_id if provider == "ollama" else None
    with _lock:
        if _resident == local:
            return
        previous, _resident = _resident, local
    if previous:
        release(previous)


def release(model_id: str) -> None:
    import ollama

    _, model = providers.split_id(model_id)
    try:
        ollama.generate(model=model, prompt="", keep_alive=0)
    except Exception:
        pass


def unload_all() -> list[str]:
    global _resident
    with _lock:
        held, _resident = _resident, None
    if held:
        release(held)
    return [held] if held else []


def resident() -> str | None:
    return _resident


# ── status for the UI ──────────────────────────────────────────────────────
def status() -> dict:
    roles = {}
    for role in ROLES:
        live = candidates(role)
        roles[role] = {
            "chain": chains()[role],
            "usable": live,
            "active": live[0] if live else None,
            "requires": REQUIRED[role],
            "note": ROLE_NOTE[role],
        }
    return {
        "roles": roles,
        "models": providers.all_models(),
        "providers": providers.status(),
        "resident": resident(),
        "defaults": DEFAULT_CHAINS,
    }
