#!/usr/bin/env python3
"""One script that sets the whole thing up.

    python install.py

Everything Native needs, in dependency order, skipping what is already done and
never stopping on a part that is optional. Safe to run twice: each step checks
before it acts, so a re-run repairs whatever is missing and leaves the rest
alone.

Four things are required — Python 3.10+, a virtual environment, the Python
packages, and a .env file. Three are not: Ollama, Docker and Steel each unlock
a part of the app and the app runs without any of them, telling you what is
missing. So a failure in an optional step is reported and stepped over rather
than aborting the run; a partial install that says exactly what it got is far
more useful than one that dies at Docker and leaves you guessing whether the
Python side worked.

Written in Python and run with the system interpreter on purpose. Python is the
one thing already guaranteed present — this is a Python project — so a shell
script would have meant one for cmd, one for PowerShell and one for sh, all
drifting apart. The venv it creates is used for every install that follows,
including its own.

Flags:
    --skip-ollama --skip-docker --skip-steel     leave a part alone
    --steel-dir PATH                             where Steel lives
    --yes                                        never ask, take the default
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path


def _utf8_console():
    """Windows consoles still default to cp1252, which has no em dash.

    Printing one there raises UnicodeEncodeError and kills the script — a setup
    script dying on its own summary line is a poor first impression, and the
    failure looks like the install broke rather than the console. errors are
    replaced rather than raised so that even a console that cannot render a
    character still gets the rest of the line.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                         # noqa: BLE001
            pass


_utf8_console()

ROOT = Path(__file__).resolve().parent
IS_WINDOWS = platform.system() == "Windows"
VENV = ROOT / ".venv"

# The one model that is genuinely required: the knowledge base and conversation
# memory are both embedded locally, so without it those features are off.
REQUIRED_OLLAMA = ["nomic-embed-text"]
OPTIONAL_OLLAMA = ["qwen3:4b", "qwen3-vl:4b"]

SANDBOX_IMAGE = "mrpl-sandbox-python"
SANDBOX_CONTEXT = "app/tools/sandbox/python"

STEEL_REPO = "https://github.com/steel-dev/steel-browser.git"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

READY, SKIPPED, FAILED = "ready", "skipped", "failed"

GREEN, YELLOW, RED, DIM, BOLD, OFF = (
    ("\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m")
    if os.getenv("NO_COLOR") is None and sys.stdout.isatty()
    else ("", "", "", "", "", "")
)


class Result:
    def __init__(self, status: str, detail: str, fix: str = ""):
        self.status, self.detail, self.fix = status, detail, fix


# ── shell ──────────────────────────────────────────────────────────────────
def run(command, cwd=None, env=None, check=True, quiet=False):
    """Run a command, streaming nothing unless it matters."""
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=merged,
        capture_output=True,
        text=True,
        shell=IS_WINDOWS and isinstance(command, str),
    )
    if result.returncode != 0 and check:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError("\n".join(tail[-6:]) or f"exit {result.returncode}")
    if not quiet and result.returncode != 0:
        print(f"    {DIM}{(result.stderr or '').strip()[:200]}{OFF}")
    return result


def have(program: str) -> bool:
    return shutil.which(program) is not None


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def step(number: int, total: int, label: str):
    print(f"\n{BOLD}[{number}/{total}] {label}{OFF}")


# ── steps ──────────────────────────────────────────────────────────────────
def check_python() -> Result:
    version = sys.version_info
    shown = f"{version.major}.{version.minor}.{version.micro}"
    if version < (3, 10):
        return Result(
            FAILED,
            f"Python {shown} is too old",
            "Install Python 3.10 or newer and run this again.",
        )
    print(f"    Python {shown}")
    return Result(READY, f"Python {shown}")


def ensure_venv() -> Result:
    # Already inside one: use it rather than nesting a second.
    if sys.prefix != sys.base_prefix:
        print(f"    Already inside a virtual environment: {sys.prefix}")
        return Result(READY, "using the active environment")

    if venv_python().exists():
        print(f"    Found {VENV}")
        return Result(READY, str(VENV))

    print(f"    Creating {VENV} …")
    try:
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    except Exception as e:                                        # noqa: BLE001
        return Result(FAILED, f"could not create the venv: {e}")
    return Result(READY, str(VENV))


def python_for_install() -> Path:
    return Path(sys.executable) if sys.prefix != sys.base_prefix else venv_python()


def pip_install() -> Result:
    python = python_for_install()
    if not python.exists():
        return Result(FAILED, "no interpreter to install into")

    print("    Upgrading pip …")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip", "-q"], check=False, quiet=True)

    print("    Installing the project and its dependencies (this takes a few minutes) …")
    try:
        # -e . reads pyproject.toml, which pulls its dependency list from
        # requirements.txt — so one command installs the packages AND the
        # `native` console command, and the two can never drift apart.
        run([str(python), "-m", "pip", "install", "-e", ".", "-q"], cwd=ROOT)
    except RuntimeError as e:
        detail = str(e)
        # The commonest re-run failure on Windows, and it looks alarming while
        # meaning almost nothing: pip cannot overwrite native.exe while the app
        # it belongs to is running. Say that, rather than showing a WinError.
        if "native.exe" in detail or ("WinError 32" in detail and "Scripts" in detail):
            return Result(
                FAILED,
                "Native is running, so its executable is locked",
                "Stop the running app (Ctrl-C in its terminal) and run this again. "
                "Nothing else is wrong.",
            )
        return Result(FAILED, "pip install failed", _tidy(detail))

    native = VENV / ("Scripts/native.exe" if IS_WINDOWS else "bin/native")
    where = f"the `native` command is at {native}" if native.exists() else "installed"
    print(f"    {where}")
    return Result(READY, where)


def _tidy(text: str) -> str:
    """The one line of a pip failure that says what went wrong."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in reversed(lines):
        if line.startswith("ERROR"):
            return line[:300]
    return (lines[-1] if lines else "no output")[:300]


def ensure_env() -> Result:
    target, sample = ROOT / ".env", ROOT / ".env.example"
    if target.exists():
        print("    .env already exists — left alone")
        return Result(READY, ".env already present")
    if not sample.exists():
        return Result(FAILED, "no .env.example to copy from")

    shutil.copyfile(sample, target)
    print("    Created .env from .env.example")
    return Result(READY, "created .env — add whichever keys you have")


def ensure_ollama(assume_yes: bool) -> Result:
    if not have("ollama"):
        return Result(
            SKIPPED,
            "Ollama is not installed",
            "Install it from https://ollama.com to get a model that needs no key, "
            "and the local embeddings the knowledge base and memory require.",
        )

    try:
        listing = run(["ollama", "list"], check=True).stdout
    except RuntimeError:
        return Result(
            SKIPPED,
            "Ollama is installed but not responding",
            "Start it (`ollama serve`) and run this script again.",
        )

    installed = {line.split()[0] for line in listing.splitlines()[1:] if line.strip()}

    def present(model: str) -> bool:
        return any(m == model or m.startswith(model + ":") for m in installed)

    pulled, failed = [], []
    for model in REQUIRED_OLLAMA:
        if present(model):
            print(f"    {model} — already there")
            continue
        print(f"    Pulling {model} (required for the knowledge base and memory) …")
        try:
            run(["ollama", "pull", model], check=True)
            pulled.append(model)
        except RuntimeError as e:
            failed.append(f"{model}: {e}")

    wanted = [m for m in OPTIONAL_OLLAMA if not present(m)]
    if wanted:
        listed = ", ".join(wanted)
        if assume_yes or ask(f"    Also pull {listed}? These are several GB", default=False):
            for model in wanted:
                print(f"    Pulling {model} …")
                try:
                    run(["ollama", "pull", model], check=True)
                    pulled.append(model)
                except RuntimeError as e:
                    failed.append(f"{model}: {e}")
        else:
            print("    Skipped the optional models — `ollama pull` them any time")

    if failed:
        return Result(FAILED, "some models did not pull", "; ".join(failed))
    return Result(READY, f"pulled {', '.join(pulled)}" if pulled else "all models present")


def ensure_docker() -> Result:
    if not have("docker"):
        return Result(
            SKIPPED,
            "Docker is not installed",
            "Needed only by python_runner and run_ocr. https://docs.docker.com/get-docker/",
        )
    if run(["docker", "info"], check=False, quiet=True).returncode != 0:
        return Result(
            SKIPPED,
            "Docker is installed but the daemon is not running",
            "Start Docker Desktop, then re-run this script.",
        )

    existing = run(["docker", "images", "-q", SANDBOX_IMAGE], check=False).stdout.strip()
    if existing:
        print(f"    Image {SANDBOX_IMAGE} already built")
        return Result(READY, "sandbox image present")

    print(f"    Building {SANDBOX_IMAGE} (needs network once, for the OCR models) …")
    try:
        run(["docker", "build", "-t", SANDBOX_IMAGE, SANDBOX_CONTEXT], cwd=ROOT)
    except RuntimeError as e:
        return Result(FAILED, "the sandbox image did not build", str(e))
    return Result(READY, f"built {SANDBOX_IMAGE}")


def ensure_steel(steel_dir: Path, assume_yes: bool) -> Result:
    if not have("node") or not have("npm"):
        return Result(
            SKIPPED,
            "Node.js is not installed",
            "Steel needs Node 22+. https://nodejs.org — then re-run this script.",
        )

    major = 0
    try:
        major = int(run(["node", "--version"]).stdout.strip().lstrip("v").split(".")[0])
    except Exception:                                             # noqa: BLE001
        pass
    if major and major < 22:
        return Result(SKIPPED, f"Node {major} is too old", "Steel needs Node 22 or newer.")

    if not have("git"):
        return Result(SKIPPED, "git is not installed", "Needed to fetch Steel.")

    chrome = next((p for p in CHROME_PATHS if Path(p).exists()), None)
    if not chrome:
        print(f"    {YELLOW}No Chrome found in the usual places{OFF} — set "
              "CHROME_EXECUTABLE_PATH in the launcher if it is somewhere else.")

    if not steel_dir.exists():
        if not assume_yes and not ask(f"    Clone Steel into {steel_dir}?"):
            return Result(SKIPPED, "declined", "Re-run with --steel-dir to choose another location.")
        print(f"    Cloning Steel into {steel_dir} …")
        try:
            run(["git", "clone", "--depth", "1", STEEL_REPO, str(steel_dir)])
        except RuntimeError as e:
            return Result(FAILED, "clone failed", str(e))
    else:
        print(f"    Found an existing checkout at {steel_dir}")

    if (steel_dir / "node_modules").exists():
        print("    Dependencies already installed")
    else:
        print("    Installing Steel's dependencies (several minutes) …")
        try:
            run(["npm", "install", "--no-audit", "--no-fund"], cwd=steel_dir)
        except RuntimeError as e:
            return Result(FAILED, "npm install failed", str(e))

    launcher = write_steel_launcher(steel_dir, chrome)
    print(f"    Wrote {launcher}")
    return Result(READY, f"start it with {launcher}")


def write_steel_launcher(steel_dir: Path, chrome: str | None) -> Path:
    """A one-command start for Steel, with the two settings that matter baked in.

    `-w api` rather than the root `npm run dev` is not a preference. The root
    script also starts the UI workspace, whose own script is
    `vite --host ${HOST:-0.0.0.0}` — POSIX parameter expansion that cmd.exe
    passes through literally, so vite gets a nonsense hostname, exits 1, and
    concurrently takes the API down with it. The UI is only Steel's session
    viewer, and it is redundant here because the browser window is on screen.

    CHROME_HEADLESS=false is the other one: without it there is no window to
    watch, which is the whole point of driving a browser this way.
    """
    if IS_WINDOWS:
        path = steel_dir / "start-steel.cmd"
        # newline="" matters: the default translates every \n to \r\n, and the
        # text below already ends its lines that way, so the file would come out
        # with \r\r\n on every line.
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(
                "@echo off\r\n"
                "REM Starts the Steel API only. Written by Native's install.py.\r\n"
                "REM The UI workspace is skipped on purpose: its dev script uses POSIX\r\n"
                "REM ${HOST:-0.0.0.0} syntax that cmd.exe cannot expand, so it exits 1\r\n"
                "REM and takes the API with it. The UI is only the session viewer.\r\n"
                "set CHROME_HEADLESS=false\r\n"
                "set HOST=127.0.0.1\r\n"
                "set PORT=3000\r\n"
                + (f"set CHROME_EXECUTABLE_PATH={chrome}\r\n" if chrome else "")
                + f'cd /d "{steel_dir}"\r\n'
                + "npm run dev -w api\r\n"
            )
        return path

    path = steel_dir / "start-steel.sh"
    path.write_text(
        "#!/bin/sh\n"
        "# Starts the Steel API only. Written by Native's install.py.\n"
        "# The UI workspace is skipped: it is only the session viewer, and the\n"
        "# browser window is already on screen.\n"
        "export CHROME_HEADLESS=false\n"
        "export HOST=127.0.0.1\n"
        "export PORT=3000\n"
        + (f'export CHROME_EXECUTABLE_PATH="{chrome}"\n' if chrome else "")
        + f'cd "{steel_dir}"\n'
        "npm run dev -w api\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def ask(question: str, default: bool = True) -> bool:
    """Ask, or take `default` when there is nobody to ask.

    The default is per-question rather than a blanket yes: piping this script
    into a terminal should still clone Steel, because that is what it is for,
    but it should not quietly pull several gigabytes of optional models.
    """
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


# ── report ─────────────────────────────────────────────────────────────────
def summarise(results: dict[str, Result], steel_dir: Path) -> int:
    print(f"\n{BOLD}{'─' * 64}{OFF}\n{BOLD}Summary{OFF}\n")

    mark = {READY: f"{GREEN}ready{OFF}", SKIPPED: f"{YELLOW}skipped{OFF}", FAILED: f"{RED}failed{OFF}"}
    for label, result in results.items():
        print(f"  {mark[result.status]:<18} {label:<22} {DIM}{result.detail}{OFF}")

    notes = [(l, r) for l, r in results.items() if r.fix]
    if notes:
        print(f"\n{BOLD}Worth knowing{OFF}\n")
        for label, result in notes:
            first, *rest = result.fix.splitlines() or [""]
            print(f"  {label}: {first}")
            for line in rest[:2]:
                print(f"    {DIM}{line.strip()}{OFF}")

    required = ["Python", "Virtual environment", "Python packages", "Configuration"]
    broken = [l for l in required if results.get(l) and results[l].status != READY]
    if broken:
        print(f"\n{RED}Setup did not complete: {', '.join(broken)}.{OFF}")
        return 1

    activate = ".venv\\Scripts\\activate" if IS_WINDOWS else "source .venv/bin/activate"
    launcher = steel_dir / ("start-steel.cmd" if IS_WINDOWS else "start-steel.sh")

    print(f"\n{BOLD}Start it{OFF}\n")
    print(f"  {activate}")
    print("  native")
    if results.get("Steel browser") and results["Steel browser"].status == READY:
        print(f"\n  For the live browser, in its own terminal first:\n    {launcher}")
    print(f"\n  Then open {BOLD}http://127.0.0.1:8000{OFF}. Add API keys to .env when you have them;")
    print("  the app runs without any and tells you what is missing.\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up Native and everything it talks to.")
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-steel", action="store_true")
    parser.add_argument(
        "--steel-dir",
        type=Path,
        default=Path.home() / "steel-browser",
        help="where to put Steel (default: ~/steel-browser, kept out of the project "
             "so a cloud-synced folder never tries to sync node_modules)",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="take every default")
    args = parser.parse_args()

    steel_dir = args.steel_dir.expanduser().resolve()

    print(f"{BOLD}Native — setup{OFF}")
    print(f"{DIM}{ROOT}{OFF}")

    results: dict[str, Result] = {}
    total = 7

    step(1, total, "Python")
    results["Python"] = check_python()
    if results["Python"].status == FAILED:
        return summarise(results, steel_dir)

    step(2, total, "Virtual environment")
    results["Virtual environment"] = ensure_venv()

    step(3, total, "Python packages")
    results["Python packages"] = (
        pip_install() if results["Virtual environment"].status == READY
        else Result(FAILED, "no environment to install into")
    )

    step(4, total, "Configuration")
    results["Configuration"] = ensure_env()

    step(5, total, "Ollama models")
    results["Ollama models"] = (
        Result(SKIPPED, "--skip-ollama") if args.skip_ollama else ensure_ollama(args.yes)
    )

    step(6, total, "Docker sandbox")
    results["Docker sandbox"] = (
        Result(SKIPPED, "--skip-docker") if args.skip_docker else ensure_docker()
    )

    step(7, total, "Steel browser")
    results["Steel browser"] = (
        Result(SKIPPED, "--skip-steel") if args.skip_steel
        else ensure_steel(steel_dir, args.yes)
    )

    return summarise(results, steel_dir)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run when you are ready — nothing is half-done that "
              "a second run will not finish.")
        sys.exit(130)
