# app/tools/notes.py
"""The research notebook — `note_source` and `review_notes`.

A research run that keeps its findings in the conversation loses them. The
context window is trimmed to a recent slice on every turn, so by the twentieth
source the first fifteen have scrolled out of the model's reach; what it then
writes is a summary of the last few pages and a memory of the rest. That is the
failure this file exists to prevent.

So the notes live on disk instead. Each source is appended to a markdown file in
/sandbox/output/ the moment it is read, numbered `[S1]`, `[S2]` … with its URL
beside it. Two things follow from that:

  - The notebook survives the context window. Twenty-five sources can be read in
    one run and every one of them is still legible at the end, because the model
    re-reads the file rather than remembering it.
  - The citations are real. `[S7]` resolves to a line in a file that names the
    page it came from, so a claim in the final answer can be checked rather than
    trusted — which is the same reason the run trace exists in this project.

`note_source` deliberately answers with the running state of the whole notebook,
not just "saved". After each source the model is told how many it has, which
ones they were, and what it said was still open — so the decision about whether
to keep going is made against the record instead of against a recollection.
"""

import re
from datetime import datetime

from langchain_core.tools import tool

from app.tools.paths import OUTPUT_DIR

MAX_SOURCES = 40          # a hard stop; the prompt asks for far fewer
MAX_REVIEW_CHARS = 24_000
SUFFIX = "_notes.md"


def _slug(topic: str) -> str:
    """One topic, one file, across every call in the run."""
    text = re.sub(r"[^a-z0-9]+", "_", str(topic or "").lower()).strip("_")
    return (text or "research")[:60]


def _path(topic: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{_slug(topic)}{SUFFIX}"


def _entries(body: str) -> list[dict]:
    """Parse the notebook back into its sources.

    The file is the single source of truth for what has been covered, so the
    count and the duplicate check are read off it rather than kept in memory —
    a run that is resumed, or that loses its context, still knows where it got to.
    """
    out = []
    for block in re.split(r"\n(?=## \[S\d+\])", body):
        head = re.match(r"## \[S(\d+)\]\s*(.*)", block)
        if not head:
            continue
        url = re.search(r"^- \*\*Source:\*\*\s*(\S+)", block, re.M)
        out.append(
            {
                "n": int(head.group(1)),
                "title": head.group(2).strip(),
                "url": (url.group(1) if url else "").strip(),
            }
        )
    return out


def _read(topic: str) -> tuple[str, list[dict]]:
    path = _path(topic)
    body = path.read_text(encoding="utf-8") if path.exists() else ""
    return body, _entries(body)


def _bullets(text: str) -> list[str]:
    """Accept a list, a newline block, or a paragraph — models send all three."""
    if isinstance(text, (list, tuple)):
        items = [str(t) for t in text]
    else:
        items = str(text or "").splitlines()
    return [re.sub(r"^\s*[-*•\d.]+\s*", "", line).strip()
            for line in items if line.strip()]


@tool
def note_source(
    topic: str,
    url: str,
    title: str,
    summary: str,
    findings: str = "",
    open_questions: str = "",
) -> str:
    """
    Write one source into the research notebook for `topic`, and report where
    the whole notebook now stands.

    Call this immediately after reading each page, before opening the next one.
    Everything worth keeping from that page goes in here, because this file —
    not the conversation — is what you will write the final answer from.

    topic groups the notes; use the same string for every source in one piece of
    research. url and title identify the source and become its citation. summary
    is what the page actually said, in your own words and in enough detail to
    write from later — several sentences, not one line. findings is the specific
    claims, numbers, dates or definitions worth quoting, one per line.
    open_questions is what this page did not settle, one per line.

    Each source is numbered [S1], [S2] … Cite those markers in your final answer.
    """
    topic = str(topic or "").strip()
    if not topic:
        return "Error: no topic given — pass the same topic string for every source."
    url = str(url or "").strip()
    if not url:
        return "Error: no url given — a note without its source cannot be cited."
    summary = str(summary or "").strip()
    if len(summary) < 40:
        return (
            "Error: the summary is too short to be worth keeping. Write what the "
            "page actually said, in enough detail to write the answer from later."
        )

    body, existing = _read(topic)

    already = next((e for e in existing if e["url"] == url), None)
    if already:
        return (
            f"Already recorded as [S{already['n']}]: {already['title'] or url}\n"
            f"{_state(topic, existing)}\n"
            "Nothing was written. Open a source you have not read yet."
        )
    if len(existing) >= MAX_SOURCES:
        return (
            f"The notebook already holds {len(existing)} sources, which is the limit. "
            "Stop searching and write the answer from review_notes."
        )

    number = len(existing) + 1
    path = _path(topic)

    if not body:
        body = (
            f"# Research notes — {topic}\n\n"
            f"Started {datetime.now():%Y-%m-%d %H:%M}. "
            "One section per source; cite them as [S1], [S2] …\n"
        )

    lines = [
        f"\n## [S{number}] {str(title or '').strip() or url}",
        f"- **Source:** {url}",
        f"- **Read:** {datetime.now():%Y-%m-%d %H:%M}",
        "",
        summary,
    ]

    found = _bullets(findings)
    if found:
        lines += ["", "**Specifics**"] + [f"- {f}" for f in found]

    open_items = _bullets(open_questions)
    if open_items:
        lines += ["", "**Still open**"] + [f"- {q}" for q in open_items]

    body = body.rstrip() + "\n" + "\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")

    covered = existing + [{"n": number, "title": title, "url": url}]
    return (
        f"Recorded as [S{number}] in /sandbox/output/{path.name}.\n"
        f"{_state(topic, covered)}\n"
        + (
            "Open questions are still listed — look for sources that settle them.\n"
            if open_items else ""
        )
        + "Read the next source, or call review_notes when you have enough to answer."
    )


def _state(topic: str, entries: list[dict]) -> str:
    """What the notebook holds right now, in the tool's own words.

    Returned after every write so the model's sense of its own progress is
    refreshed from the file on each step, rather than drifting.
    """
    if not entries:
        return "The notebook is empty."
    listed = "\n".join(
        f"  [S{e['n']}] {(e['title'] or e['url'])[:70]}" for e in entries[-8:]
    )
    more = f"  … and {len(entries) - 8} earlier\n" if len(entries) > 8 else ""
    return f"{len(entries)} source(s) on '{topic}' so far:\n{more}{listed}"


@tool
def review_notes(topic: str, full: bool = True) -> str:
    """
    Read the research notebook for `topic` back from disk.

    Do this before writing the final answer. The notebook is the record of what
    you actually read; the conversation above you has been trimmed and no longer
    holds the early sources. Write the answer from what this returns, and cite
    the [S…] markers it shows.

    full=False returns only the list of sources, for checking coverage mid-run
    without pulling the whole notebook into the conversation.
    """
    topic = str(topic or "").strip()
    body, entries = _read(topic)
    if not body:
        return (
            f"No notes yet for '{topic}'. Nothing has been recorded — "
            "read a source and call note_source first."
        )

    path = _path(topic)
    if not full:
        return f"/sandbox/output/{path.name}\n{_state(topic, entries)}"

    header = f"Research notes for '{topic}' — {len(entries)} source(s), /sandbox/output/{path.name}\n"
    if len(body) > MAX_REVIEW_CHARS:
        # Keeping the tail rather than the head would drop the earliest sources,
        # which are the ones the conversation has already forgotten — the exact
        # thing this notebook exists to hold on to.
        body = body[:MAX_REVIEW_CHARS] + (
            f"\n\n[Truncated at {MAX_REVIEW_CHARS} characters of {len(body)}. "
            f"Read the rest with read_text_file('{path.name}', location='output').]"
        )
    return header + "\n" + body
