# app/tools/authoring/presentation.py
"""`create_presentation` — a .pptx deck for management, from work already done.

The last step of an investigation is usually explaining it to someone who will
not read the evidence. This turns the conclusions into slides: a title slide,
then one slide per point, each carrying bullets, a table, or a chart that
python_runner rendered earlier.

Layout is deliberately not negotiable. The model supplies content and picks a
slide type; where things sit on the slide is decided here, because a model
asked to place boxes produces overlapping text, and a deck with overlapping
text is worse than no deck.
"""

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt
from langchain_core.tools import tool

from app.tools.authoring.blocks import SpecError, _decode, image_path, output_path, size_note

BLANK, TITLE_ONLY, TITLE_SLIDE = 6, 5, 0

SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)  # 16:9
MARGIN = Inches(0.7)
BODY_TOP = Inches(1.7)
BODY_H = SLIDE_H - BODY_TOP - Inches(0.6)
BODY_W = SLIDE_W - (2 * MARGIN)

ACCENT = RGBColor(0x1F, 0x38, 0x64)
INK = RGBColor(0x22, 0x22, 0x22)
MUTED = RGBColor(0x66, 0x66, 0x66)

MAX_BULLETS = 7  # past this a slide is a document with a title on it

SLIDE_HELP = (
    'slides must be a JSON list. Each slide has a "title" and one kind of body:\n'
    '  {"title": "Findings", "bullets": ["P4 at 10.9 mm", "Below the 11.0 mm limit"]}\n'
    '  {"title": "Measurements", "header": ["Point", "mm"], "rows": [["P1", 12.4]]}\n'
    '  {"title": "Wall loss", "image": "thickness_chart.png"}\n'
    '  {"title": "Section break"}\n'
    'Any slide may also carry "notes" for the speaker.'
)


@tool
def create_presentation(filename: str, title: str, slides: str, subtitle: str = "") -> str:
    """
    Create a PowerPoint deck (.pptx) in /sandbox/output/ — a management summary,
    an investigation walkthrough, a findings briefing.

    title and subtitle become the opening slide. slides is a JSON list, one
    object per slide after it, each with a title and one kind of body:
      {"title": "Findings", "bullets": ["P4 measured 10.9 mm", "Limit is 11.0 mm"]}
      {"title": "Measurements", "header": ["Point", "mm"], "rows": [["P1", 12.4]]}
      {"title": "Wall loss trend", "image": "thickness_chart.png", "caption": "..."}
      {"title": "Recommendation", "bullets": [...], "notes": "say this out loud"}

    An image slide embeds a file already in /sandbox/output/ or /sandbox/input/:
    render charts with python_runner first, then name them here. Keep bullets
    short — seven per slide is the maximum, and the tool says so if you exceed
    it rather than silently overflowing the slide.

    Use this when the user asks to present, brief, or summarize for management.
    For something to be signed or filed, use write_document. The tool reads the
    saved deck back and reports each slide as it actually exists.
    """
    try:
        specs = _slides(slides)
        path = output_path(filename, ".pptx")
    except SpecError as e:
        return f"Error: {e}"

    deck = Presentation()
    deck.slide_width, deck.slide_height = SLIDE_W, SLIDE_H

    _title_slide(deck, (title or "").strip(), (subtitle or "").strip())

    warnings = []
    try:
        for index, spec in enumerate(specs, 1):
            warnings += _slide(deck, spec, index)
    except SpecError as e:
        return f"Error: {e} Nothing was written."

    try:
        deck.save(str(path))
    except PermissionError:
        return (
            f"Error: {path.name} is open in PowerPoint and could not be replaced. "
            "Ask the user to close it."
        )

    return _read_back(path, warnings)


# ── input ──────────────────────────────────────────────────────────────────
def _slides(slides) -> list[dict]:
    data = _decode(slides)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise SpecError(f"no slides given. {SLIDE_HELP}")

    out = []
    for i, slide in enumerate(data, 1):
        if isinstance(slide, str):
            out.append({"title": slide, "bullets": [], "notes": ""})
            continue
        if not isinstance(slide, dict):
            raise SpecError(f"slide {i} is a {type(slide).__name__}, not an object. {SLIDE_HELP}")

        bullets = slide.get("bullets", slide.get("points", []))
        if isinstance(bullets, str):
            bullets = [line for line in bullets.splitlines() if line.strip()]
        if not isinstance(bullets, list):
            raise SpecError(f"slide {i} has 'bullets' that is not a list.")

        rows = slide.get("rows", slide.get("data"))
        if rows is not None and not isinstance(rows, list):
            raise SpecError(f"slide {i} has 'rows' that is not a list.")

        out.append(
            {
                "title": str(slide.get("title", slide.get("heading", ""))).strip(),
                "bullets": [str(b) for b in bullets if str(b).strip()],
                "header": slide.get("header", slide.get("headers")),
                "rows": rows,
                "image": str(slide.get("image", slide.get("filename", ""))).strip(),
                "location": str(slide.get("location", "output")).strip().lower(),
                "caption": str(slide.get("caption", "")).strip(),
                "notes": str(slide.get("notes", "")).strip(),
            }
        )
    return out


# ── slides ─────────────────────────────────────────────────────────────────
def _title_slide(deck: Presentation, title: str, subtitle: str) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[TITLE_SLIDE])
    slide.shapes.title.text = title or "Untitled"
    _restyle(slide.shapes.title.text_frame, size=40, bold=True, color=ACCENT)

    if len(slide.placeholders) > 1:
        placeholder = slide.placeholders[1]
        if subtitle:
            placeholder.text = subtitle
            _restyle(placeholder.text_frame, size=18, color=MUTED)
        else:
            # An empty placeholder still prints its prompt text when presented.
            placeholder._element.getparent().remove(placeholder._element)


def _slide(deck: Presentation, spec: dict, index: int) -> list[str]:
    slide = deck.slides.add_slide(deck.slide_layouts[TITLE_ONLY])
    warnings = []

    slide.shapes.title.text = spec["title"] or f"Slide {index + 1}"
    title = slide.shapes.title
    title.left, title.top, title.width = MARGIN, Inches(0.5), BODY_W
    title.height = Inches(1.0)
    _restyle(title.text_frame, size=28, bold=True, color=ACCENT)

    if spec["image"]:
        warnings += _image_body(slide, spec)
    elif spec["rows"]:
        _table_body(slide, spec)
    elif spec["bullets"]:
        warnings += _bullet_body(slide, spec, index)

    if spec["notes"]:
        slide.notes_slide.notes_text_frame.text = spec["notes"]
    return warnings


def _bullet_body(slide, spec: dict, index: int) -> list[str]:
    bullets = spec["bullets"]
    warnings = []
    if len(bullets) > MAX_BULLETS:
        warnings.append(
            f"slide {index + 1} '{spec['title']}' had {len(bullets)} bullets; "
            f"kept the first {MAX_BULLETS}"
        )
        bullets = bullets[:MAX_BULLETS]

    box = slide.shapes.add_textbox(MARGIN, BODY_TOP, BODY_W, BODY_H)
    frame = box.text_frame
    frame.word_wrap = True
    for i, text in enumerate(bullets):
        paragraph = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        paragraph.text = f"•  {text}"
        paragraph.space_after = Pt(14)
        for run in paragraph.runs:
            run.font.size = Pt(20)
            run.font.color.rgb = INK
    return warnings


def _table_body(slide, spec: dict) -> None:
    header = spec["header"] if isinstance(spec["header"], list) else None
    rows = [r if isinstance(r, list) else [r] for r in spec["rows"]]
    width = max([len(r) for r in rows] + [len(header) if header else 0])
    for row in rows:
        row += [""] * (width - len(row))

    count = len(rows) + (1 if header else 0)
    shape = slide.shapes.add_table(count, width, MARGIN, BODY_TOP, BODY_W, Inches(0.4) * count)
    table = shape.table

    offset = 0
    if header:
        header = [str(h) for h in header] + [""] * (width - len(header))
        for column, text in enumerate(header):
            _cell(table.cell(0, column), text, bold=True)
        offset = 1

    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            _cell(table.cell(r + offset, c), "" if value is None else str(value))


def _cell(cell, text: str, bold: bool = False) -> None:
    cell.text = text
    for paragraph in cell.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(14)
            run.font.bold = bold


def _image_body(slide, spec: dict) -> list[str]:
    path = image_path(
        {"filename": spec["image"], "location": spec["location"], "caption": spec["caption"]}
    )

    picture = slide.shapes.add_picture(str(path), MARGIN, BODY_TOP)
    available_h = BODY_H - (Inches(0.4) if spec["caption"] else 0)

    # Scale to fit the body area, preserving aspect ratio, then centre it.
    scale = min(BODY_W / picture.width, available_h / picture.height, 1.0)
    picture.width, picture.height = Emu(int(picture.width * scale)), Emu(int(picture.height * scale))
    picture.left = Emu(int((SLIDE_W - picture.width) / 2))

    if spec["caption"]:
        box = slide.shapes.add_textbox(MARGIN, BODY_TOP + picture.height + Inches(0.1), BODY_W, Inches(0.4))
        box.text_frame.text = spec["caption"]
        _restyle(box.text_frame, size=12, color=MUTED, italic=True)
    return []


def _restyle(frame, size: int, bold: bool = False, italic: bool = False, color=INK) -> None:
    for paragraph in frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.italic = italic
            run.font.color.rgb = color


# ── read-back ──────────────────────────────────────────────────────────────
def _read_back(path, warnings: list[str]) -> str:
    try:
        deck = Presentation(str(path))
    except Exception as e:
        return f"Saved /sandbox/output/{path.name}, but it could not be reopened to verify it: {e}"

    lines = [
        f"Wrote /sandbox/output/{path.name} ({size_note(path)}).",
        f"Read back from the saved file — {len(deck.slides)} slide(s):",
    ]
    for i, slide in enumerate(deck.slides, 1):
        title = slide.shapes.title.text.strip() if slide.shapes.title else "(no title)"
        parts = []
        bullets = sum(
            len([p for p in shape.text_frame.paragraphs if p.text.strip()])
            for shape in slide.shapes
            if shape.has_text_frame and shape != slide.shapes.title
        )
        if bullets:
            parts.append(f"{bullets} line(s) of text")
        for shape in slide.shapes:
            if shape.has_table:
                parts.append(f"table {len(shape.table.rows)}x{len(shape.table.columns)}")
            if shape.shape_type == 13:  # PICTURE
                parts.append("image")
        lines.append(f"  {i}. {title}" + (f" — {', '.join(parts)}" if parts else ""))

    if warnings:
        lines.append("Trimmed while building:")
        lines += [f"  - {w}" for w in warnings]
    lines.append("Open it with open_on_screen if the user wants to look at it.")
    return "\n".join(lines)
