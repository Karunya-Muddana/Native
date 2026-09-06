from pathlib import Path
from langchain_core.tools import tool
from pypdf import PdfReader

from app.tools.paths import (
    BASE_DIR,
    INPUT_DIR,
    KNOWLEDGE_DIR as KNOWLEDGE_BASE_DIR,
    OUTPUT_DIR,
    resolve_file,
)


@tool
def read_pdf(filename: str, start_page: int, end_page: int, location: str = "input") -> str:
    """
    Read a range of pages from a text-based PDF and return the extracted text.
    Do NOT use this for scanned/image-based PDFs — use run_ocr instead, since
    those have no extractable text layer.
    Set location to 'input' for a user-uploaded PDF, or 'output' for one saved
    by a previous tool. Pages are 0-indexed (page 0 = first page).
    """
    try:
        file_path = resolve_file(filename, location)
        if isinstance(file_path, str):
            return file_path

        reader = PdfReader(str(file_path))
        total_pages = len(reader.pages)

        if start_page >= total_pages:
            return f"Error: start_page {start_page} is beyond the document ({total_pages} pages total)."

        end_page = min(end_page, total_pages)
        text_parts = []
        for i in range(start_page, end_page):
            page_text = reader.pages[i].extract_text() or "[No extractable text on this page]"
            text_parts.append(f"--- Page {i + 1} of {total_pages} ---\n{page_text}")

        chunk = "\n\n".join(text_parts)

        if end_page >= total_pages:
            note = f"\n\n[End of document reached — {total_pages} pages total.]"
        else:
            note = f"\n\n[Showing pages {start_page}-{end_page - 1} of {total_pages} total.]"

        return chunk + note

    except Exception as e:
        return f"Error: {str(e)}"

@tool
def read_text_file(path: str, start_line: int, end_line: int, location: str = "input") -> str:
    """
    Read a range of lines from any plain-text file — source code (.py, .js, etc.),
    config (.json, .yaml), csv, markdown, or plain text. Set location to 'output'
    for a file you or a previous tool saved, 'input' for a file the user uploaded,
    or 'knowledge_base' for an organizational document (SOP, standard, report).
    Pass just the filename. Lines are 0-indexed. Use this to read a file in chunks
    across multiple calls.
    """
    try:
        file_path = resolve_file(path, location, ("input", "output", "knowledge_base"))
        if isinstance(file_path, str):
            return file_path

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        total_lines = len(lines)
        chunk = ''.join(lines[start_line:end_line])

        if end_line >= total_lines:
            note = f"\n\n[End of file reached — total {total_lines} lines.]"
        else:
            note = f"\n\n[Showing lines {start_line}-{end_line} of {total_lines} total.]"

        return chunk + note
    except Exception as e:
        return f"Error: {str(e)}"