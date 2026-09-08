# app/tools/livebrowser.py
"""`browser_do` and `browser_page` — a real Chrome the model can operate.

Two tools, not twenty. Steel's API is large — sessions, contexts, proxies,
extensions, fingerprints, credentials, scrape, screenshot, pdf — and exposing
that surface one endpoint per tool would bury the twenty-five tools already here
under plumbing the model would then have to choose between. So the whole of it
reduces to the two things a person actually does at a browser:

    browser_do    change something — go somewhere, click, type, submit
    browser_page  read what is on the screen right now

Everything else is a detail of those two. Session lifecycle is not a decision
the model makes: the first call opens a window, and it stays open across calls
until something closes it.

Both tools answer with the page as it is *after* the action, numbered — which is
the same discipline the authoring tools follow when they reopen a file they just
wrote. The model never has to remember what the page looked like, and it cannot
report a click that did not land, because the proof is in the next result.

The window is visible on the operator's desktop while all this happens; see
app/tools/steel.py for why that is the point rather than an accident.
"""

from langchain_core.tools import tool

from app.tools import steel
from app.tools.authoring.blocks import output_path
from app.tools.readback import describe as describe_file

ACTIONS = ("open", "click", "type", "select", "press", "scroll", "wait", "back", "close")
FORMATS = ("text", "links", "html", "screenshot")

MAX_TEXT_CHARS = 20_000
MAX_HTML_CHARS = 20_000
MAX_LINKS = 80
MAX_WAIT = 30


@tool
def browser_do(action: str, url: str = "", ref: int = 0, text: str = "") -> str:
    """
    Operate a real Chrome window that stays open between calls, holding its
    cookies, its logins and its page. Use this when the job needs a browser
    rather than a fetch: signing in, filling and submitting a form, clicking
    through several steps, or reading a page that builds itself with JavaScript.
    For simply reading a public page, browse_web is faster and cheaper — come
    here when that returns nothing useful, or when the page needs a session.

    The window is visible on the operator's screen. They are watching.

    action is what to do:
      'open'   — go to url. Opens the browser on the first call.
      'click'  — click element ref
      'type'   — type text into field ref, replacing what is there
      'select' — choose the option named text in dropdown ref
      'press'  — press a key: text='Enter', 'Tab', 'Escape', 'PageDown'
      'scroll' — text='down' (default), 'up', 'top' or 'bottom'
      'wait'   — text=seconds to wait, or text to wait for on the page
      'back'   — the browser back button
      'close'  — end the session and release the window

    ref is the number in square brackets from the last result. Those numbers are
    reassigned every time the page changes, so use the ones from the most recent
    result and never a number you remember from earlier.

    Every call answers with the page it produced — its title, its URL and its
    interactive elements, renumbered. Read that before deciding the next step;
    it is what actually happened, not what you intended.

    Passwords and other secrets you type are not echoed back. If you need a
    credential you have not been given, stop and ask the operator rather than
    inventing one. Sites that require a CAPTCHA will stop you here — say so
    instead of retrying.
    """
    verb = str(action or "").strip().lower()
    if verb not in ACTIONS:
        return f"Error: action must be one of {', '.join(ACTIONS)}, got '{action}'."

    if verb == "close":
        return steel.close()

    ref = int(ref or 0)
    text = str(text or "")

    if verb == "open":
        target = str(url or "").strip()
        if not target:
            return "Error: action='open' needs a url."
        if not target.startswith(("http://", "https://")):
            target = "https://" + target.lstrip("/")
        return steel.run(lambda pw: _open(pw, target))

    if verb in ("click", "type", "select") and ref < 1:
        return (
            f"Error: action='{verb}' needs ref — the number in square brackets "
            "beside the element in the last result."
        )
    if verb in ("type", "select") and not text:
        return f"Error: action='{verb}' needs text."

    handlers = {
        "click": lambda pw: _click(pw, ref),
        "type": lambda pw: _type(pw, ref, text),
        "select": lambda pw: _select(pw, ref, text),
        "press": lambda pw: _press(pw, text or "Enter"),
        "scroll": lambda pw: _scroll(pw, text or "down"),
        "wait": lambda pw: _wait(pw, text),
        "back": _back,
    }
    return steel.run(handlers[verb])


@tool
def browser_page(format: str = "text", filename: str = "") -> str:
    """
    Read the page the live browser is currently showing.

    Use this when browser_do's element list is not enough — when you need the
    prose, the data in a table, or a look at the page itself. It reads whatever
    is on screen now; it does not navigate. Open the page with
    browser_do(action='open') first.

    format selects what comes back:
      'text'       — the readable text of the page (the default; start here)
      'links'      — every link, as text and URL, for finding where to go next
      'html'       — the raw HTML, when you need markup, attributes or a table
      'screenshot' — saves a PNG of the page to /sandbox/output/ and reports it.
                     Use it when the page has to be *seen* — a layout, a chart,
                     a rendered document — then look at it with
                     run_through_vision_model, or show the user with
                     open_on_screen. filename names the file.
    """
    kind = str(format or "text").strip().lower()
    if kind not in FORMATS:
        return f"Error: format must be one of {', '.join(FORMATS)}, got '{format}'."
    if kind == "screenshot":
        name = str(filename or "").strip() or "page.png"
        return steel.run(lambda pw: _screenshot(pw, name))
    return steel.run(lambda pw: _read(pw, kind))


# ── actions ────────────────────────────────────────────────────────────────
def _open(pw, target: str) -> str:
    page = steel.page(pw)
    response = page.goto(target, wait_until="domcontentloaded", timeout=60_000)
    _settle(page)
    status = f" (HTTP {response.status})" if response and response.status >= 400 else ""
    return steel.describe(page, f"Opened {target}{status}.")


def _click(pw, ref: int) -> str:
    page = steel.page(pw)
    locator = _locate(page, ref)
    if isinstance(locator, str):
        return locator
    label = _label(page, ref)
    before = page.url

    # A click that opens a new tab leaves the old page in front of us, which
    # looks to the model like a click that did nothing. Watch for the tab and
    # follow it, because that is what a person would see happen.
    context = page.context
    opened = []
    watch = lambda fresh: opened.append(fresh)          # noqa: E731
    context.on("page", watch)
    try:
        locator.click(timeout=20_000)
        _settle(page)
    finally:
        # Removed, always. A listener left behind stays attached to the context
        # for the life of the session, so a long run would accumulate one per
        # click and hand every future tab to a list nothing reads any more.
        context.remove_listener("page", watch)

    if opened:
        fresh = opened[-1]
        fresh.wait_for_load_state("domcontentloaded", timeout=30_000)
        steel._state["page"] = fresh
        return steel.describe(fresh, f"Clicked [{ref}] {label}. It opened a new tab, now in front.")

    note = f"Clicked [{ref}] {label}."
    if page.url != before:
        note += f" The page navigated from {before}"
    return steel.describe(page, note)


def _type(pw, ref: int, text: str) -> str:
    page = steel.page(pw)
    locator = _locate(page, ref)
    if isinstance(locator, str):
        return locator
    label = _label(page, ref)
    secret = (locator.get_attribute("type") or "").lower() == "password"

    locator.click(timeout=20_000)
    locator.fill("", timeout=10_000)
    # Typed rather than filled: a field that validates or autocompletes as you
    # go — most sign-up forms — never sees the keystrokes a fill() skips.
    locator.type(text, delay=25, timeout=30_000)
    _settle(page)

    shown = "•" * len(text) if secret else (text[:60] + ("…" if len(text) > 60 else ""))
    return steel.describe(page, f"Typed into [{ref}] {label}: {shown}")


def _select(pw, ref: int, text: str) -> str:
    page = steel.page(pw)
    locator = _locate(page, ref)
    if isinstance(locator, str):
        return locator
    try:
        locator.select_option(label=text, timeout=20_000)
    except Exception:                                             # noqa: BLE001
        try:
            locator.select_option(value=text, timeout=20_000)
        except Exception:                                         # noqa: BLE001
            options = locator.evaluate(
                "el => Array.from(el.options || []).map(o => o.text).slice(0, 25)"
            )
            listed = ", ".join(options) if options else "none found"
            return f"Error: [{ref}] has no option '{text}'. Its options are: {listed}."
    _settle(page)
    return steel.describe(page, f"Selected '{text}' in [{ref}] {_label(page, ref)}.")


def _press(pw, keys: str) -> str:
    page = steel.page(pw)
    page.keyboard.press(keys.strip())
    _settle(page)
    return steel.describe(page, f"Pressed {keys.strip()}.")


def _scroll(pw, where: str) -> str:
    page = steel.page(pw)
    where = where.strip().lower()
    moves = {
        "top": "window.scrollTo(0, 0)",
        "bottom": "window.scrollTo(0, document.body.scrollHeight)",
        "up": "window.scrollBy(0, -window.innerHeight * 0.8)",
        "down": "window.scrollBy(0, window.innerHeight * 0.8)",
    }
    page.evaluate(moves.get(where, moves["down"]))
    page.wait_for_timeout(600)          # lazy-loaded content gets a moment
    return steel.describe(page, f"Scrolled {where}.")


def _wait(pw, what: str) -> str:
    page = steel.page(pw)
    what = (what or "").strip()

    if not what:
        page.wait_for_timeout(2000)
        return steel.describe(page, "Waited 2s.")

    try:
        seconds = min(float(what), MAX_WAIT)
        page.wait_for_timeout(int(seconds * 1000))
        return steel.describe(page, f"Waited {seconds:g}s.")
    except ValueError:
        pass

    try:
        page.get_by_text(what, exact=False).first.wait_for(timeout=MAX_WAIT * 1000)
        return steel.describe(page, f"'{what}' appeared on the page.")
    except Exception:                                             # noqa: BLE001
        return steel.describe(
            page,
            f"'{what}' did not appear within {MAX_WAIT}s. The page as it stands:",
        )


def _back(pw) -> str:
    page = steel.page(pw)
    before = page.url
    try:
        # 'commit' rather than a load event: a page restored from the back /
        # forward cache never re-fires one, so waiting for it times out on a
        # page that is already back and rendered.
        page.go_back(wait_until="commit", timeout=20_000)
    except Exception:                                             # noqa: BLE001
        pass
    page.wait_for_timeout(400)
    if page.url == before:
        return steel.describe(page, "There was nothing to go back to. Still on:")
    return steel.describe(page, "Went back.")


# ── reading ────────────────────────────────────────────────────────────────
def _read(pw, kind: str) -> str:
    page = steel.page(pw)
    header = f"{page.title() or '(untitled)'}\n{page.url}\n"

    if kind == "links":
        links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                  .map(a => ({ t: a.innerText.trim().replace(/\\s+/g, ' '), h: a.href }))
                  .filter(l => l.h && !l.h.startsWith('javascript:'))"""
        )
        seen, lines = set(), []
        for link in links:
            if link["h"] in seen:
                continue
            seen.add(link["h"])
            lines.append(f"  {link['t'][:70] or '(no text)'} → {link['h']}")
            if len(lines) >= MAX_LINKS:
                break
        if not lines:
            return header + "\nNo links on this page."
        return header + f"\n{len(lines)} link(s):\n" + "\n".join(lines)

    if kind == "html":
        body = page.content()
        return header + "\n" + _clip(body, MAX_HTML_CHARS, "HTML")

    body = page.evaluate(
        """() => {
              const c = document.body.cloneNode(true);
              c.querySelectorAll('script,style,noscript,svg').forEach(e => e.remove());
              return c.innerText.replace(/\\n{3,}/g, '\\n\\n').trim();
        }"""
    )
    if not body.strip():
        return header + (
            "\nThe page has no readable text. It may still be loading — "
            "browser_do(action='wait') — or it may be an image or a PDF, in which "
            "case take a screenshot and look at it with run_through_vision_model."
        )
    return header + "\n" + _clip(body, MAX_TEXT_CHARS, "text")


def _screenshot(pw, filename: str) -> str:
    page = steel.page(pw)
    path = output_path(filename, ".png")
    page.screenshot(path=str(path), full_page=True)
    return (
        f"Saved /sandbox/output/{path.name} — a screenshot of {page.url}\n"
        f"Read back from the saved file:\n  - {describe_file(path)}\n"
        "Look at it with run_through_vision_model, or show it with open_on_screen."
    )


# ── shared ─────────────────────────────────────────────────────────────────
def _locate(page, ref: int):
    """The element the last page read numbered `ref`, or an error string."""
    locator = page.locator(f'[data-nref="{ref}"]')
    try:
        if locator.count() == 0:
            return (
                f"Error: there is no element [{ref}] on this page. The numbers are "
                "reassigned whenever the page changes — call "
                "browser_do(action='wait') to see the page as it is now, then use "
                "a ref from that result."
            )
    except Exception as e:                                        # noqa: BLE001
        return f"Error: could not find element [{ref}] ({type(e).__name__})."
    return locator.first


def _label(page, ref: int) -> str:
    try:
        value = page.evaluate(
            """(r) => {
                  const el = document.querySelector(`[data-nref="${r}"]`);
                  if (!el) return '';
                  return (el.getAttribute('aria-label') || el.placeholder ||
                          el.innerText || el.name || el.title || '')
                         .trim().replace(/\\s+/g, ' ').slice(0, 60);
            }""",
            ref,
        )
        return value or "(no label)"
    except Exception:                                             # noqa: BLE001
        return "(no label)"


def _settle(page):
    """Give the page a moment to finish reacting before it is read back.

    `networkidle` is the honest signal but it never arrives on pages that poll,
    which is most of them, so it is tried briefly and its failure is fine — the
    load state below it is what actually matters.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:                                             # noqa: BLE001
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:                                             # noqa: BLE001
        pass


def _clip(body: str, limit: int, what: str) -> str:
    if len(body) <= limit:
        return body
    return (
        body[:limit]
        + f"\n\n[Truncated at {limit} characters of {len(body)} {what}. "
        "Scroll and read again, or open a more specific page.]"
    )
