# app/tools/desktop.py
"""Hand something to the operator's own machine to display.

Every other tool in this project brings content *in* — reads a PDF, fetches a
page, renders a chart into /sandbox/output/. This one sends it back *out*: it
asks the operating system to open a URL in the real browser, or a file in
whatever application the machine uses for that file type.

The agent runs on the operator's workstation, so "the machine" and "the user's
desktop" are the same box. That is the whole reason this can work, and also the
reason it is deliberately narrow: it opens a URL or a file that is already in a
sandbox directory, and nothing else. It never launches an arbitrary command.
"""

import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from langchain_core.tools import tool

from app.tools.paths import LOCATIONS, resolve_file

LOCATION_CHOICES = ("input", "output", "knowledge_base")

# Formats the page renders itself.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")

# A marker the UI watches for, on its own first line. The rest of the result is
# plain English for the model; this line is the instruction for the client.
SHOW_MARK = "[[show-image]] "

# A URL scheme the OS would happily hand to some other application. Only http
# and https are ever opened; anything else is a way to run something.
SAFE_SCHEMES = ("http", "https")


@tool
def open_on_screen(target: str, location: str = "output") -> str:
    """
    Show something to the user on their actual screen: open a web page in their
    real browser, or open a file — image, PDF, spreadsheet, document, text — in
    whichever application their machine uses for it.

    target is either a URL ('https://...') or the name of a file that already
    exists in a sandbox directory. location says which one to look in: 'output'
    (the default, where tools save what they produce), 'input', or
    'knowledge_base'.

    Images open inside the app, right where the user is reading. Other file
    types open in the application their machine uses for them.

    Use this when the user asks to see, view, open or look at something, and
    when what you produced is easier to look at than to describe — a chart, a
    diagram, a generated spreadsheet, a page you found. It shows the file; it
    does not read it. To read a file yourself, use read_pdf, read_text_file,
    run_ocr or run_through_vision_model. To read a web page yourself, use
    browse_web. Opening a window is for the user's benefit, not yours, so do it
    when they would want to look — not as a step in your own work.
    """
    target = (target or "").strip()
    if not target:
        return "Error: nothing to open — give a URL or a filename."

    if _looks_like_url(target):
        return _open_url(target)
    return _open_file(target, (location or "output").strip().lower())


def _looks_like_url(target: str) -> bool:
    """Only an explicit scheme counts.

    A bare 'report.pdf' is a filename, and 'example.com' is ambiguous enough
    that guessing 'website' over 'file' would eventually open a browser when
    the user meant a document sitting in the sandbox.
    """
    return "://" in target or target.lower().startswith(("http:", "https:"))


def _open_url(url: str) -> str:
    scheme = urlparse(url).scheme.lower()
    if scheme not in SAFE_SCHEMES:
        return (
            f"Error: refusing to open a '{scheme}:' link — only http and https. "
            "Other schemes hand the target to another application to execute."
        )
    if not urlparse(url).netloc:
        return f"Error: '{url}' has no host in it."

    try:
        opened = webbrowser.open(url)
    except Exception as e:
        return f"Error: could not open a browser ({str(e)})."
    if not opened:
        return f"Error: the machine reported no browser available to open {url}."
    return f"Opened {url} in the user's browser."


def _open_file(filename: str, location: str) -> str:
    if location not in LOCATION_CHOICES:
        opts = ", ".join(repr(c) for c in LOCATION_CHOICES)
        return f"Error: location must be one of {opts}, got '{location}'."

    path = resolve_file(filename, location, allowed=LOCATION_CHOICES)
    if isinstance(path, str):
        return path  # already a usable error message, with a list of what's there

    # An image is shown in the app rather than handed to the operating system.
    # Launching a photo viewer over the top of the conversation to display
    # something the page can render itself is a worse answer to "show me" — it
    # takes focus, it covers the work, and the user has to come back. Anything
    # the page cannot render (a PDF, a spreadsheet, a document) still goes to
    # the application built for it.
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return (
            f"{SHOW_MARK}{path.name}\n"
            f"Showing {path.name} in the app. The user can see it now."
        )

    try:
        _launch(path)
    except Exception as e:
        return f"Error: the machine could not open {path.name} ({str(e)})."

    where = _where(path)
    return (
        f"Opened {path.name} from /sandbox/{where} in the default application "
        f"for a {path.suffix.lstrip('.').lower() or 'file'}."
    )


def _launch(path: Path) -> None:
    """Ask the desktop to open the file, per platform.

    All three of these hand the path to the OS's file-association machinery
    rather than to a shell, so a filename is never interpreted as a command.
    """
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 — Windows-only, no shell involved
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)
    else:
        subprocess.run(["xdg-open", str(path)], check=True)


def _where(path: Path) -> str:
    """Which sandbox the resolved file actually came from — it may not be the
    one that was asked for, since resolve_file looks in the others too."""
    for key, directory in LOCATIONS.items():
        if path.parent == directory:
            return key
    return "input"
