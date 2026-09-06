# app/tools/authoring/spreadsheet.py
"""`create_spreadsheet` — an .xlsx the operator can sort, filter and hand on.

python_runner can already write a workbook with openpyxl, and for anything with
real computation in it that remains the right tool. This one exists because
"tabulate what I just worked out" is the common case, and going through the
sandbox for it means the model writes a program, the program writes a file, and
nobody checks the file matches the answer given in chat.

Here the rows are the argument. They are typed on the way in — a number stays a
number, so Excel sorts and sums it — and read back off the saved sheet on the
way out, which is the step that catches a summary that drifted from the data.
"""

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from langchain_core.tools import tool

from app.tools.authoring.blocks import SpecError, _decode, output_path, size_note

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

MAX_WIDTH = 55
MIN_WIDTH = 9
PREVIEW_ROWS = 3

SHEET_HELP = (
    'sheets must be a JSON list, each sheet {"name": ..., "header": [...], "rows": [[...]]}:\n'
    '  [{"name": "Thickness", "header": ["Point", "mm", "Status"], '
    '"rows": [["P1", 12.4, "OK"], ["P4", 10.9, "Below limit"]]}]\n'
    "A single sheet may be given as that object on its own."
)


@tool
def create_spreadsheet(filename: str, sheets: str, freeze_header: bool = True) -> str:
    """
    Create an Excel workbook (.xlsx) in /sandbox/output/ from data you already
    have — measurements, a comparison, a status table, a summary per vessel.

    sheets is a JSON list of sheets, each with a name, an optional header row,
    and its rows:
      [{"name": "Thickness",
        "header": ["Point", "Measured mm", "Minimum mm", "Status"],
        "rows": [["P1", 12.4, 11.0, "OK"], ["P4", 10.9, 11.0, "Below limit"]]}]

    Numbers must be sent as numbers, not strings — "10.9" sorts as text and
    will not sum. Headers are styled and frozen, and columns are auto-sized.

    Use this for tabular results the user will sort, filter or paste onward. For
    prose the user will sign or file, use write_document. When the numbers come
    out of real computation — statistics, a parsed dataset, a chart — do that in
    python_runner first, then tabulate the results here. The tool reads the
    saved sheet back and reports the row counts and value ranges it actually
    contains; quote those, not what you intended to write.
    """
    try:
        specs = _sheets(sheets)
        path = output_path(filename, ".xlsx")
    except SpecError as e:
        return f"Error: {e}"

    workbook = Workbook()
    workbook.remove(workbook.active)  # drop the default empty "Sheet"

    for spec in specs:
        _build(workbook, spec, freeze_header)

    try:
        workbook.save(str(path))
    except PermissionError:
        return (
            f"Error: {path.name} is open in Excel and could not be replaced. "
            "Ask the user to close it."
        )

    return _read_back(path)


# ── input ──────────────────────────────────────────────────────────────────
def _sheets(sheets) -> list[dict]:
    data = _decode(sheets)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise SpecError(f"no sheets given. {SHEET_HELP}")

    out, used = [], set()
    for i, sheet in enumerate(data, 1):
        if not isinstance(sheet, dict):
            raise SpecError(f"sheet {i} is a {type(sheet).__name__}, not an object. {SHEET_HELP}")

        rows = sheet.get("rows", sheet.get("data"))
        if not isinstance(rows, list) or not rows:
            raise SpecError(f'sheet {i} has no "rows". {SHEET_HELP}')

        header = sheet.get("header", sheet.get("headers"))
        if header is not None and not isinstance(header, list):
            raise SpecError(f"sheet {i} has a 'header' that is not a list.")

        normalized = []
        for j, row in enumerate(rows, 1):
            if not isinstance(row, list):
                raise SpecError(f"sheet {i}, row {j} is a {type(row).__name__}, not a list of cells.")
            normalized.append([_value(c) for c in row])

        out.append(
            {
                "name": _name(sheet.get("name") or sheet.get("title") or f"Sheet{i}", used),
                "header": [_value(h) for h in header] if header else None,
                "rows": normalized,
            }
        )
    return out


def _value(cell):
    """Keep numbers numeric so Excel treats them as numbers.

    A model that sends "10.9" almost always means the measurement, not the
    text — and left as text it neither sorts nor sums, which is the whole
    reason for putting it in a spreadsheet.
    """
    if cell is None or isinstance(cell, (int, float, bool)):
        return cell
    text = str(cell).strip()
    if not text:
        return None
    try:
        return int(text) if text.lstrip("+-").isdigit() else float(text)
    except ValueError:
        return text


def _name(raw, used: set) -> str:
    """Excel refuses these characters and anything over 31 chars, silently
    corrupting the file rather than complaining, so trim before writing."""
    name = "".join(c for c in str(raw) if c not in "[]:*?/\\").strip()[:31] or "Sheet"
    candidate, n = name, 2
    while candidate.lower() in used:
        candidate = f"{name[:28]}_{n}"
        n += 1
    used.add(candidate.lower())
    return candidate


# ── output ─────────────────────────────────────────────────────────────────
def _build(workbook: Workbook, spec: dict, freeze_header: bool) -> None:
    sheet = workbook.create_sheet(spec["name"])
    header, rows = spec["header"], spec["rows"]

    if header:
        sheet.append(header)
        for cell in sheet[1]:
            cell.fill, cell.font = HEADER_FILL, HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if freeze_header:
            sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(max(len(header), 1))}{len(rows) + 1}"

    for row in rows:
        sheet.append(row)

    for cell_row in sheet.iter_rows():
        for cell in cell_row:
            cell.border = BORDER
            if isinstance(cell.value, float):
                cell.number_format = "0.00"

    _autosize(sheet)


def _autosize(sheet) -> None:
    for column in sheet.columns:
        longest = max((len(str(c.value)) for c in column if c.value is not None), default=0)
        letter = get_column_letter(column[0].column)
        sheet.column_dimensions[letter].width = min(max(longest + 3, MIN_WIDTH), MAX_WIDTH)


# ── read-back ──────────────────────────────────────────────────────────────
def _read_back(path) -> str:
    """Reopen the workbook and describe the data that is actually in it.

    Numeric columns get their range reported: if the agent said "minimum 11.0"
    and the sheet holds 10.9, that contradiction is visible right here, in the
    tool result, before the agent writes its summary.
    """
    try:
        workbook = load_workbook(str(path), data_only=True)
    except Exception as e:
        return f"Saved /sandbox/output/{path.name}, but it could not be reopened to verify it: {e}"

    lines = [f"Wrote /sandbox/output/{path.name} ({size_note(path)}).", "Read back from the saved file:"]
    for name in workbook.sheetnames:
        sheet = workbook[name]
        lines.append(f"  '{name}': {sheet.max_row} row(s) x {sheet.max_column} column(s)")

        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        header = rows[0]
        body = rows[1:]
        if header and all(isinstance(v, str) or v is None for v in header):
            lines.append("    header: " + " | ".join("" if v is None else str(v) for v in header))
        else:
            body = rows

        for index, label in enumerate(header or []):
            values = [r[index] for r in body if isinstance(r[index], (int, float)) and not isinstance(r[index], bool)]
            if len(values) > 1:
                lines.append(
                    f"    {label or get_column_letter(index + 1)}: "
                    f"min {min(values):g}, max {max(values):g}, sum {sum(values):g} over {len(values)} value(s)"
                )

        for row in body[:PREVIEW_ROWS]:
            lines.append("    " + " | ".join("" if v is None else str(v) for v in row))
        if len(body) > PREVIEW_ROWS:
            lines.append(f"    … {len(body) - PREVIEW_ROWS} more data row(s)")

    workbook.close()
    lines.append("Quote these figures in your reply. Open it with open_on_screen if the user wants to see it.")
    return "\n".join(lines)
