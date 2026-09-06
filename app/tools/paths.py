"""app/tools/paths.py — one place that knows where sandbox files live.

Every reader tool takes a `location` argument, and the model has to guess it.
It guesses wrong often enough that a file sitting in the next directory gets
reported to the user as missing. So resolution is forgiving: look where we were
told, then look in the other sandboxes, and only fail once the file is genuinely
nowhere — and say where we looked when that happens.
"""

from pathlib import Path

BASE_DIR = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())

INPUT_DIR = BASE_DIR / "sandbox_input"
OUTPUT_DIR = BASE_DIR / "sandbox_output"
KNOWLEDGE_DIR = BASE_DIR / "knowledge_base"

LOCATIONS = {
    "input": INPUT_DIR,
    "output": OUTPUT_DIR,
    "knowledge_base": KNOWLEDGE_DIR,
}


def resolve_file(
    filename: str,
    location: str,
    allowed: tuple[str, ...] = ("input", "output"),
) -> Path | str:
    """Return a real Path, or an error string the tool can hand straight back.

    Any directory part of `filename` is discarded: tools address files by name,
    and accepting a path would let a caller walk out of the sandbox.
    """
    if location not in allowed:
        opts = " or ".join(repr(a) for a in allowed)
        return f"Error: location must be {opts}, got '{location}'."

    name = Path(str(filename)).name
    if not name:
        return "Error: no filename given."

    # Where we were told first, then everywhere else we are allowed to look.
    for key in [location, *[k for k in allowed if k != location]]:
        candidate = LOCATIONS[key] / name
        if candidate.is_file():
            return candidate

    return f"Error: File '{name}' does not exist in {_looked_in(allowed)}.{_hint(name, allowed)}"


def _looked_in(allowed: tuple[str, ...]) -> str:
    return ", ".join(f"/sandbox/{k}" for k in allowed)


def _hint(name: str, allowed: tuple[str, ...]) -> str:
    """Name the files that are actually there, so the next call can succeed."""
    available = []
    for key in allowed:
        directory = LOCATIONS[key]
        if directory.is_dir():
            available += [f"{key}/{f.name}" for f in sorted(directory.iterdir()) if f.is_file()]
    if not available:
        return " Those directories are empty."

    stem = Path(name).stem.lower()[:4]
    near = [a for a in available if stem and stem in a.lower()]
    shortlist = near or available
    return " Available: " + ", ".join(shortlist[:8]) + ("…" if len(shortlist) > 8 else "") + "."
