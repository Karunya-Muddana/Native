# app/tools/authoring/presentation.py
"""`create_presentation` — a .pptx deck for management, from work already done.

The last step of an investigation is usually explaining it to someone who will
not read the evidence. This turns the conclusions into slides: a title slide,
then one slide per point, each carrying bullets, a table, an image, a headline
number or a pull quote.

Layout is deliberately not negotiable. The model supplies content and picks a
slide kind; where things sit on the slide is decided here, because a model asked
to place boxes produces overlapping text, and a deck with overlapping text is
worse than no deck.

What *is* decided here is also the reason a deck looks designed rather than
typed. python-pptx's stock template is a white page with a Calibri heading, and
a deck built straight onto it reads as the bare minimum no matter how good the
content is. So this file carries a small design system — a painted palette, a
type scale, a set of composed layouts, full-bleed imagery — and every slide is
built out of it rather than dropped onto the default master.
"""

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt
from langchain_core.tools import tool

from app.tools.authoring.blocks import SpecError, _decode, image_path, output_path, size_note

BLANK = 6

SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)  # 16:9
MARGIN = Inches(0.85)
BODY_W = SLIDE_W - (2 * MARGIN)
TITLE_TOP = Inches(0.72)
BODY_TOP = Inches(2.05)
BODY_H = SLIDE_H - BODY_TOP - Inches(0.85)

# One family, used everywhere. A deck that mixes fonts looks accidental, and
# these two ship with every PowerPoint install so nothing substitutes at open.
FONT = "Segoe UI"
FONT_LIGHT = "Segoe UI Light"

MAX_BULLETS = 6  # past this a slide is a document with a title on it


# ── palette ────────────────────────────────────────────────────────────────
def _rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value)


# Each theme is the same six roles, so every layout below can be written once
# against the roles and re-skinned by swapping the theme. `deep` carries the
# title and section slides; `paper` carries the content slides; `accent` is the
# one saturated colour and is spent sparingly, which is what keeps it reading as
# a deliberate choice rather than decoration.
THEMES = {
    "midnight": {
        "deep": "0E1B2E", "deep_soft": "1B2C46", "paper": "FFFFFF",
        "surface": "F2F5F9", "ink": "13203A", "muted": "6A7789", "accent": "3B7DF5",
    },
    "slate": {
        "deep": "1D2529", "deep_soft": "2C383E", "paper": "FFFFFF",
        "surface": "F1F4F4", "ink": "1D2529", "muted": "6E7B82", "accent": "0E9C8B",
    },
    "ember": {
        "deep": "231A16", "deep_soft": "3A2B24", "paper": "FFFFFF",
        "surface": "F7F3F0", "ink": "2A1E19", "muted": "7C6D64", "accent": "D2622A",
    },
    "forest": {
        "deep": "132218", "deep_soft": "22392A", "paper": "FFFFFF",
        "surface": "F1F5F1", "ink": "16261B", "muted": "6B7A6E", "accent": "2F8F4E",
    },
    "plum": {
        "deep": "1F1630", "deep_soft": "332448", "paper": "FFFFFF",
        "surface": "F5F2F9", "ink": "241A36", "muted": "756A85", "accent": "7C4DDB",
    },
}

DEFAULT_THEME = "midnight"

LAYOUTS = ("bullets", "split", "image", "table", "quote", "stats", "section")

SLIDE_HELP = (
    'slides must be a JSON list. Each slide has a "title" and one kind of body:\n'
    '  {"title": "Findings", "bullets": ["P4 at 10.9 mm", "Below the 11.0 mm limit"]}\n'
    '  {"title": "Measurements", "header": ["Point", "mm"], "rows": [["P1", 12.4]]}\n'
    '  {"title": "Wall loss", "image": "thickness_chart.png", "caption": "..."}\n'
    '  {"title": "Root cause", "image": "pipe.png", "bullets": ["...", "..."]}\n'
    '  {"title": "Scale", "stats": [{"value": "27%", "label": "thinner than spec"}]}\n'
    '  {"title": "", "quote": "The limit was passed in March.", "attribution": "Log 4.2"}\n'
    '  {"title": "Part Two: Controls", "layout": "section"}\n'
    'Any slide may also carry "notes" for the speaker.'
)


# ── tool ───────────────────────────────────────────────────────────────────
@tool
def create_presentation(
    filename: str,
    title: str,
    slides: str,
    subtitle: str = "",
    theme: str = DEFAULT_THEME,
) -> str:
    """
    Create a designed PowerPoint deck (.pptx) in /sandbox/output/ — a management
    summary, an investigation walkthrough, a findings briefing.

    title and subtitle become the opening slide. slides is a JSON list, one
    object per slide after it. Each slide has a title and one kind of body, and
    the body decides how the slide is composed:
      {"title": "Findings", "bullets": ["P4 measured 10.9 mm", "Limit is 11.0 mm"]}
      {"title": "Measurements", "header": ["Point", "mm"], "rows": [["P1", 12.4]]}
      {"title": "Wall loss", "image": "chart.png", "caption": "..."}      full bleed
      {"title": "Cause", "image": "pipe.png", "bullets": [...]}           image + text
      {"title": "Scale", "stats": [{"value": "27%", "label": "below spec"}]}
      {"quote": "The limit was passed in March.", "attribution": "Log 4.2"}
      {"title": "Part Two: Controls", "layout": "section"}                divider

    Compose the deck, do not just list points. A good deck alternates: open with
    a section divider, put the number that matters on a stats slide, give a
    strong image a full slide of its own, and keep bullets to four or five short
    lines — six is the maximum and the tool trims past it. Bullets should be
    fragments, not sentences.

    An image slide embeds a file already in /sandbox/output/ or /sandbox/input/:
    render charts with python_runner or draw pictures with generate_image first,
    then name them here. Images are cropped to fill their frame, so any aspect
    ratio works.

    theme sets the palette: 'midnight' (blue, default), 'slate' (teal), 'ember'
    (warm orange), 'forest' (green) or 'plum' (violet).

    Use this when the user asks to present, brief, or summarize for management.
    For something to be signed or filed, use write_document. The tool reads the
    saved deck back and reports each slide as it actually exists.
    """
    try:
        specs = _slides(slides)
        path = output_path(filename, ".pptx")
    except SpecError as e:
        return f"Error: {e}"

    palette = THEMES.get(str(theme or "").strip().lower(), THEMES[DEFAULT_THEME])

    deck = Presentation()
    deck.slide_width, deck.slide_height = SLIDE_W, SLIDE_H

    _title_slide(deck, palette, (title or "").strip(), (subtitle or "").strip())

    warnings = []
    try:
        for index, spec in enumerate(specs, 1):
            warnings += _slide(deck, palette, spec, index)
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
            out.append(_spec({"title": slide, "layout": "section"}, i))
            continue
        if not isinstance(slide, dict):
            raise SpecError(f"slide {i} is a {type(slide).__name__}, not an object. {SLIDE_HELP}")
        out.append(_spec(slide, i))
    return out


def _spec(slide: dict, i: int) -> dict:
    bullets = slide.get("bullets", slide.get("points", []))
    if isinstance(bullets, str):
        bullets = [line for line in bullets.splitlines() if line.strip()]
    if not isinstance(bullets, list):
        raise SpecError(f"slide {i} has 'bullets' that is not a list.")

    rows = slide.get("rows", slide.get("data"))
    if rows is not None and not isinstance(rows, list):
        raise SpecError(f"slide {i} has 'rows' that is not a list.")

    stats = slide.get("stats", slide.get("figures"))
    if stats is not None and not isinstance(stats, list):
        raise SpecError(f"slide {i} has 'stats' that is not a list.")

    spec = {
        "title": str(slide.get("title", slide.get("heading", ""))).strip(),
        "kicker": str(slide.get("kicker", slide.get("eyebrow", ""))).strip(),
        "bullets": [str(b) for b in bullets if str(b).strip()],
        "header": slide.get("header", slide.get("headers")),
        "rows": rows,
        "stats": _stats(stats, i),
        "quote": str(slide.get("quote", "")).strip(),
        "attribution": str(slide.get("attribution", slide.get("source", ""))).strip(),
        "image": str(slide.get("image", slide.get("filename", ""))).strip(),
        "location": str(slide.get("location", "output")).strip().lower(),
        "caption": str(slide.get("caption", "")).strip(),
        "notes": str(slide.get("notes", "")).strip(),
    }

    asked = str(slide.get("layout", slide.get("type", ""))).strip().lower()
    spec["layout"] = asked if asked in LAYOUTS else _infer(spec)
    return spec


def _stats(stats, i: int) -> list[dict]:
    """Accept the shape a model reaches for first, and the tidy one."""
    if not stats:
        return []
    out = []
    for entry in stats[:4]:  # four across is the most that stays readable
        if isinstance(entry, dict):
            value = entry.get("value", entry.get("number", entry.get("figure", "")))
            label = entry.get("label", entry.get("text", entry.get("caption", "")))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            value, label = entry[0], entry[1]
        else:
            value, label = entry, ""
        if str(value).strip():
            out.append({"value": str(value).strip(), "label": str(label).strip()})
    if not out:
        raise SpecError(f"slide {i} has 'stats' with no usable value/label pairs.")
    return out


def _infer(spec: dict) -> str:
    """Pick the composition the content is actually asking for.

    An image with bullets beside it is a different slide from an image on its
    own, and a model that only ever fills in `bullets` should still get the
    better of the two when it happens to attach a picture.
    """
    if spec["quote"]:
        return "quote"
    if spec["stats"]:
        return "stats"
    if spec["image"] and spec["bullets"]:
        return "split"
    if spec["image"]:
        return "image"
    if spec["rows"]:
        return "table"
    if spec["bullets"]:
        return "bullets"
    return "section"


# ── painting primitives ────────────────────────────────────────────────────
def _blank(deck: Presentation):
    return deck.slides.add_slide(deck.slide_layouts[BLANK])


def _background(slide, colour: str) -> None:
    """Paint the whole slide. Without this every slide is stock white."""
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(colour)


def _rect(slide, left, top, width, height, colour: str, alpha: int | None = None):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(colour)
    shape.line.fill.background()
    _no_shadow(shape)
    if alpha is not None:
        _alpha(shape, alpha)
    return shape


def _no_shadow(shape) -> None:
    """Autoshapes inherit the theme's drop shadow, which reads as clip art.

    `shape.shadow.inherit = False` only stops the inheritance; the renderer then
    falls back to its own default. An explicit empty effect list is what
    actually means "no effects".
    """
    shape.shadow.inherit = False
    properties = shape._element.spPr
    for existing in properties.findall(qn("a:effectLst")):
        properties.remove(existing)
    properties.append(properties.makeelement(qn("a:effectLst"), {}))

    # An empty effect list is not enough on its own: a new autoshape also
    # carries a <p:style> block pointing at the theme's effect and line
    # references, and renderers honour that. Dropping it leaves the shape with
    # only the fill set above, which is all these blocks are meant to be.
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


def _alpha(shape, percent: int) -> None:
    """Set fill transparency.

    python-pptx has no API for it, so the alpha element is written onto the
    colour directly. Used for the scrim that keeps white text legible over a
    photograph whose brightness we cannot know in advance.
    """
    srgb = shape.fill.fore_color._xFill.find(qn("a:srgbClr"))
    if srgb is None:
        return
    element = srgb.makeelement(qn("a:alpha"), {"val": str(int(percent * 1000))})
    srgb.append(element)


TITLE_TAG = "deck-title"  # so the read-back can find the real title again


def _text(slide, left, top, width, height, anchor=MSO_ANCHOR.TOP, name: str = ""):
    box = slide.shapes.add_textbox(left, top, width, height)
    if name:
        box.name = name
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    return frame


def _write(
    frame,
    text: str,
    size: int,
    colour: str,
    bold: bool = False,
    font: str = FONT,
    align=PP_ALIGN.LEFT,
    spacing: int = 0,
    first: bool = False,
    line: float | None = None,
):
    paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
    paragraph.alignment = align
    paragraph.space_after = Pt(spacing)
    if line:
        paragraph.line_spacing = line
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = font
    run.font.color.rgb = _rgb(colour)
    return paragraph


def _picture(slide, path, left, top, width, height):
    """Place an image cropped to fill the frame, never squashed to fit.

    Letterboxing a photo into a fixed box is what makes a deck look automated —
    the bands of empty white on either side read as a missing decision. Cropping
    to fill means any aspect ratio the generator returns lands as a deliberate
    composition, and the centre of the picture is what survives the crop.
    """
    picture = slide.shapes.add_picture(str(path), left, top)
    native = picture.width / picture.height
    target = width / height
    if native > target:
        keep = target / native
        picture.crop_left = picture.crop_right = (1 - keep) / 2
    else:
        keep = native / target
        picture.crop_top = picture.crop_bottom = (1 - keep) / 2
    picture.left, picture.top = left, top
    picture.width, picture.height = width, height
    return picture


def _chrome(slide, palette: dict, index: int, title: str, kicker: str = "") -> None:
    """The furniture every content slide shares: kicker, title, rule, number."""
    top = TITLE_TOP
    if kicker:
        frame = _text(slide, MARGIN, Inches(0.5), BODY_W, Inches(0.3))
        _write(frame, kicker.upper(), 11, palette["accent"], bold=True, first=True)
        top = Inches(0.92)

    frame = _text(slide, MARGIN, top, BODY_W, Inches(0.85), name=TITLE_TAG)
    _write(frame, title, 30, palette["ink"], bold=True, first=True, line=1.05)

    # A short accent rule under the title, not a full-width line — it marks the
    # start of the content without drawing a box around it.
    _rect(slide, MARGIN, Inches(1.78), Inches(0.62), Pt(4), palette["accent"])
    _number(slide, palette, index)


def _number(slide, palette: dict, index: int) -> None:
    frame = _text(slide, SLIDE_W - MARGIN - Inches(1.0), SLIDE_H - Inches(0.62),
                  Inches(1.0), Inches(0.3))
    _write(frame, str(index + 1), 11, palette["muted"], align=PP_ALIGN.RIGHT, first=True)


# ── slides ─────────────────────────────────────────────────────────────────
def _title_slide(deck: Presentation, palette: dict, title: str, subtitle: str) -> None:
    """Full-bleed dark, with the accent as a wedge rather than a line.

    The opening slide is the one people photograph, so it gets the strongest
    composition in the deck and no bullet points at all.
    """
    slide = _blank(deck)
    _background(slide, palette["deep"])

    # An off-centre block of the softer deep tone: enough structure that the
    # slide is not a flat rectangle, quiet enough not to compete with the words.
    _rect(slide, SLIDE_W - Inches(4.4), 0, Inches(4.4), SLIDE_H, palette["deep_soft"])
    _rect(slide, SLIDE_W - Inches(4.4), 0, Pt(5), SLIDE_H, palette["accent"])

    _rect(slide, MARGIN, Inches(2.15), Inches(1.1), Pt(5), palette["accent"])

    frame = _text(slide, MARGIN, Inches(2.6), Inches(7.6), Inches(2.6), name=TITLE_TAG)
    _write(frame, title or "Untitled", 46, palette["paper"], bold=True, first=True, line=1.06)
    if subtitle:
        _write(frame, subtitle, 18, palette["muted"], font=FONT_LIGHT, spacing=0, line=1.25)


def _slide(deck: Presentation, palette: dict, spec: dict, index: int) -> list[str]:
    builder = {
        "section": _section_slide,
        "bullets": _bullets_slide,
        "split": _split_slide,
        "image": _image_slide,
        "table": _table_slide,
        "quote": _quote_slide,
        "stats": _stats_slide,
    }[spec["layout"]]

    slide = _blank(deck)
    warnings = builder(slide, palette, spec, index)

    if spec["notes"]:
        slide.notes_slide.notes_text_frame.text = spec["notes"]
    return warnings


def _section_slide(slide, palette: dict, spec: dict, index: int) -> list[str]:
    """A divider. Its whole job is to give the audience a beat between parts."""
    _background(slide, palette["deep"])
    _rect(slide, 0, 0, Pt(7), SLIDE_H, palette["accent"])

    frame = _text(slide, MARGIN, Inches(2.9), Inches(10.0), Inches(2.0), MSO_ANCHOR.MIDDLE,
                  name=TITLE_TAG)
    if spec["kicker"]:
        _write(frame, spec["kicker"].upper(), 12, palette["accent"], bold=True,
               spacing=10, first=True)
        _write(frame, spec["title"], 38, palette["paper"], bold=True, line=1.08)
    else:
        _write(frame, spec["title"], 38, palette["paper"], bold=True, first=True, line=1.08)
    if spec["bullets"]:
        _write(frame, spec["bullets"][0], 16, palette["muted"], font=FONT_LIGHT, line=1.3)
    return []


def _bullets_slide(slide, palette: dict, spec: dict, index: int) -> list[str]:
    _background(slide, palette["paper"])
    _chrome(slide, palette, index, spec["title"], spec["kicker"])

    bullets, warnings = _trim(spec, index)
    # Three bullets pinned to the top of a 16:9 slide leaves a third of the page
    # visibly unused. Sitting them in the middle of the body area reads as a
    # composition instead of a list that ran out.
    anchor = MSO_ANCHOR.MIDDLE if len(bullets) <= 4 else MSO_ANCHOR.TOP
    frame = _text(slide, MARGIN, BODY_TOP, BODY_W - Inches(1.2), BODY_H, anchor)
    _bullets(frame, palette, bullets, size=_bullet_size(bullets))
    return warnings


def _split_slide(slide, palette: dict, spec: dict, index: int) -> list[str]:
    """Text left, picture bleeding off the right edge.

    Running the image to the slide edge rather than insetting it inside a margin
    is most of the difference between this and a stock content slide.
    """
    _background(slide, palette["paper"])

    image_w = Inches(5.6)
    image_left = SLIDE_W - image_w
    _picture(slide, image_path(_image_spec(spec)), image_left, 0, image_w, SLIDE_H)
    _rect(slide, image_left, 0, Pt(4), SLIDE_H, palette["accent"])

    column = image_left - MARGIN - Inches(0.7)
    top = TITLE_TOP
    if spec["kicker"]:
        frame = _text(slide, MARGIN, Inches(0.5), column, Inches(0.3))
        _write(frame, spec["kicker"].upper(), 11, palette["accent"], bold=True, first=True)
        top = Inches(0.92)

    frame = _text(slide, MARGIN, top, column, Inches(1.5), name=TITLE_TAG)
    _write(frame, spec["title"], 28, palette["ink"], bold=True, first=True, line=1.06)
    _rect(slide, MARGIN, Inches(2.05), Inches(0.62), Pt(4), palette["accent"])

    bullets, warnings = _trim(spec, index, limit=5)
    frame = _text(slide, MARGIN, Inches(2.45), column, SLIDE_H - Inches(3.1),
                  MSO_ANCHOR.MIDDLE if len(bullets) <= 3 else MSO_ANCHOR.TOP)
    _bullets(frame, palette, bullets, size=17)

    if spec["caption"]:
        frame = _text(slide, image_left + Inches(0.3), SLIDE_H - Inches(0.75),
                      image_w - Inches(0.6), Inches(0.4))
        _write(frame, spec["caption"], 11, palette["paper"], first=True)
    return warnings


def _image_slide(slide, palette: dict, spec: dict, index: int) -> list[str]:
    """The picture is the slide: full bleed, title reversed out over a scrim.

    A generated illustration loses most of its effect shrunk into the middle of
    a white page. Given the whole frame it carries the point on its own, and the
    scrim is what lets the title sit on top of it without knowing whether the
    image underneath came back light or dark.
    """
    _background(slide, palette["deep"])
    _picture(slide, image_path(_image_spec(spec)), 0, 0, SLIDE_W, SLIDE_H)

    if not spec["title"] and not spec["caption"]:
        _number(slide, palette, index)
        return []

    # A band across the lower third rather than a wash over everything, so the
    # image is still an image and the text still has a solid ground.
    band_h = Inches(2.5)
    _rect(slide, 0, SLIDE_H - band_h, SLIDE_W, band_h, palette["deep"], alpha=78)
    _rect(slide, 0, SLIDE_H - band_h, SLIDE_W, Pt(4), palette["accent"])

    frame = _text(slide, MARGIN, SLIDE_H - Inches(1.95), Inches(10.2), Inches(1.4),
                  name=TITLE_TAG)
    if spec["kicker"]:
        _write(frame, spec["kicker"].upper(), 11, palette["accent"], bold=True,
               spacing=6, first=True)
        _write(frame, spec["title"], 30, palette["paper"], bold=True, line=1.06)
    else:
        _write(frame, spec["title"], 30, palette["paper"], bold=True, first=True, line=1.06)
    if spec["caption"]:
        _write(frame, spec["caption"], 14, "D8DEE8", font=FONT_LIGHT, line=1.25)
    return []


def _table_slide(slide, palette: dict, spec: dict, index: int) -> list[str]:
    _background(slide, palette["paper"])
    _chrome(slide, palette, index, spec["title"], spec["kicker"])

    header = spec["header"] if isinstance(spec["header"], list) else None
    rows = [r if isinstance(r, list) else [r] for r in spec["rows"]]
    width = max([len(r) for r in rows] + [len(header) if header else 0])
    for row in rows:
        row += [""] * (width - len(row))

    count = len(rows) + (1 if header else 0)
    row_h = Inches(0.46)
    shape = slide.shapes.add_table(count, width, MARGIN, BODY_TOP, BODY_W, row_h * count)
    table = shape.table
    # The built-in banded style is the loudest thing python-pptx will hand you.
    # Turning it off and banding by hand keeps the table in the deck's palette.
    _plain_table(table)

    offset = 0
    if header:
        header = [str(h) for h in header] + [""] * (width - len(header))
        for column, text in enumerate(header):
            _cell(table.cell(0, column), text, palette,
                  colour=palette["paper"], fill=palette["deep"], bold=True)
        offset = 1

    for r, row in enumerate(rows):
        shade = palette["surface"] if r % 2 else palette["paper"]
        for c, value in enumerate(row):
            _cell(table.cell(r + offset, c), "" if value is None else str(value),
                  palette, colour=palette["ink"], fill=shade)

    if spec["caption"]:
        frame = _text(slide, MARGIN, BODY_TOP + row_h * count + Inches(0.2),
                      BODY_W, Inches(0.4))
        _write(frame, spec["caption"], 12, palette["muted"], font=FONT_LIGHT, first=True)
    return []


def _quote_slide(slide, palette: dict, spec: dict, index: int) -> list[str]:
    """One sentence, large, on the dark ground — a deliberate pause in the deck."""
    _background(slide, palette["deep"])
    _rect(slide, MARGIN, Inches(1.9), Pt(5), Inches(3.4), palette["accent"])

    frame = _text(slide, MARGIN + Inches(0.5), Inches(1.9), Inches(10.4), Inches(3.4),
                  MSO_ANCHOR.MIDDLE, name=TITLE_TAG)
    size = 32 if len(spec["quote"]) < 150 else 24
    _write(frame, spec["quote"], size, palette["paper"], font=FONT_LIGHT,
           first=True, line=1.3, spacing=18)
    if spec["attribution"]:
        _write(frame, f"— {spec['attribution']}", 15, palette["accent"], bold=True)
    _number(slide, palette, index)
    return []


def _stats_slide(slide, palette: dict, spec: dict, index: int) -> list[str]:
    """Headline numbers as cards. The figure is the message; the label explains it."""
    _background(slide, palette["paper"])
    _chrome(slide, palette, index, spec["title"], spec["kicker"])

    stats = spec["stats"]
    gap = Inches(0.35)
    card_w = Emu(int((BODY_W - gap * (len(stats) - 1)) / len(stats)))
    card_h = Inches(2.15)
    top = BODY_TOP + Inches(0.25)

    # One size for the whole row, set by the longest figure. Sizing each card
    # independently makes a row of numbers look mismatched, which undoes the
    # comparison the slide exists to make.
    longest = max(len(s["value"]) for s in stats)
    size = 54 if longest <= 4 else (42 if longest <= 7 else 32)

    for i, stat in enumerate(stats):
        left = Emu(int(MARGIN + i * (card_w + gap)))
        _rect(slide, left, top, card_w, card_h, palette["surface"])
        _rect(slide, left, top, card_w, Pt(5), palette["accent"])

        frame = _text(slide, left + Inches(0.3), top + Inches(0.45),
                      card_w - Inches(0.6), card_h - Inches(0.7))
        _write(frame, stat["value"], size, palette["accent"], bold=True,
               first=True, spacing=10, line=1.0)
        if stat["label"]:
            _write(frame, stat["label"], 14, palette["muted"], line=1.25)

    if spec["bullets"]:
        frame = _text(slide, MARGIN, top + card_h + Inches(0.4), BODY_W, Inches(0.9))
        _write(frame, spec["bullets"][0], 15, palette["ink"], font=FONT_LIGHT,
               first=True, line=1.3)
    return []


# ── body helpers ───────────────────────────────────────────────────────────
def _image_spec(spec: dict) -> dict:
    return {"filename": spec["image"], "location": spec["location"], "caption": spec["caption"]}


def _trim(spec: dict, index: int, limit: int = MAX_BULLETS) -> tuple[list[str], list[str]]:
    bullets = spec["bullets"]
    if len(bullets) <= limit:
        return bullets, []
    return bullets[:limit], [
        f"slide {index + 1} '{spec['title']}' had {len(bullets)} bullets; kept the first {limit}"
    ]


def _bullet_size(bullets: list[str]) -> int:
    """Fewer, shorter bullets earn more room. Keeps a sparse slide from looking
    empty and a full one from running off the bottom."""
    longest = max((len(b) for b in bullets), default=0)
    if len(bullets) <= 3 and longest < 70:
        return 22
    if len(bullets) <= 5:
        return 19
    return 17


def _bullets(frame, palette: dict, bullets: list[str], size: int) -> None:
    """Accent marker, then the text — two runs so the marker can carry colour.

    PowerPoint's own bullet glyph inherits from the master and cannot be tinted
    per-paragraph without more XML than it is worth; a leading run does the same
    job and stays in the palette.
    """
    for i, text in enumerate(bullets):
        paragraph = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        paragraph.space_after = Pt(int(size * 0.85))
        paragraph.line_spacing = 1.25

        marker = paragraph.add_run()
        marker.text = "■  "
        marker.font.size = Pt(int(size * 0.55))
        marker.font.name = FONT
        marker.font.color.rgb = _rgb(palette["accent"])

        run = paragraph.add_run()
        run.text = str(text)
        run.font.size = Pt(size)
        run.font.name = FONT
        run.font.color.rgb = _rgb(palette["ink"])


def _plain_table(table) -> None:
    """Drop the built-in table style so the hand-set fills are what shows."""
    properties = table._tbl.find(qn("a:tblPr"))
    if properties is None:
        return
    properties.set("firstRow", "0")
    properties.set("bandRow", "0")
    style = properties.find(qn("a:tableStyleId"))
    if style is not None:
        properties.remove(style)


def _cell(cell, text: str, palette: dict, colour: str, fill: str, bold: bool = False) -> None:
    cell.text = text
    cell.fill.solid()
    cell.fill.fore_color.rgb = _rgb(fill)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.margin_left = cell.margin_right = Inches(0.16)
    for paragraph in cell.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(13)
            run.font.bold = bold
            run.font.name = FONT
            run.font.color.rgb = _rgb(colour)


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
        texts = [
            shape.text_frame.text.strip()
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text.strip()
        ]
        # The title box is tagged when it is built, because on these layouts it
        # is not the first shape on the slide and often not the first text
        # either — a kicker sits above it.
        titled = [
            shape.text_frame.text.strip()
            for shape in slide.shapes
            if shape.name == TITLE_TAG and shape.has_text_frame
        ]
        source = titled or texts
        # A tagged frame can hold kicker/title/subtitle; the title is the line
        # that is not the all-caps kicker.
        written_lines = [ln for ln in (source[0].splitlines() if source else []) if ln.strip()]
        named = [ln for ln in written_lines
                 if not (ln.isupper() and len(written_lines) > 1)] or written_lines
        title = named[0] if named else "(no title)"
        parts = []
        written = sum(len([p for p in t.splitlines() if p.strip()]) for t in texts)
        if written:
            parts.append(f"{written} line(s) of text")
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
