# app/tools/browser.py
"""Stateless web tools over plain HTTP.

`web_search` finds a URL; `browse_web` fetches one and returns its content. Both
are one-shot: an HTTP request, the HTML parsed to text, nothing kept between
calls. There is no browser here — no cookies, no login, no clicking, and no
JavaScript execution, so a page that renders itself client-side will come back
thin or empty. That is the trade: the web tools need no container and no Chrome.
"""

import html as html_module
import re
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from langchain_core.tools import tool

from app.tools import liveweb

REQUEST_TIMEOUT = 30
MAX_TEXT_CHARS = 20_000  # keep one page from swallowing the context window
MAX_LINKS = 60
MAX_RESULTS = 10

# Some sites serve a stub or a block page to anything that looks automated.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

ACTIONS = ("read", "html", "links")

# Everything whose text is markup, script or styling rather than page content.
DROP_TAGS = ("script", "style", "noscript", "template", "svg", "head")


@tool
def web_search(query: str, num_results: int = 5) -> str:
    """
    Search the public web and return ranked results as title and URL.
    Use this when you need information from the internet but do not already know
    the address — then pass the URL you pick to browse_web to read the page.
    This does NOT search the organization's own documents; that is
    list_knowledge_base → search_documents.
    """
    query = (query or "").strip()
    if not query:
        return "Error: no query given."

    num_results = max(1, min(int(num_results or 5), MAX_RESULTS))

    # The live browser first: it reaches Google, and it sees the results a
    # person would. None means Steel is not running, so fall through to the
    # HTTP endpoint below — losing the browser costs rendering, not the web.
    rendered = liveweb.live_search(query, num_results)
    if rendered is not None:
        return rendered

    try:
        results = _search(query)
    except requests.Timeout:
        return f"Error: the search did not come back within {REQUEST_TIMEOUT}s."
    except Exception as e:
        return f"Error: {str(e)}"

    if not results:
        return f"No results for '{query}'. Try different words, or fewer of them."
    lines = [f"Results for '{query}':"]
    for i, result in enumerate(results[:num_results], 1):
        lines.append(f"\n{i}. {result.get('title', '(no title)')}\n   {result.get('url', '')}")
    lines.append("\nRead one of these with browse_web(url, action='read').")
    return "\n".join(lines)


def _search(query: str) -> list[dict]:
    """DuckDuckGo's HTML-only endpoint: no JavaScript, so plain HTTP can read it."""
    response = requests.get(
        "https://lite.duckduckgo.com/lite/",
        params={"q": query},
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return []

    results, seen = [], set()
    for href, text in _iter_links(response.text, response.url):
        url = _unwrap(href)
        title = text.strip()
        if not url.startswith("http") or not title:
            continue
        if "duckduckgo.com" in urlparse(url).netloc or url in seen:
            continue
        seen.add(url)
        results.append({"title": title, "url": url})
    return results


def _unwrap(url: str) -> str:
    """DuckDuckGo hands back /l/?uddg=<real url> redirects; follow them offline."""
    if "duckduckgo.com/l/" not in url and not url.startswith("//duckduckgo.com/l/"):
        return url
    target = parse_qs(urlparse(url).query).get("uddg")
    return unquote(target[0]) if target else url


@tool
def browse_web(url: str, action: str = "read") -> str:
    """
    Fetch a page from the public web and return its content. Use web_search
    first if you do not know the URL. Use this for the public web; use
    search_documents for the organization's own knowledge base.

    action selects what to bring back:
      'read'  — the page as plain text (the default; start here)
      'html'  — the raw HTML, when you need markup, tables or attributes
      'links' — every link on the page, to find the next page to visit

    This is a plain HTTP fetch, not a browser: no cookies, no login, no clicking,
    and no JavaScript. A site that builds its content in the browser will return
    little or nothing here — prefer a page that serves its content directly.
    """
    action = (action or "read").strip().lower()
    if action not in ACTIONS:
        return f"Error: action must be one of {', '.join(ACTIONS)}, got '{action}'."

    url = normalize_url(url)
    if url.startswith("Error:"):
        return url

    rendered = liveweb.live_fetch(url, action)
    if rendered is not None:
        return rendered

    try:
        response = requests.get(
            url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
    except requests.Timeout:
        return f"Error: {url} did not respond within {REQUEST_TIMEOUT}s."
    except requests.RequestException as e:
        return f"Error: could not reach {url} ({str(e)})."

    if not response.ok:
        return f"Error: {url} returned HTTP {response.status_code}."

    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type and "xml" not in content_type and "text" not in content_type:
        return (
            f"Error: {url} is {content_type or 'not text'}, which this tool cannot read. "
            "Only HTML and text pages."
        )

    body = response.text
    final_url = response.url

    if action == "links":
        return format_links(body, final_url)
    if action == "html":
        return truncate(body, final_url)

    title, text = extract_text(body)
    header = f"--- {title or final_url} ---\n"
    if not text:
        return (
            f"Error: {final_url} loaded but had no readable text. "
            "The page probably builds its content with JavaScript, which this tool does not run."
        )
    return header + truncate(text, final_url)


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "Error: no url given."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def extract_text(body: str) -> tuple[str, str]:
    """Strip a page down to its title and its visible text.

    A regex is a poor HTML parser, but the alternative is a dependency for a job
    whose only consumer is a language model that tolerates rough edges.
    """
    match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = _clean(match.group(1)) if match else ""

    text = body
    for tag in DROP_TAGS:
        text = re.sub(rf"<{tag}\b.*?</{tag}>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    # Block-level tags become line breaks so paragraphs and rows stay apart.
    text = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6]|/section|/article)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _clean(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return title, "\n".join(line.strip() for line in text.splitlines()).strip()


def _clean(text: str) -> str:
    return html_module.unescape(text).replace("\xa0", " ").strip()


def _iter_links(body: str, base_url: str):
    """Yield (absolute url, link text) for every <a href> in the page."""
    for match in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", body, re.I | re.S):
        href = html_module.unescape(match.group(1)).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        text = _clean(re.sub(r"<[^>]+>", " ", match.group(2)))
        text = re.sub(r"\s+", " ", text)
        yield urljoin(base_url, href), text


def format_links(body: str, url: str) -> str:
    links, seen = [], set()
    for href, text in _iter_links(body, url):
        if href in seen:
            continue
        seen.add(href)
        links.append(f"- {text or '(no text)'} → {href}")

    if not links:
        return f"No links found on {url}."
    shown = links[:MAX_LINKS]
    note = f"\n\n[Showing {len(shown)} of {len(links)} links.]" if len(links) > len(shown) else ""
    return f"Links on {url}:\n" + "\n".join(shown) + note


def truncate(text: str, url: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return (
        text[:MAX_TEXT_CHARS]
        + f"\n\n[Truncated at {MAX_TEXT_CHARS} characters — the page is {len(text)} long. "
        f"Fetch a more specific page under {urlparse(url).netloc} if you need the rest.]"
    )
