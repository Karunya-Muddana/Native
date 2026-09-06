# app/config/providers.py
"""Every place a model can come from, and what each one can actually do.

The premise of this project is a capable agent that costs nothing to run. That
is achievable today only by spreading the work across several free tiers and a
local runtime, because no single free tier is generous enough to be somebody's
everyday assistant on its own. So a provider here is not a preference — it is
one lane of a road, and the point of the road is that traffic keeps moving when
a lane closes.

Five lanes:

  ollama      local. No key, no quota, no network. Slowest and smallest, and
              the only one that still works with the cable pulled — which is
              why it is always last in a chain rather than absent from it.
  groq        very fast, generous free tier, a short list of large models.
  gemini      free tier via an API key, or Vertex credentials if gcloud is
              already logged in on this machine.
  openrouter  the widest selection, and the only provider that publishes real
              per-model capability data. Its `:free` variants cost nothing.
  nvidia      NIM's free developer tier.

Capabilities decide which role a model may hold, and this file is careful to
distinguish two things that look alike: capabilities Ollama and OpenRouter
*report*, and capabilities the others leave us to infer. Inferred ones are
marked unverified so the UI can say so rather than implying a guarantee.
"""

import os
import threading
import time

from dotenv import load_dotenv

load_dotenv()

SEP = "::"          # provider::model — neither half contains it

# Providers that publish machine-readable capability data. Everything else is
# inferred from the model id, and flagged as such.
VERIFIED = {"ollama", "openrouter"}

CACHE_TTL = 300.0
_cache: dict[str, tuple[float, list]] = {}
_lock = threading.Lock()


# ── identity ───────────────────────────────────────────────────────────────
def make_id(provider: str, model: str) -> str:
    return f"{provider}{SEP}{model}"


def split_id(model_id: str) -> tuple[str, str]:
    provider, _, model = str(model_id).partition(SEP)
    return (provider, model) if model else ("ollama", str(model_id))


# ── keys ───────────────────────────────────────────────────────────────────
KEYS = {
    "groq": ("GROQ_KEY", "GROQ_API_KEY"),
    "openrouter": ("OPENROUTER_KEY", "OPENROUTER_API_KEY"),
    "nvidia": ("NVIDIA_NIM_KEY", "NVIDIA_API_KEY"),
    "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


def key_for(provider: str) -> str | None:
    for name in KEYS.get(provider, ()):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def available(provider: str) -> tuple[bool, str]:
    """(usable, why not) — asked before a provider is offered in the UI."""
    if provider == "ollama":
        return True, ""
    if provider == "gemini":
        if key_for("gemini"):
            return True, ""
        # Vertex works off gcloud's application-default credentials, which is
        # how this project was originally set up — no API key in sight.
        if (os.getenv("PROJECT_ID") or "").strip():
            return True, ""
        return False, "set GOOGLE_API_KEY, or PROJECT_ID with gcloud credentials"
    env = KEYS.get(provider, ("",))[0]
    return (True, "") if key_for(provider) else (False, f"set {env} in .env")


META = {
    "ollama":     {"label": "Local",      "note": "Runs on this machine. No quota, works offline."},
    "groq":       {"label": "Groq",       "note": "Very fast. Generous free tier."},
    "gemini":     {"label": "Gemini",     "note": "Google's free tier, or Vertex credentials."},
    "openrouter": {"label": "OpenRouter", "note": "Widest choice. Models tagged :free cost nothing."},
    "nvidia":     {"label": "NVIDIA NIM", "note": "Free developer tier."},
}
ORDER = ("groq", "gemini", "openrouter", "nvidia", "ollama")


# ── capability inference for providers that publish none ───────────────────
# Not chat models at all: transcription, moderation, embedding, speech.
NOT_CHAT = ("whisper", "prompt-guard", "orpheus", "embed", "tts", "guard", "safeguard",
            "moderation", "rerank", "nemoretriever", "safety")
VISION_HINT = ("vision", "-vl", "vl-", "llava", "omni", "multimodal", "pixtral", "maverick", "scout")
NO_TOOLS_HINT = ("deepseek-r1", "-r1-distill", "qwq")


def infer(model_id: str) -> dict:
    name = model_id.lower()
    return {
        "tools": not any(h in name for h in NO_TOOLS_HINT),
        "vision": any(h in name for h in VISION_HINT),
    }


def is_chat(model_id: str) -> bool:
    name = model_id.lower()
    return not any(h in name for h in NOT_CHAT)


# ── listing ────────────────────────────────────────────────────────────────
def models(provider: str) -> list[dict]:
    """Every usable model from one provider, cached for a few minutes."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(provider)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]

    ok, _ = available(provider)
    out = []
    if ok:
        try:
            out = _LISTERS[provider]()
        except Exception:
            out = []          # a provider that is down must not break the page
    with _lock:
        _cache[provider] = (now, out)
    return out


def invalidate(provider: str | None = None) -> None:
    with _lock:
        _cache.pop(provider, None) if provider else _cache.clear()


def all_models() -> list[dict]:
    out = []
    for provider in ORDER:
        out.extend(models(provider))
    return out


def find(model_id: str) -> dict | None:
    provider, _ = split_id(model_id)
    for m in models(provider):
        if m["id"] == model_id:
            return m
    return None


def _rec(provider, model, *, tools, vision, free, context=None, size=0, label=None,
         imagegen=False):
    return {
        "id": make_id(provider, model),
        "provider": provider,
        "model": model,
        "label": label or model,
        "tools": bool(tools),
        "vision": bool(vision),
        # Generating an image is not the same skill as reading one, and almost
        # no model does both. Kept as its own flag so the two never get
        # confused into a single "multimodal" bucket.
        "imagegen": bool(imagegen),
        "free": bool(free),
        "context": context,
        "size": size,
        "verified": provider in VERIFIED,
    }


def _ollama():
    import ollama

    out = []
    for m in ollama.list().get("models") or []:
        name = m.get("model") or m.get("name")
        if not name:
            continue
        try:
            caps = list(ollama.show(name).get("capabilities") or [])
        except Exception:
            caps = []
        if "embedding" in caps:
            continue                      # real, but fills no role here
        out.append(_rec("ollama", name, tools="tools" in caps, vision="vision" in caps,
                        free=True, size=m.get("size") or 0))
    return sorted(out, key=lambda m: m["model"].lower())


def _groq():
    from groq import Groq

    # Cloudflare rejects a bare urllib request to this endpoint; the SDK's
    # client is fingerprinted acceptably, so the SDK is the only way in.
    client = Groq(api_key=key_for("groq"))
    out = []
    for m in client.models.list().data:
        if not is_chat(m.id):
            continue
        caps = infer(m.id)
        out.append(_rec("groq", m.id, tools=caps["tools"], vision=caps["vision"],
                        free=True, context=getattr(m, "context_window", None)))
    return sorted(out, key=lambda m: m["model"].lower())


def _openrouter():
    import httpx

    r = httpx.get("https://openrouter.ai/api/v1/models",
                  headers={"Authorization": f"Bearer {key_for('openrouter')}"}, timeout=30)
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        model_id = m.get("id") or ""
        if not model_id:
            continue
        pricing = m.get("pricing") or {}
        free = str(pricing.get("prompt", "1")) in ("0", "0.0") and model_id.endswith(":free")
        params = m.get("supported_parameters") or []
        modalities = (m.get("architecture") or {}).get("input_modalities") or []
        outputs = (m.get("architecture") or {}).get("output_modalities") or []
        out.append(_rec("openrouter", model_id,
                        tools="tools" in params,
                        vision="image" in modalities,
                        imagegen="image" in outputs,
                        free=free,
                        context=m.get("context_length"),
                        label=m.get("name") or model_id))
    # free first, then alphabetical — the free ones are the point
    return sorted(out, key=lambda m: (not m["free"], m["model"].lower()))


def _nvidia():
    import httpx

    r = httpx.get("https://integrate.api.nvidia.com/v1/models",
                  headers={"Authorization": f"Bearer {key_for('nvidia')}"}, timeout=30)
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        model_id = m.get("id") or ""
        if not model_id or not is_chat(model_id):
            continue
        caps = infer(model_id)
        out.append(_rec("nvidia", model_id, tools=caps["tools"], vision=caps["vision"], free=True))
    return sorted(out, key=lambda m: m["model"].lower())


# Gemini's list endpoint needs the same auth as a call and returns a great deal
# that is not a chat model, so the useful set is short enough to state.
# (id, label, tools, vision, imagegen)
GEMINI_MODELS = [
    ("gemini-2.5-flash", "Gemini 2.5 Flash", True, True, False),
    ("gemini-2.5-pro", "Gemini 2.5 Pro", True, True, False),
    ("gemini-2.0-flash", "Gemini 2.0 Flash", True, True, False),
    ("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite", True, True, False),
    # Draws rather than talks: it returns image parts, and is reached through
    # generate_content rather than a chat client. Imagen is the other option on
    # Vertex but is not enabled on every project, so this is the default.
    ("gemini-2.5-flash-image", "Gemini 2.5 Flash Image", False, True, True),
]


def _gemini():
    return [_rec("gemini", m, tools=t, vision=v, imagegen=g, free=True, label=label)
            for m, label, t, v, g in GEMINI_MODELS]


_LISTERS = {
    "ollama": _ollama,
    "groq": _groq,
    "gemini": _gemini,
    "openrouter": _openrouter,
    "nvidia": _nvidia,
}


# ── clients ────────────────────────────────────────────────────────────────
# Vertex needs a region; both of these serve the image model, and us-central1
# is where this project's other Vertex usage already points.
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")

OPENAI_COMPATIBLE = {
    "openrouter": "https://openrouter.ai/api/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
}


def chat_client(model_id: str, tools: list | None = None, **kw):
    """A LangChain chat model for this id, with tools bound if any are given."""
    provider, model = split_id(model_id)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        from app.config import models as model_config

        client = ChatOllama(model=model, temperature=0, num_ctx=kw.get("num_ctx", 16384),
                            keep_alive=model_config.KEEP_ALIVE, reasoning=False)
    elif provider == "groq":
        from langchain_groq import ChatGroq
        client = ChatGroq(model=model, api_key=key_for("groq"), temperature=0)
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        api_key = key_for("gemini")
        extra = {"google_api_key": api_key} if api_key else {"project": os.getenv("PROJECT_ID")}
        client = ChatGoogleGenerativeAI(model=model, temperature=0, **extra)
    elif provider in OPENAI_COMPATIBLE:
        from langchain_openai import ChatOpenAI
        client = ChatOpenAI(model=model, api_key=key_for(provider),
                            base_url=OPENAI_COMPATIBLE[provider], temperature=0)
    else:
        raise ValueError(f"unknown provider '{provider}'")

    return client.bind_tools(tools) if tools else client


def status() -> list[dict]:
    """Per-provider availability, for the settings panel."""
    out = []
    for provider in ORDER:
        ok, why = available(provider)
        out.append({
            "id": provider,
            "label": META[provider]["label"],
            "note": META[provider]["note"],
            "available": ok,
            "reason": why,
            "verified": provider in VERIFIED,
            "count": len(models(provider)) if ok else 0,
        })
    return out
