from __future__ import annotations

import argparse
import subprocess
import sys
import webbrowser

import uvicorn

from .settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claude Session Watcher")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the background service and web UI")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--open-ui", action="store_true", help="Open the web UI in your browser")

    subparsers.add_parser("open-ui", help="Open the configured local web UI")
    subparsers.add_parser("fetch-browser", help="Download the pinned Camoufox browser build")

    args = parser.parse_args(argv)
    settings = Settings()

    if args.command in (None, "serve"):
        host = args.host or settings.host
        port = args.port or settings.port
        if getattr(args, "open_ui", False):
            webbrowser.open(f"http://{host}:{port}")
        uvicorn.run(
            "claude_session_watcher.app:app",
            host=host,
            port=port,
            reload=False,
            access_log=True,
        )
        return 0

    if args.command == "open-ui":
        webbrowser.open(f"http://{settings.host}:{settings.port}")
        return 0

    if args.command == "fetch-browser":
        return subprocess.call([sys.executable, "-m", "camoufox", "fetch"])

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
