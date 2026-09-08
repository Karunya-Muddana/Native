# app/tools/imagefind.py
"""`find_image` — search the web for a photograph and save it to the workspace.

The sibling of `generate_image`. A model asked for "a picture of a hard hat"
can draw one, but for anything that must be *real* — an actual piece of
equipment, a real place, a recognisable sign — a drawn picture is a plausible
invention and a found photograph is evidence. This tool covers that half.

Two sources, because they answer different questions:

  web       Bing's image index. Wide, current, and the right answer when the
            question is "what does this actually look like". The results are
            ordinary web images, so they carry whatever rights their owner
            attached — the tool records the page each one came from so a
            caption or credit can be written.
  open      Wikimedia Commons. Narrower and older, but everything in it is
            published for reuse and arrives with a licence string, which is
            what a deck that leaves the building wants.

The download lands in /sandbox/output/ as a real file, so everything downstream
— the viewer, `create_presentation`, `write_document`, `run_through_vision_model`
— picks it up the same way it picks up a generated one.
"""

import html as html_module
import io
import json
import re
from urllib.parse import urlparse

import requests
from langchain_core.tools import tool

from app.tools.authoring.blocks import output_path
from app.tools.readback import describe

REQUEST_TIMEOUT = 30
MAX_BYTES = 12 * 1024 * 1024
# A slide is 13.3 inches wide, so an image that fills one needs real pixels.
# Search results are full of 600 px previews that look fine in a result grid and
# soft on a projector, and they are cheap to reject and try the next one.
MIN_SHORT_EDGE = 400
MIN_LONG_EDGE = 900
MAX_CANDIDATES = 12       # how many results to try before giving up
SOURCES = ("web", "open")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

# Saved with the extension the bytes actually are, not the one the URL claims.
FORMATS = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp"}


@tool
def find_image(query: str, filename: str, source: str = "web", skip: int = 0) -> str:
    """
    Search the web for a real photograph and save it to /sandbox/output/.

    Use this when the picture has to be genuine — real equipment, a real place,
    a real sign, a real product. Use generate_image instead when the picture is
    an illustration, a diagram or an icon, where an invented image is the point.

    query is what to search for; be specific, as you would in a search box
    ("warehouse worker wearing a hard hat", not "safety"). filename is what to
    save it as.

    source picks where to look:
      'web'  — the open web via Bing. Widest choice and the most current, but
               the results are ordinary copyrighted web images. The tool reports
               the page each came from; credit it if the deck leaves the building.
      'open' — Wikimedia Commons. Everything there is published for reuse and
               comes back with its licence, which is the safer choice for
               anything published or sent outside the organisation.

    skip discards that many leading results — use it to get a different picture
    when the first one is not what you wanted, rather than re-running the search.

    The tool downloads candidates in order and keeps the first that is a real,
    large enough image, then reads the saved file back and reports its actual
    size. Small, broken or unreachable results are skipped automatically.
    """
    query = (query or "").strip()
    if not query:
        return "Error: no query given — describe the picture to search for."

    source = str(source or "web").strip().lower()
    if source not in SOURCES:
        return f"Error: source must be 'web' or 'open', got '{source}'."

    skip = max(0, int(skip or 0))

    try:
        candidates = _search(source, query)
        # A near-empty answer from the open web means the search stubbed out,
        # not that the subject has no pictures. Rather than report failure and
        # make the caller work out the retry, ask the other source — it answers
        # different questions but it does answer, and the result says which one
        # actually supplied the picture.
        fallback = ""
        if source == "web" and len(candidates) < 3:
            spare = _commons(query)
            if len(spare) > len(candidates):
                candidates, fallback = spare, "web search returned almost nothing"
    except requests.Timeout:
        return f"Error: the image search did not come back within {REQUEST_TIMEOUT}s."
    except Exception as e:
        return f"Error: the image search failed: {type(e).__name__}: {str(e)[:140]}"

    if not candidates:
        other = "open" if source == "web" else "web"
        return (
            f"No images found for '{query}'. Try plainer words, or source='{other}'."
        )
    if skip >= len(candidates):
        return f"Only {len(candidates)} results for '{query}' — skip={skip} is past the end."

    rejected = []
    for candidate in candidates[skip:skip + MAX_CANDIDATES]:
        data, suffix, why = _fetch(candidate["url"])
        if not data:
            rejected.append(f"{_host(candidate['url'])}: {why}")
            continue

        try:
            path = output_path(filename, suffix)
            path.write_bytes(data)
        except Exception as e:
            return f"Error: {e}"

        lines = []
        if fallback:
            lines.append(f"Note: {fallback}, so this came from Wikimedia Commons instead.")
        lines += [
            f"Saved /sandbox/output/{path.name} from {_host(candidate['url'])}.",
            f"Read back from the saved file:\n  - {describe(path)}",
            f"Source page: {candidate['page'] or candidate['url']}",
        ]
        if candidate.get("title"):
            lines.append(f"Titled: {candidate['title']}")
        if candidate.get("licence"):
            lines.append(f"Licence: {candidate['licence']}")
        else:
            lines.append(
                "Licence unknown — this is an ordinary web image. Credit the source "
                "page if the result is published or sent outside the organisation."
            )
        if rejected:
            lines.append(f"Skipped {len(rejected)} unusable result(s) before this one.")
        # The search occasionally answers with results that have nothing to do
        # with the query. The tool cannot see the picture, so it cannot judge
        # that — but a title sharing not one word with the query is a good
        # enough signal to warn on, rather than let an unrelated photograph go
        # into a deck as though it had been checked.
        if not _overlaps(candidate["title"], query):
            lines.append(
                "WARNING: nothing in this result's title matches the query, so it "
                "may not show what you asked for. Look at it with "
                "run_through_vision_model before using it, or call again with "
                "skip= or source='open'."
            )
        lines.append(
            "Name it in create_presentation or write_document to place it, or show "
            "it with open_on_screen. Call again with skip= to get a different one."
        )
        return "\n".join(lines)

    return (
        f"Error: found {len(candidates)} result(s) for '{query}' but none could be "
        "downloaded as a usable image.\n"
        + "\n".join(f"  - {r}" for r in rejected[:6])
    )


# ── search ─────────────────────────────────────────────────────────────────
def _search(source: str, query: str) -> list[dict]:
    return _commons(query) if source == "open" else _bing(query)


def _bing(query: str) -> list[dict]:
    """Bing's image results page carries each result's metadata in an attribute.

    Every thumbnail is an <a class="iusc"> whose `m` attribute is a JSON blob
    holding the full-size url, the page it appears on and its title. That is far
    steadier than scraping the thumbnails themselves, and it needs no API key —
    which is the whole premise of the other web tools in this project.

    Two endpoints are tried because one of them intermittently answers with a
    stub — a handful of results that have nothing to do with the query, served
    from somebody else's cache. Measured over repeated calls it is not a
    particular query that fails but a particular moment, and the endpoints do
    not fail together, so the better of the two answers is the one to keep.
    """
    attempts = (
        (
            "https://www.bing.com/images/async",
            {"q": query, "first": "0", "count": "35", "mmasync": "1", "adlt": "strict"},
        ),
        (
            "https://www.bing.com/images/search",
            {"q": query, "first": "1", "safesearch": "strict"},
        ),
    )

    # Each endpoint is asked twice before giving up. The stub answer is tied to
    # the moment rather than the query — the same words return 1 result on one
    # call and 12 on the next — so a plain retry recovers most of them, and the
    # early exits below mean a healthy first answer never pays for it.
    best = []
    for pass_number, (url, params) in enumerate(attempts * 2):
        if pass_number >= len(attempts) and len(best) >= 3:
            break
        try:
            response = requests.get(
                url,
                params=params,
                headers={**HEADERS, "Referer": "https://www.bing.com/images/search"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            continue
        if not response.ok:
            continue
        found = _parse_bing(response.text)
        if len(found) > len(best):
            best = found
        if len(best) >= 10:  # a healthy page; no reason to ask twice
            break
    return _by_relevance(best, query)


def _parse_bing(body: str) -> list[dict]:
    out, seen = [], set()
    for blob in re.findall(r'class="iusc"[^>]*\sm="([^"]+)"', body):
        try:
            meta = json.loads(html_module.unescape(blob))
        except ValueError:
            continue
        url = (meta.get("murl") or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "url": url,
                "page": (meta.get("purl") or "").strip(),
                "title": _clean(meta.get("t") or ""),
                "licence": "",
            }
        )
    return out


STOPWORDS = {"a", "an", "the", "of", "in", "on", "at", "and", "or", "with", "for", "to"}


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOPWORDS}


def _overlaps(title: str, query: str) -> bool:
    """True when the title shares a word with the query — or cannot be judged.

    A result with no title, or one written in a script this crude tokeniser does
    not read, is not evidence of a bad match, so it is not reported as one.
    """
    wanted = _terms(query)
    got = _terms(title)
    return not wanted or not got or bool(wanted & got)


def _by_relevance(candidates: list[dict], query: str) -> list[dict]:
    """Re-rank on how much of the query the result's own title accounts for.

    The first hit for a multi-word query is often the highest-traffic page that
    matched one word of it — an office lobby for "construction site workers
    safety gear". Since the tool keeps the first candidate that downloads, that
    ranking becomes the answer. Scoring on title overlap costs nothing and moves
    the results that are about the whole query to the front; Bing's own order
    breaks ties, so this only reorders where there is a reason to.
    """
    terms = _terms(query)
    if not terms:
        return candidates

    def score(entry: dict) -> int:
        return -len(terms & _terms(entry["title"]))  # negative: more overlap first

    return sorted(candidates, key=score)  # stable: ties keep the engine's order


def _commons(query: str) -> list[dict]:
    """Wikimedia Commons, through its own API rather than its HTML.

    `iiurlwidth` asks for a scaled render: the originals are routinely 20 MB and
    5000 px wide, which is a slow download and more resolution than a slide can
    show. The licence comes back in the same call, which is the reason to prefer
    this source when the deck will be published.
    """
    response = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,          # the File: namespace
            "gsrlimit": 20,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "iiurlwidth": 1800,
        },
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return []

    pages = ((response.json().get("query") or {}).get("pages") or {}).values()
    out = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url") or ""
        if not url.startswith("http"):
            continue
        meta = info.get("extmetadata") or {}
        licence = (meta.get("LicenseShortName") or {}).get("value", "")
        artist = _clean(re.sub(r"<[^>]+>", " ", (meta.get("Artist") or {}).get("value", "")))
        out.append(
            {
                "url": url,
                "page": info.get("descriptionurl") or "",
                "title": str(page.get("title", "")).replace("File:", "").strip(),
                "licence": " — ".join(p for p in (licence, artist) if p),
            }
        )
    return out


# ── download ───────────────────────────────────────────────────────────────
def _fetch(url: str) -> tuple[bytes | None, str, str]:
    """Download one candidate and prove it is an image before keeping it.

    Image search results rot: hotlink blocks, redirects to a login page, and
    tracking pixels dressed as photographs are all common. Decoding the bytes is
    the only check that actually settles it, so nothing is written to the
    workspace on the strength of a URL or a Content-Type header alone.

    Returns (bytes, extension, reason it was rejected). The extension comes from
    the decoded bytes rather than the URL, because plenty of image URLs end in
    .php, .ashx or nothing at all.
    """
    try:
        response = requests.get(
            url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True
        )
    except requests.RequestException as e:
        return None, "", type(e).__name__

    if not response.ok:
        return None, "", f"HTTP {response.status_code}"

    length = response.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > MAX_BYTES:
        return None, "", f"{int(length) // 1_000_000} MB is too large"

    data = b""
    for chunk in response.iter_content(64 * 1024):
        data += chunk
        if len(data) > MAX_BYTES:
            return None, "", "larger than the size limit"
    if not data:
        return None, "", "empty response"

    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        image.verify()
        width, height = image.size
        kind = (image.format or "").upper()
    except Exception:
        return None, "", "not a readable image"

    if min(width, height) < MIN_SHORT_EDGE or max(width, height) < MIN_LONG_EDGE:
        return None, "", f"only {width}x{height}"
    if kind not in FORMATS:
        return None, "", f"{kind or 'unknown'} format"
    return data, FORMATS[kind], ""



def _host(url: str) -> str:
    return urlparse(url).netloc or url


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_module.unescape(str(text))).strip()
