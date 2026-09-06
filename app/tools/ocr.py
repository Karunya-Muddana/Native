import docker
from pathlib import Path
from langchain_core.tools import tool

client = None


def _get_client():
    global client
    if client is None:
        client = docker.from_env()
    return client


from app.tools.paths import BASE_DIR, INPUT_DIR, OUTPUT_DIR, resolve_file

OUTPUT_DIR.mkdir(exist_ok=True)
INPUT_DIR.mkdir(exist_ok=True)


def _run_in_sandbox(code: str) -> str:
    try:
        output = _get_client().containers.run(
            image="mrpl-sandbox-python",
            command=["python3", "-c", code],
            network_disabled=True,
            # Roomier than python_runner's cap: this runs our own OCR code, not
            # model-authored code, and a 300 dpi page plus the ONNX session does
            # not fit comfortably in 512m.
            mem_limit="2g",
            volumes={
                str(INPUT_DIR): {"bind": "/sandbox/input", "mode": "ro"},
                str(OUTPUT_DIR): {"bind": "/sandbox/output", "mode": "rw"},
            },
            remove=True,
        )
        return output.decode()
    except Exception as e:
        return f"Error: {str(e)}"


OCR_CODE = r"""
import io
import os

import fitz  # PyMuPDF

DPI = 300
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp")
EN_REC = "/models/en_PP-OCRv3_rec_infer.onnx"
LOW_CONF = 0.60

_engine = None


def engine():
    '''PP-OCRv4 detection + English recognition, on ONNX Runtime.

    The English recogniser matters: the model bundled with the wheel is trained
    on Chinese, which does not use spaces, and it runs Latin words together
    ("INSPECTIONREPORT").
    '''
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        kwargs = {"rec_model_path": EN_REC} if os.path.exists(EN_REC) else {}
        _engine = RapidOCR(**kwargs)
    return _engine


def to_lines(result):
    '''Rebuild reading order from detection boxes.

    Detection returns boxes in no particular order, so text from a drawing or a
    multi-column page arrives scrambled unless it is re-sorted. Boxes are
    grouped into rows by vertical overlap, then read left to right.
    '''
    items = []
    for box, text, conf in result:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append({
            "text": text,
            "conf": float(conf),
            "cy": sum(ys) / len(ys),
            "x": min(xs),
            "h": max(ys) - min(ys),
        })
    if not items:
        return [], []

    heights = sorted(i["h"] for i in items)
    tol = max(heights[len(heights) // 2] * 0.6, 6)

    items.sort(key=lambda i: i["cy"])
    rows, row = [], [items[0]]
    for it in items[1:]:
        if abs(it["cy"] - row[-1]["cy"]) <= tol:
            row.append(it)
        else:
            rows.append(row)
            row = [it]
    rows.append(row)

    lines, confs = [], []
    for r in rows:
        r.sort(key=lambda i: i["x"])
        lines.append(" ".join(i["text"] for i in r))
        confs.extend(i["conf"] for i in r)
    return lines, confs


def ocr_image_bytes(png):
    result, _ = engine()(png)
    if not result:
        return "", []
    return to_lines(result)


def tesseract_fallback(png):
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(Image.open(io.BytesIO(png))), []


def render_pages(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        with open(path, "rb") as f:
            yield 1, 1, f.read()
        return
    if ext != ".pdf":
        raise ValueError(f"Unsupported file type: {ext}")
    doc = fitz.open(path)
    total = len(doc)
    for n, page in enumerate(doc):
        yield n + 1, total, page.get_pixmap(dpi=DPI).tobytes("png")
    doc.close()


def main(path):
    if not os.path.exists(path):
        return f"Error: file not found at {path}"

    parts, all_confs, engine_name = [], [], "PP-OCRv4 (ONNX)"
    try:
        engine()
    except Exception as e:
        engine_name = f"Tesseract (fallback: OCR engine unavailable - {e})"

    for num, total, png in render_pages(path):
        try:
            if engine_name.startswith("PP-OCRv4"):
                lines, confs = ocr_image_bytes(png)
                text = "\n".join(lines)
            else:
                text, confs = tesseract_fallback(png)
        except Exception as e:
            text, confs = tesseract_fallback(png)
            engine_name = f"Tesseract (fallback: {e})"

        all_confs.extend(confs)
        header = f"--- Page {num} of {total} ---" if total > 1 else ""
        parts.append(f"{header}\n{text}".strip())

    body = "\n\n".join(parts)

    # An inspection reading that was recognised badly is worse than one that was
    # not read at all, so say plainly how confident the engine was.
    note = f"[OCR engine: {engine_name}]"
    if all_confs:
        mean = sum(all_confs) / len(all_confs)
        weak = sum(1 for c in all_confs if c < LOW_CONF)
        note += f" [mean confidence {mean:.2f} over {len(all_confs)} text regions]"
        if weak:
            note += (
                f" [WARNING: {weak} region(s) below {LOW_CONF:.2f} - treat any "
                f"figure taken from this text as unverified and confirm it "
                f"against another source before relying on it]"
            )
    return f"{note}\n\n{body}"


print(main(path))
"""


MAX_INLINE_CHARS = 4000  # rough proxy for ~1000 tokens; tune once you see real outputs


@tool
def run_ocr(filename: str, location: str = "input") -> str:
    """
    Run OCR on a file already present in the sandbox. Works on images
    (png/jpg/jpeg/tiff/bmp) and PDFs (page by page, for scanned/image-based
    documents specifically — use python_runner with pypdf/pdfplumber instead
    for text-based PDFs, which will be faster and more accurate).
    Pass just the filename. Set location to 'input' for a user-uploaded file,
    or 'output' for one saved by a previous tool.

    If the extracted text is long, it will be saved to /sandbox/output/ocr_output.md
    instead of being returned directly — you'll be told the path and total length,
    and can read it in parts using a separate file-reading tool.
    """
    # Resolve on the host first, then translate to the path the container sees:
    # /sandbox/input and /sandbox/output are the two mounts.
    resolved = resolve_file(filename, location)
    if isinstance(resolved, str):
        return resolved
    mount = "input" if resolved.parent == INPUT_DIR else "output"
    path = f"/sandbox/{mount}/{resolved.name}"

    content = _run_in_sandbox(f'path = {path!r}\n{OCR_CODE}')

    # A failure is not a result: without this an exception long enough to trip
    # the size check gets written to ocr_output.md and reported as if the file
    # had been read successfully.
    if content.startswith("Error:"):
        return content[:1500]

    if len(content) > MAX_INLINE_CHARS:
        output_path = OUTPUT_DIR / "ocr_output.md"
        output_path.write_text(content, encoding="utf-8")
        return (
            f"OCR text was {len(content)} characters — too long to return directly. "
            f"Saved to /sandbox/output/ocr_output.md. "
            f"Use a file-reading tool to read it in chunks."
        )

    return content