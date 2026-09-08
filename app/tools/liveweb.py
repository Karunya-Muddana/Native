# app/tools/liveweb.py
"""`web_search` and `browse_web`, done in the real browser instead of over HTTP.

These are not new tools. They are the rendered implementations of the two web
tools that already exist, and `browser.py` calls them first: the model's routing
does not change, the prompt does not change, and research mode does not change —
what changes is that JavaScript now runs, cookie walls clear, and a page that
used to come back empty comes back whole. Search reaches Google rather than
DuckDuckGo's HTML endpoint, because a real browser can ask for it.

Every function here returns `None` to mean one specific thing: *the live browser
is not available, use plain HTTP instead*. That is the whole fallback contract.
A page that genuinely failed — a 404, a timeout, a refused connection — returns
its error as a string rather than None, because silently refetching it over HTTP
would hide the reason and hand back a thinner copy of the same failure.

Reads happen in a scratch tab, never the tab the model is working in. Looking
something up in the middle of a signed-in, half-filled form has to be as
harmless as opening a new tab, because that is exactly what it is.
"""

from urllib.parse import quote_plus

from app.tools import steel

MAX_CHARS = 20_000
MAX_LINKS = 60
SETTLE_MS = 900           # results and late content paint after domcontentloaded

TEXT_JS = """
() => {
  const c = document.body.cloneNode(true);
  c.querySelectorAll('script,style,noscript,svg,iframe').forEach(e => e.remove());
  return c.innerText.replace(/\\n{3,}/g, '\\n\\n').trim();
}
"""

LINKS_JS = """
() => Array.from(document.querySelectorAll('a[href]'))
        .map(a => ({ t: a.innerText.trim().replace(/\\s+/g, ' '), h: a.href }))
        .filter(l => l.h.startsWith('http'))
"""

# Both engines put the result's title in an <h3> inside the result's own anchor.
# That has outlived every other change to either results page, so it is what we
# key on rather than the class names, which change constantly.
RESULTS_JS = """
(host) => {
  const out = [], seen = new Set();
  const sel = 'a h3, a[data-testid="result-title-a"]';
  for (const h of document.querySelectorAll(sel)) {
    const a = h.closest('a');
    if (!a || !a.href.startsWith('http')) continue;
    if (a.href.includes(host)) continue;            // the engine's own links
    if (seen.has(a.href)) continue;
    seen.add(a.href);
    out.push({ title: h.innerText.trim(), url: a.href });
  }
  return out;
}
"""

ENGINES = (
    ("https://www.google.com/search?q=", "google.com", "Google"),
    ("https://duckduckgo.com/?q=", "duckduckgo.com", "DuckDuckGo"),
)


def live_search(query: str, count: int) -> str | None:
    """Search in the real browser. None means the live browser is unavailable."""

    def job(pw):
        tab = steel.scratch(pw)
        for prefix, host, name in ENGINES:
            try:
                tab.goto(prefix + quote_plus(query),
                         wait_until="domcontentloaded", timeout=45_000)
                tab.wait_for_timeout(SETTLE_MS)
                found = tab.evaluate(RESULTS_JS, host)
            except Exception:                                     # noqa: BLE001
                continue          # a blocked engine is a reason to try the other
            if found:
                return name, found
        return "", []

    outcome = _guarded(job)
    if outcome is None or isinstance(outcome, str):
        return outcome            # unavailable, or a real error to report

    name, found = outcome
    if not found:
        return (
            f"No results for '{query}'. Both engines answered without any — one of "
            "them may be showing a consent screen or a robot check, which is "
            "visible in the Chrome window on screen. Try different words, or "
            "browse_web a URL you already know."
        )

    lines = [f"Results for '{query}' (via {name}):"]
    for i, result in enumerate(found[:count], 1):
        lines.append(f"{i}. {result['title']}\n   {result['url']}")
    lines.append("\nA title is a hint, not evidence — open one with browse_web.")
    return "\n".join(lines)


def live_fetch(url: str, action: str) -> str | None:
    """Fetch and render a page. None means the live browser is unavailable."""

    def job(pw):
        tab = steel.scratch(pw)
        response = tab.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            tab.wait_for_load_state("networkidle", timeout=4_000)
        except Exception:                                         # noqa: BLE001
            pass                  # pages that poll never go idle; not an error

        if response is not None and response.status >= 400:
            return f"Error: {url} returned HTTP {response.status}."

        final = tab.url
        header = f"{tab.title() or '(untitled)'}\n{final}\n"

        if action == "html":
            return header + _clip(tab.content(), final)

        if action == "links":
            seen, lines = set(), []
            for link in tab.evaluate(LINKS_JS):
                if link["h"] in seen:
                    continue
                seen.add(link["h"])
                lines.append(f"  {link['t'][:70] or '(no text)'} → {link['h']}")
                if len(lines) >= MAX_LINKS:
                    break
            if not lines:
                return header + "\nNo links on this page."
            return header + f"\n{len(lines)} link(s):\n" + "\n".join(lines)

        body = tab.evaluate(TEXT_JS)
        if not body.strip():
            return header + (
                "\nThe page rendered but has no readable text — it is likely an "
                "image or a PDF. Open it with browser_do and take a screenshot, "
                "then read that with run_through_vision_model."
            )
        return header + "\n" + _clip(body, final)

    return _guarded(job)


def _guarded(job):
    """Run `job` on the browser thread. None only when the browser is unavailable."""
    try:
        return steel.submit(job)
    except steel.Unavailable:
        return None
    except Exception as e:                                        # noqa: BLE001
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        if "ECONNREFUSED" in first or "connect_over_cdp" in first.lower():
            return None
        return f"Error: {type(e).__name__}: {first[:300]}"


def _clip(text: str, url: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS] + f"\n\n[Truncated at {MAX_CHARS} characters. Source: {url}]"
