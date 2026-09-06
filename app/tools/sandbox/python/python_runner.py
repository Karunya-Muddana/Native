import docker
from pathlib import Path
from langchain_core.tools import tool

from app.tools.readback import changed_since, describe, snapshot

client = None


def _get_client():
    global client
    if client is None:
        client = docker.from_env()
    return client


BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = BASE_DIR / "sandbox_output"
INPUT_DIR = BASE_DIR / "sandbox_input"
OUTPUT_DIR.mkdir(exist_ok=True)
INPUT_DIR.mkdir(exist_ok=True)

MAX_REPORTED_FILES = 8


@tool
def python_runner(code: str) -> str:
    """
    A tool that executes Python code in a sandboxed environment.

    Reading uploaded files: any document, image, or spreadsheet the user has provided
    is available read-only at /sandbox/input/. List its contents with os.listdir('/sandbox/input')
    if you need to check what's available.

    Saving output: save any files you want the user to keep (plots, csvs, documents)
    to /sandbox/output/. After saving a file, always print its full path,
    e.g. print("Saved to /sandbox/output/plot.png")

    Any file the code writes is opened afterwards and described back to you —
    sheet sizes, value ranges, page and slide counts. Base what you tell the
    user on that description, not on what the code was supposed to do.
    """
    before = snapshot(OUTPUT_DIR)
    try:
        output = _get_client().containers.run(
            image="mrpl-sandbox-python",
            command=["python3", "-c", code],
            network_disabled=True,
            mem_limit="512m",
            volumes={
                str(INPUT_DIR): {"bind": "/sandbox/input", "mode": "ro"},
                str(OUTPUT_DIR): {"bind": "/sandbox/output", "mode": "rw"},
            },
            remove=True,
        )
        return _with_readback(output.decode(), before)
    except Exception as e:
        # A crashed run can still have written files before it died, and those
        # half-finished artifacts are exactly what the next step must not trust.
        return _with_readback(f"Error: {str(e)}", before)


def _with_readback(output: str, before: dict) -> str:
    """Append what the run actually left in /sandbox/output/.

    Read from disk rather than from the script's own print statements: the code
    reports what it meant to write, and the point of this is to catch the case
    where those two differ.
    """
    produced = changed_since(OUTPUT_DIR, before)
    if not produced:
        return output

    lines = [output.rstrip(), "", f"Files written to /sandbox/output/ ({len(produced)}):"]
    for path in produced[:MAX_REPORTED_FILES]:
        lines.append(f"  - {describe(path)}")
    if len(produced) > MAX_REPORTED_FILES:
        lines.append(f"  - … and {len(produced) - MAX_REPORTED_FILES} more")
    lines.append(
        "This is the file as saved. If it disagrees with what the code was meant to "
        "produce, trust this and say so."
    )
    return "\n".join(lines)
