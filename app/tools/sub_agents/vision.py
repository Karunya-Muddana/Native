import base64
from pathlib import Path

from langchain_core.tools import tool

from app.models.vision_model import vision_model as model

from app.tools.paths import BASE_DIR, INPUT_DIR, OUTPUT_DIR, resolve_file


@tool
def run_through_vision_model(prompt: str, filename: str, location: str = "input") -> str:
    """
    Ask the vision model a question about an image file already in the sandbox.
    Use for photos, drawings, diagrams, or handwriting — anything needing visual
    understanding, not just text extraction (for scanned text, use run_ocr instead).
    location is 'input' for a user-uploaded image, or 'output' for one saved by
    a previous tool. Call list_sandbox_files first if you don't know the filename.
    """
    try:
        file_path = resolve_file(filename, location)
        if isinstance(file_path, str):
            return file_path

        with open(file_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        return model(prompt, image_b64)
    except Exception as e:
        return f"Error: {str(e)}"