# app/tools/steel.py
"""The live browser — one Steel session, one real Chrome window, one thread.

`browse_web` is a plain HTTP fetch: no cookies, no login, no JavaScript. That
covers reading a public page and nothing else. Everything a person actually does
in a browser — sign in, fill a form, click through three steps of a checkout,
read a table that only exists after the page's own script has run — needs a real
browser holding real state. This file is that browser.

Steel (https://github.com/steel-dev/steel-browser) runs the Chrome. It is asked
for a session over REST, hands back a CDP websocket, and manages the process,
the fingerprint and the profile on disk. We drive that session with Playwright
over CDP, so nothing here reimplements clicking.

Three things are worth knowing before reading the code.

**The window is meant to be visible.** Sessions are created with
`headless: false`, so Chrome opens on the operator's actual desktop and the run
can be watched — and interrupted — as it happens. That is a deliberate choice
rather than a default: an agent that types into forms and creates accounts on
someone's behalf should do it where they can see it, not in a container. Steel
must therefore run on the operator's machine, not in Docker, because a Linux
container on a Windows host has no screen to draw on.

**Playwright's sync API is thread-affine.** Its objects may only be touched from
the thread that created them, and this process serves tool calls from a pool of
threads, so a session created on one call would be unusable on the next. So the
browser lives on one dedicated thread that outlives any single call, and tool
calls submit closures to it. `_Driver` below is that thread and nothing else.

**Elements are addressed by number, not by selector.** A model asked to write a
CSS selector for "the email field" guesses, and a guessed selector fails
silently on the wrong element. So every page read stamps the interactive
elements with `data-nref="1"`, `"2"` … and reports them as a numbered list; the
model then clicks `ref=4`. The number is only valid until the page changes,
which is why every action answers with a freshly numbered list.
"""

import os
import queue
import threading

import requests

STEEL_URL = os.getenv("STEEL_URL", "http://localhost:3000").rstrip("/")
REQUEST_TIMEOUT = 30
CALL_TIMEOUT = 180        # a slow page load, plus room for a redirect chain
MAX_ELEMENTS = 60         # past this the list costs more context than it earns
MAX_LABEL = 90

# Steel is a separate process the operator starts. Everything that can go wrong
# with that is the same failure from here — nothing is listening — so the tools
# say how to fix it rather than reporting a connection error.
NOT_RUNNING = (
    f"Error: no Steel browser at {STEEL_URL}. The live browser needs it running.\n"
    "  git clone https://github.com/steel-dev/steel-browser && cd steel-browser\n"
    "  npm install\n"
    "  CHROME_HEADLESS=false npm run dev      # $env:CHROME_HEADLESS='false' on PowerShell\n"
    "Leave it running in its own terminal. Set STEEL_URL if it is not on "
    f"{STEEL_URL}."
)


# ── the element index ──────────────────────────────────────────────────────
# Injected after every action. It numbers what can actually be interacted with,
# and labels each one the way a person reading the screen would name it, so the
# model picks a field by its visible label rather than by its markup.
INDEX_JS = """
() => {
  document.querySelectorAll('[data-nref]').forEach(e => e.removeAttribute('data-nref'));
  const SEL = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]',
    '[role=tab]', '[role=menuitem]', '[contenteditable=""]',
    '[contenteditable=true]', '[onclick]'
  ].join(',');

  const text = el => {
    // The label a person would use, in the order a person would find it.
    const own = (el.getAttribute('aria-label') || '').trim();
    if (own) return own;
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\\s+/).map(id => document.getElementById(id))
                  .filter(Boolean).map(n => n.innerText).join(' ').trim();
      if (t) return t;
    }
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab && lab.innerText.trim()) return lab.innerText.trim();
    }
    const wrap = el.closest('label');
    if (wrap && wrap.innerText.trim()) return wrap.innerText.trim();
    return (el.placeholder || el.innerText || el.value || el.title ||
            el.name || el.alt || '').trim();
  };

  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;             // laid out but not shown
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) continue;
    if (el.disabled) continue;

    n += 1;
    el.setAttribute('data-nref', String(n));
    const tag = el.tagName.toLowerCase();
    const entry = {
      ref: n,
      tag: tag,
      type: (el.getAttribute('type') || el.getAttribute('role') || '').toLowerCase(),
      label: text(el).replace(/\\s+/g, ' ').slice(0, 200),
      required: !!el.required,
      // Report that a field is filled, never with what — a password read back
      // into the transcript would be written to the checkpoint database.
      // Radios and checkboxes carry a value whether or not they are chosen, so
      // for those the honest signal is `checked` and `filled` would be a lie.
      filled: !['radio', 'checkbox', 'submit', 'button', 'reset'].includes(
                (el.getAttribute('type') || '').toLowerCase())
              && !!(el.value && String(el.value).length),
      secret: (el.getAttribute('type') || '').toLowerCase() === 'password',
      checked: el.checked === true,
      offscreen: r.top > innerHeight || r.bottom < 0
    };
    if (tag === 'select') {
      entry.options = Array.from(el.options).slice(0, 25).map(o => o.text.trim());
    }
    out.push(entry);
  }
  return { url: location.href, title: document.title, elements: out };
}
"""


# ── the thread that owns the browser ───────────────────────────────────────
class _Driver(threading.Thread):
    """Runs sync Playwright for the life of the process and takes jobs by queue.

    Every closure submitted here runs on this one thread, which is what makes a
    session survive across tool calls served from different pool threads.
    """

    def __init__(self):
        super().__init__(daemon=True, name="native-live-browser")
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self.start_error: str = ""

    def run(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.start_error = (
                "Error: Playwright is not installed. It drives the Steel session:\n"
                "  pip install playwright\n"
                "No browser download is needed — `playwright install` is for "
                "Playwright's own Chromium, and this uses Steel's."
            )
            self._ready.set()
            return

        try:
            with sync_playwright() as pw:
                self._ready.set()
                while True:
                    job = self._jobs.get()
                    if job is None:
                        return
                    fn, box = job
                    try:
                        box["value"] = fn(pw)
                    except Exception as e:                       # noqa: BLE001
                        box["error"] = e
                    finally:
                        box["done"].set()
        except Exception as e:                                    # noqa: BLE001
            self.start_error = f"Error: Playwright would not start ({type(e).__name__}: {e})."
            self._ready.set()

    def call(self, fn, timeout: int = CALL_TIMEOUT):
        self._ready.wait(30)
        if self.start_error:
            raise _Unavailable(self.start_error)

        box = {"done": threading.Event(), "value": None, "error": None}
        self._jobs.put((fn, box))
        if not box["done"].wait(timeout):
            # The thread is stuck inside Playwright and there is no safe way to
            # interrupt it. Say so plainly rather than return a wrong answer.
            raise _Unavailable(
                f"Error: the browser did not respond within {timeout}s. The page may "
                "be blocking on a dialog — check the Chrome window, then call "
                "browser_do(action='close') and start again."
            )
        if box["error"]:
            raise box["error"]
        return box["value"]


class _Unavailable(RuntimeError):
    """Carries a ready-to-return error string rather than a stack trace."""


_driver: _Driver | None = None
_driver_guard = threading.Lock()


def _thread() -> _Driver:
    global _driver
    with _driver_guard:
        if _driver is None or not _driver.is_alive():
            _driver = _Driver()
            _driver.start()
        return _driver


# ── session state, only ever touched on the driver thread ──────────────────
_state: dict = {"session": None, "browser": None, "page": None,
                "scratch": None, "viewer": ""}


def _open_session(pw):
    """Create a Steel session, attach to it, and remember the page.

    `headless: false` is the point of the whole file — the operator watches the
    run in a real window. `persist` keeps cookies and logins on disk between
    runs, so an account made in one session is still signed in at the next.
    """
    try:
        response = requests.post(
            f"{STEEL_URL}/v1/sessions",
            json={
                "headless": False,
                "blockAds": True,
                "persist": True,
                "dimensions": {"width": 1440, "height": 900},
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        raise _Unavailable(NOT_RUNNING)

    if not response.ok:
        raise _Unavailable(
            f"Error: Steel refused to open a session (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )

    session = response.json()
    ws = session.get("websocketUrl") or STEEL_URL.replace("http://", "ws://") + "/"
    # Steel binds 0.0.0.0 and reports it back; that is not an address to dial.
    ws = ws.replace("0.0.0.0", "localhost").replace("//host:", "//localhost:")

    browser = pw.chromium.connect_over_cdp(ws, timeout=60_000)
    # Steel opens the session with a context and a page already attached, so
    # making our own would leave a second, invisible window behind.
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(30_000)

    _state.update(
        session=session.get("id", ""),
        browser=browser,
        page=page,
        scratch=None,
        viewer=session.get("sessionViewerUrl", ""),
    )
    return page


def page(pw):
    """The live page, opening a session on first use."""
    current = _state.get("page")
    if current is not None:
        try:
            current.title()            # cheap round trip: is it still there?
            return current
        except Exception:              # noqa: BLE001 — window closed by hand
            _reset()
    return _open_session(pw)


def scratch(pw):
    """A second tab, for reads that are not part of what the model is doing.

    `web_search` and `browse_web` now render in this browser too, and they must
    not navigate the tab holding a half-filled form or a signed-in page. They get
    their own tab in the same session, so cookies and logins are still shared —
    looking something up mid-task is exactly as harmless as opening a new tab.
    """
    existing = _state.get("scratch")
    if existing is not None:
        try:
            existing.title()
            return existing
        except Exception:                                         # noqa: BLE001
            _state["scratch"] = None

    main = page(pw)
    fresh = main.context.new_page()
    fresh.set_default_timeout(30_000)
    _state["scratch"] = fresh
    return fresh


def _reset():
    for key in ("scratch", "browser", "page"):
        target = _state.get(key)
        if target is not None:
            try:
                target.close()
            except Exception:          # noqa: BLE001
                pass
    _state.update(session=None, browser=None, page=None, scratch=None, viewer="")


def submit(fn, timeout: int = CALL_TIMEOUT):
    """Run `fn(pw)` on the browser thread, letting Unavailable through.

    `run` below swallows everything into a string, which is what a tool wants.
    Callers that need to tell "the browser is not there" apart from "the page
    failed" — the plain web tools, which fall back to HTTP for the first and not
    the second — need the exception, so they come here.
    """
    return _thread().call(fn, timeout)


Unavailable = _Unavailable          # the name other modules should use


def run(fn, timeout: int = CALL_TIMEOUT) -> str:
    """Submit `fn(pw)` to the browser thread and turn any failure into a string.

    Tools in this project return errors, they do not raise them, because a raised
    exception ends the turn while a returned one lets the model try something
    else. Everything funnels through here so that is true in one place.
    """
    try:
        return _thread().call(fn, timeout)
    except _Unavailable as e:
        return str(e)
    except Exception as e:                                        # noqa: BLE001
        message = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        if "ECONNREFUSED" in message or "connect_over_cdp" in message.lower():
            return NOT_RUNNING
        return f"Error: {type(e).__name__}: {message[:300]}"


def close() -> str:
    """End the session and release the Chrome window."""
    session_id = _state.get("session")
    _reset()
    if session_id:
        try:
            requests.post(
                f"{STEEL_URL}/v1/sessions/{session_id}/release", timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException:
            pass
        return f"Closed the browser session {session_id}."
    return "No browser session was open."


# ── reporting a page back to the model ─────────────────────────────────────
def describe(page, note: str = "") -> str:
    """What the page is, and everything on it that can be acted on."""
    try:
        index = page.evaluate(INDEX_JS)
    except Exception as e:                                        # noqa: BLE001
        return (
            f"{note}\nThe action ran, but the page could not be read back "
            f"({type(e).__name__}). It may still be loading — call "
            "browser_do(action='wait') and look again."
        ).strip()

    elements = index.get("elements", [])
    lines = []
    if note:
        lines.append(note)
    lines.append(f"Page: {index.get('title') or '(untitled)'}")
    lines.append(f"URL:  {index.get('url')}")

    if not elements:
        lines.append(
            "\nNothing interactive found. The page may still be loading, or its "
            "content may sit inside an iframe this index does not reach — read it "
            "with browser_page(format='text') to see what is actually there."
        )
        return "\n".join(lines)

    lines.append(f"\nInteractive elements ({len(elements)} found), address these by ref:")
    for entry in elements[:MAX_ELEMENTS]:
        lines.append("  " + _element_line(entry))
    if len(elements) > MAX_ELEMENTS:
        lines.append(
            f"  … and {len(elements) - MAX_ELEMENTS} more. Narrow the page down — "
            "scroll, or open the specific page you need — rather than guessing."
        )
    return "\n".join(lines)


def _element_line(entry: dict) -> str:
    kind = entry["tag"]
    if entry.get("type") and kind in ("input", "button"):
        kind = f"{kind}:{entry['type']}"

    label = (entry.get("label") or "").strip()
    label = label[:MAX_LABEL] + "…" if len(label) > MAX_LABEL else label

    marks = []
    if entry.get("required"):
        marks.append("required")
    if entry.get("secret"):
        marks.append("password")
    if entry.get("filled"):
        marks.append("filled")
    if entry.get("checked"):
        marks.append("checked")
    if entry.get("offscreen"):
        marks.append("off-screen")
    if entry.get("options"):
        shown = ", ".join(entry["options"][:6])
        marks.append(f"options: {shown}" + ("…" if len(entry["options"]) > 6 else ""))

    suffix = f"  ({'; '.join(marks)})" if marks else ""
    return f"[{entry['ref']}] {kind} — {label or '(no label)'}{suffix}"
