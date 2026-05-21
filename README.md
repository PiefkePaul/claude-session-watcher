# Claude Session Watcher

Background watcher for Claude Code Remote Control sessions.

It runs a small local service with a web UI, keeps a pinned Camoufox browser profile per Claude account, reads Claude usage from that authenticated browser session, and sends pause/continue prompts into the configured Remote Control session before hard limits interrupt work.

## Status

Early MVP. The core pieces are present:

- local FastAPI service and web UI
- SQLite storage
- one Camoufox profile per Claude account
- automatic `sessionKey` extraction from the browser profile
- 5-hour and 7-day usage checks
- pause/continue state machine
- Docker image and compose file
- Python CLI and thin npm launcher

Remote Control UI selectors may need adjustment when Claude's web UI changes.

## Why

The intended workflow is one long-running Claude Code session:

1. Start Claude Code locally with Remote Control enabled.
2. Connect the watcher to that Remote Control URL.
3. The watcher monitors usage in the background.
4. At 95% of the 5-hour limit or 98% of the weekly limit, it sends a pause instruction.
5. It resumes only when all known limits are reset enough to continue.

The browser `sessionKey` is not entered manually in the normal flow. The watcher reads it from the Camoufox profile after you log in.

## Local Install

```bash
pipx install .
claude-session-watcher fetch-browser
claude-session-watcher serve --open-ui
```

For development:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m camoufox fetch
csw serve --open-ui
```

Open the UI at:

```text
http://127.0.0.1:47831
```

## Docker

```bash
docker compose up -d --build
```

Open:

```text
http://localhost:47831
```

The container stores browser profiles and SQLite state in the `csw-data` volume.

## Setup Flow

1. Add an account in the web UI.
2. Click `Open login`.
3. Sign in to Claude in the Camoufox window.
4. Add a watcher with a Claude Remote Control URL.
5. Leave the service running.

The watcher will extract the `sessionKey` cookie from that profile and use it for usage checks.
After login you can close the visible Camoufox window. Later checks reopen the same persistent profile in the background.

## Start Claude Code With Remote Control

In the project directory you want Claude to work in:

```bash
claude --remote-control "Main Project"
```

Then open `https://claude.ai/code`, find the session, and copy its Remote Control URL into the watcher.

## Configuration

Environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `CSW_HOST` | `127.0.0.1` | Bind host |
| `CSW_PORT` | `47831` | Web UI port |
| `CSW_DATA_DIR` | platform data dir | SQLite DB, logs, browser profiles |
| `CSW_CAMOUFOX_HEADLESS` | `virtual` | Camoufox headless mode |
| `CSW_CAMOUFOX_OS` | unset | Optional Camoufox fingerprint OS |

Watcher defaults:

| Setting | Default |
| --- | --- |
| 5-hour pause threshold | `95%` |
| 7-day pause threshold | `98%` |
| Resume threshold | `5%` for every known usage section |
| Check interval | `60s` |

## Security Notes

- Each Claude account should use a separate Camoufox profile.
- The service does not require manually storing `CLAUDE_SESSION_KEY`.
- The web UI should stay bound to `127.0.0.1` unless protected by a reverse proxy.
- Docker users should protect the persistent volume because it contains browser login state.
- Auto-entering account passwords is intentionally not implemented in the MVP. Prefer interactive login.

## npm Launcher

The repository includes a thin npm launcher for convenience:

```bash
npm install -g .
csw serve --open-ui
```

The Python package still needs to be installed because Camoufox is Python-first.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT
