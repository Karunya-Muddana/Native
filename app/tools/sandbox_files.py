from pathlib import Path
from langchain_core.tools import tool

BASE_DIR = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
OUTPUT_DIR = BASE_DIR / "sandbox_output"
INPUT_DIR = BASE_DIR / "sandbox_input"


@tool
def list_sandbox_files(location: str = "input") -> str:
    """
    List the filenames currently available in the sandbox.
    Set location to 'input' for user-uploaded files, or 'output' for files
    previously saved by other tools (e.g. run_ocr, python_runner).
    """
    try:
        if location == "input":
            base = INPUT_DIR
        elif location == "output":
            base = OUTPUT_DIR
        else:
            return f"Error: location must be 'input' or 'output', got '{location}'."
        base.mkdir(exist_ok=True)

        files = [f.name for f in base.iterdir()]
        return "\n".join(files) if files else f"No files found in /sandbox/{location}."
    except Exception as e:
        return f"Error: {str(e)}"