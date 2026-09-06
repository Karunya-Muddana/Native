# app/tools/authoring/blocks.py
"""The content spec the three authoring tools share, and how they check their work.

A model writing a document has to describe it in one argument. The shape used
here is a list of blocks — heading, paragraph, bullets, table, image, pagebreak
— because it is the smallest vocabulary that covers an inspection note, and
because a flat list is something a 7B model gets right on the first attempt in a
way that nested layout objects are not.

The spec arrives as JSON text. Local models are inconsistent about whether they
send a JSON string or an already-decoded list, so `parse_blocks` takes both.

Everything in this file is deliberately strict about *reporting* rather than
guessing: a block it cannot read is named in the error along with the shape it
expected, so the next attempt is informed rather than another guess.
"""

import json
from pathlib import Path

from app.tools.paths import OUTPUT_DIR, resolve_file

BLOCK_TYPES = ("heading", "paragraph", "bullets", "table", "image", "pagebreak")

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff")

SPEC_HELP = (
    "content must be a JSON list of blocks, each with a 'type':\n"
    '  {"type": "heading", "text": "Findings", "level": 2}\n'
    '  {"type": "paragraph", "text": "..."}\n'
    '  {"type": "bullets", "items": ["...", "..."]}\n'
    '  {"type": "table", "header": ["Point", "mm"], "rows": [["P1", 10.9]]}\n'
    '  {"type": "image", "filename": "chart.png", "caption": "..."}\n'
    '  {"type": "pagebreak"}'
)


class SpecError(Exception):
    """The content spec could not be read. The message is for the model."""


# ── parsing ────────────────────────────────────────────────────────────────
def parse_blocks(content) -> list[dict]:
    """Turn whatever the model sent into a validated list of block dicts."""
    blocks = _decode(content)

    if isinstance(blocks, dict):
        blocks = [blocks]  # one block, unwrapped — accept it rather than refuse
    if not isinstance(blocks, list):
        raise SpecError(f"content must be a list of blocks, got {type(blocks).__name__}. {SPEC_HELP}")
    if not blocks:
        raise SpecError(f"content is empty — there is nothing to write. {SPEC_HELP}")

    return [_check(block, i) for i, block in enumerate(blocks, 1)]


def _decode(content):
    if isinstance(content, (list, dict)):
        return content
    if not isinstance(content, str):
        raise SpecError(f"content must be JSON text or a list, got {type(content).__name__}.")

    text = content.strip()
    if not text:
        raise SpecError(f"content is empty. {SPEC_HELP}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SpecError(
            f"content is not valid JSON ({e.msg} at line {e.lineno}, column {e.colno}). {SPEC_HELP}"
        )


def _check(block, position: int) -> dict:
    where = f"block {position}"
    if isinstance(block, str):
        # A bare string is unambiguous enough to accept as a paragraph.
        return {"type": "paragraph", "text": block}
    if not isinstance(block, dict):
        raise SpecError(f"{where} is a {type(block).__name__}, not an object. {SPEC_HELP}")

    kind = str(block.get("type", "")).strip().lower()
    if not kind:
        raise SpecError(f"{where} has no 'type'. One of: {', '.join(BLOCK_TYPES)}.")
    if kind not in BLOCK_TYPES:
        raise SpecError(f"{where} has type '{kind}', which is not one of: {', '.join(BLOCK_TYPES)}.")

    if kind in ("heading", "paragraph"):
        text = block.get("text", block.get("content", ""))
        if not str(text).strip():
            raise SpecError(f"{where} is a {kind} with no 'text'.")
        out = {"type": kind, "text": str(text)}
        if kind == "heading":
            out["level"] = _level(block.get("level", 1), where)
        return out

    if kind == "bullets":
        items = block.get("items", block.get("text", []))
        if isinstance(items, str):
            items = [line for line in items.splitlines() if line.strip()]
        if not isinstance(items, list) or not items:
            raise SpecError(f'{where} is bullets with no "items" list. Expected {{"items": ["a", "b"]}}.')
        return {"type": "bullets", "items": [str(i) for i in items]}

    if kind == "table":
        return _table(block, where)

    if kind == "image":
        filename = str(block.get("filename", block.get("file", ""))).strip()
        if not filename:
            raise SpecError(f"{where} is an image with no 'filename'.")
        return {
            "type": "image",
            "filename": filename,
            "location": str(block.get("location", "output")).strip().lower() or "output",
            "caption": str(block.get("caption", "")).strip(),
        }

    return {"type": "pagebreak"}


def _table(block: dict, where: str) -> dict:
    rows = block.get("rows", block.get("data"))
    if not isinstance(rows, list) or not rows:
        raise SpecError(f'{where} is a table with no "rows". Expected {{"rows": [["a", 1], ["b", 2]]}}.')

    header = block.get("header", block.get("headers"))
    if header is not None and not isinstance(header, list):
        raise SpecError(f"{where} has a 'header' that is not a list.")

    normalized = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, list):
            raise SpecError(f"{where}, row {i} is a {type(row).__name__}, not a list of cells.")
        normalized.append([_cell(c) for c in row])

    # Ragged rows are padded rather than rejected: the model has usually got the
    # data right and simply omitted a trailing empty cell, and losing the whole
    # document over that is a worse outcome than a blank cell.
    width = max([len(r) for r in normalized] + [len(header) if header else 0])
    for row in normalized:
        row += [""] * (width - len(row))
    if header:
        header = [_cell(c) for c in header] + [""] * (width - len(header))

    return {"type": "table", "header": header, "rows": normalized, "width": width}


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _level(value, where: str) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        raise SpecError(f"{where} has a non-numeric heading 'level'.")
    return max(1, min(level, 4))


# ── output paths ───────────────────────────────────────────────────────────
def output_path(filename: str, suffix: str) -> Path:
    """Where a generated file goes, with the right extension, inside the sandbox.

    Any directory part of the name is discarded: everything this project writes
    lands in /sandbox/output/, and accepting a path would let a caller write
    outside it.
    """
    name = Path(str(filename or "").strip()).name
    if not name:
        raise SpecError("no filename given.")
    if not name.lower().endswith(suffix):
        name += suffix
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / name


def image_path(block: dict) -> Path:
    """Resolve an image block to a real file, or explain why it isn't one."""
    allowed = ("output", "input")
    location = block["location"] if block["location"] in allowed else "output"
    found = resolve_file(block["filename"], location, allowed=allowed)
    if isinstance(found, str):
        # resolve_file already phrases this as an error; the caller adds its own
        # prefix, so drop that one rather than saying "Error: Error:".
        raise SpecError(found.removeprefix("Error: "))
    if found.suffix.lower() not in IMAGE_SUFFIXES:
        raise SpecError(
            f"'{found.name}' is not an image ({', '.join(IMAGE_SUFFIXES)}). "
            "Charts come from python_runner; a page image comes from extract_pdf_images."
        )
    return found


def size_note(path: Path) -> str:
    kb = max(1, path.stat().st_size // 1024)
    return f"{kb} KB"
