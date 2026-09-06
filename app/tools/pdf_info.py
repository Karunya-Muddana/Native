# app/tools/pdf_info.py
from pathlib import Path
from langchain_core.tools import tool
import fitz  # PyMuPDF

from app.tools.paths import BASE_DIR, INPUT_DIR, OUTPUT_DIR, resolve_file as _resolve


@tool
def get_pdf_info(filename: str, location: str = "input") -> str:
    """
    Get basic information about a PDF before deciding how to read it: page count,
    whether each page has extractable text (vs. likely scanned/image-only), and
    how many embedded images each page contains. Call this BEFORE read_pdf or
    run_ocr on an unfamiliar PDF, so you don't have to guess which tool fits —
    a page with no extractable text needs run_ocr, not read_pdf.
    location is 'input' for a user-uploaded file, or 'output' for one saved by
    a previous tool.
    """
    try:
        file_path = _resolve(filename, location)
        if isinstance(file_path, str):
            return file_path

        doc = fitz.open(str(file_path))
        lines = [f"File: {filename}", f"Total pages: {len(doc)}"]

        text_pages, image_pages, total_images = 0, 0, 0
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            images = page.get_images()
            has_text = bool(text)
            if has_text:
                text_pages += 1
            if images:
                image_pages += 1
                total_images += len(images)
            lines.append(
                f"  Page {i}: {'has text' if has_text else 'NO extractable text'}, "
                f"{len(images)} embedded image(s)"
            )
        doc.close()

        lines.append(f"\nSummary: {text_pages}/{len(lines) - 2} pages have extractable text, "
                     f"{total_images} embedded image(s) across {image_pages} page(s).")
        if text_pages == 0:
            lines.append("Verdict: this looks like a SCANNED/image-only PDF — use run_ocr, not read_pdf.")
        elif image_pages > 0:
            lines.append("Verdict: text-based PDF with embedded images — read_pdf works for text; "
                         "use extract_pdf_images if you need the images themselves.")
        else:
            lines.append("Verdict: text-based PDF, no embedded images — read_pdf is the right tool.")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def extract_pdf_images(filename: str, location: str = "input", page: int = -1) -> str:
    """
    Extract embedded images from a PDF and save them as PNG files to
    /sandbox/output/. Use this when a PDF (e.g. a P&ID or report) has diagrams
    or photos embedded in it that you need to look at directly, e.g. with
    run_through_vision_model. Call get_pdf_info first to see which pages have
    images. Set page to a specific 0-indexed page number, or leave as -1 to
    extract from all pages.
    """
    try:
        file_path = _resolve(filename, location)
        if isinstance(file_path, str):
            return file_path

        doc = fitz.open(str(file_path))
        pages = range(len(doc)) if page == -1 else [page]
        if page != -1 and (page < 0 or page >= len(doc)):
            return f"Error: page {page} is out of range (document has {len(doc)} pages)."

        saved = []
        stem = Path(filename).stem
        for p in pages:
            for img_index, img in enumerate(doc[p].get_images()):
                xref = img[0]
                base_image = doc.extract_image(xref)
                ext = base_image["ext"]
                out_name = f"{stem}_p{p}_img{img_index}.{ext}"
                out_path = OUTPUT_DIR / out_name
                out_path.write_bytes(base_image["image"])
                saved.append(out_name)
        doc.close()

        if not saved:
            return f"No embedded images found on {'page ' + str(page) if page != -1 else 'any page'}."
        return f"Saved {len(saved)} image(s) to /sandbox/output/: " + ", ".join(saved)
    except Exception as e:
        return f"Error: {str(e)}"