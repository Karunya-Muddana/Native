# app/models/router.py
"""Run a role against its chain, moving down it when a link gives out.

This is the piece that makes a free stack usable every day. Free tiers fail in
predictable ways — a quota resets tomorrow, a rate limit resets in a minute, a
model is retired without warning — and all of those are recoverable by asking
somebody else. What is not recoverable is the agent stopping mid-run because
one provider said 429.

Two kinds of failure, treated differently:

  worth retrying elsewhere   rate limits, quota exhaustion, auth problems,
                             retired models, provider outages, timeouts. The
                             request was fine; this provider cannot serve it.

  not worth retrying         the request itself is malformed — a context length
                             overrun, an unsupported argument. Every provider
                             will say the same thing, so failing over just
                             produces the same error more slowly.

A client is built once per model id and reused, because binding twenty-one tool
schemas is not free and the chain is walked on every single turn.
"""

import threading

from app.config import models as model_config
from app.config import providers

# Substrings of the error text that mean "ask someone else". Matching on text
# is crude, but five providers raise five unrelated exception types for the
# same condition and none of them expose a stable code.
RETRY_ON = (
    "rate limit", "rate_limit", "429", "quota", "exceeded", "too many requests",
    "insufficient", "credit", "billing", "payment",
    "unauthorized", "401", "403", "invalid api key", "authentication",
    "not found", "404", "does not exist", "end of life", "410", "decommission",
    "timeout", "timed out", "connection", "unavailable", "503", "502", "500",
    "overloaded", "capacity",
)
# These mean the request is wrong, and it will be wrong everywhere.
NO_RETRY = ("context length", "context_length_exceeded", "maximum context",
            "too long", "invalid_request_error: messages")

_lock = threading.Lock()
_clients: dict[str, object] = {}


class NoModelAvailable(RuntimeError):
    """Every link in the chain was tried and none of them answered."""


def _should_failover(error: Exception) -> bool:
    text = f"{type(error).__name__}: {error}".lower()
    if any(h in text for h in NO_RETRY):
        return False
    return any(h in text for h in RETRY_ON) or isinstance(error, (ConnectionError, TimeoutError))


def client_for(model_id: str, tools: list | None):
    key = f"{model_id}|{len(tools) if tools else 0}"
    with _lock:
        hit = _clients.get(key)
        if hit is not None:
            return hit
    built = providers.chat_client(model_id, tools)
    with _lock:
        _clients[key] = built
    return built


def clear_clients() -> None:
    """Called when the selection changes, so a stale binding is never reused."""
    with _lock:
        _clients.clear()


def invoke(role: str, messages, tools: list | None = None):
    """Ask the first link in `role`'s chain that will answer.

    Returns the model's reply. Raises NoModelAvailable only when every link has
    been tried, with all the reasons attached — one message naming five real
    failures is far more useful than whichever one happened to be last.
    """
    chain = model_config.candidates(role)
    if not chain:
        raise NoModelAvailable(
            f"No model is configured for the {role} role that supports "
            f"'{model_config.REQUIRED[role]}'. Open Models and pick one."
        )

    failures = []
    for model_id in chain:
        try:
            model_config.acquire(model_id)
            reply = client_for(model_id, tools).invoke(messages)
        except Exception as e:
            failures.append(f"{model_id}: {type(e).__name__}: {str(e)[:160]}")
            if _should_failover(e) and model_id != chain[-1]:
                continue
            if not _should_failover(e):
                raise
            break
        else:
            _note_active(role, model_id, failures)
            return reply

    raise NoModelAvailable(
        f"Every model in the {role} chain failed.\n" + "\n".join(f"  - {f}" for f in failures)
    )


# What actually served each role last, so the UI can show it rather than
# showing the first link and hoping.
_active: dict[str, dict] = {}


def _note_active(role: str, model_id: str, failures: list[str]) -> None:
    _active[role] = {"model": model_id, "skipped": failures}


def active() -> dict:
    return dict(_active)
