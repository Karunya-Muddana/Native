"""The `native` command.

    native            start the app and open it
    native start      the same, with flags
    native chat       talk to the agent in the terminal, no web UI

Runs as a console script after `pip install -e .`, and directly as
`python cli.py` before that.
"""

import argparse
import sys
import threading
import webbrowser


def _start(args):
    import uvicorn

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"Native is starting on {url} — Ctrl-C to stop.")
    uvicorn.run("api:app", host=args.host, port=args.port, reload=args.reload)


def _chat(args):
    from app.runtime.runtime import AgentRuntime

    runtime = AgentRuntime()
    session_id = runtime.new_session()
    print("Native, in the terminal. Ask something, or 'exit' to quit.")
    while True:
        try:
            text = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.strip().lower() in {"exit", "quit"}:
            break
        if not text.strip():
            continue
        print(runtime.run(text, session_id)["answer"])


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="native", description="Native by K — an agent you run yourself."
    )
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="start the app and open it (the default)")
    start.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1")
    start.add_argument("--port", type=int, default=8000, help="default 8000")
    start.add_argument("--no-browser", action="store_true", help="do not open a browser")
    start.add_argument("--reload", action="store_true", help="restart on code changes")

    sub.add_parser("chat", help="talk to the agent in the terminal, no web UI")

    args = parser.parse_args(argv)
    if args.command == "chat":
        return _chat(args)
    if args.command is None:
        args = parser.parse_args(["start"])
    return _start(args)


if __name__ == "__main__":
    sys.exit(main())
