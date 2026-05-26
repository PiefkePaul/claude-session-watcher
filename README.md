# Claude Session Watcher

Background watcher for Claude Code Remote Control sessions.

It runs a small local service with a web UI, keeps a pinned Camoufox browser profile per Claude account, reads Claude usage from that authenticated browser session, and sends pause/continue prompts into selected Claude Code Remote Control sessions before hard limits interrupt work.

## Status

Early MVP. The core pieces are present:

- local FastAPI service and web UI
- SQLite storage
- one Camoufox profile per Claude account
- lightweight usage checks from authenticated browser profile cookies
- browser fallback for Claude Web/API changes
- account-based 5-hour and 7-day usage checks
- selectable sessions under each account
- best-effort session discovery from the Claude Code dashboard
- reset-aware resume with a configurable safety margin
- pause templates for minimal, worklog, and handoff-style checkpoints
- usage history with burn-rate and pause-threshold projections
- optional ntfy notifications
- pause/continue state machine
- Docker image and compose file
- CLI status/check/log/service commands and thin npm launcher

Remote Control UI selectors may need adjustment when Claude's web UI changes.

## Why

The intended workflow is one long-running Claude Code session:

1. Start Claude Code locally with Remote Control enabled.
2. Add the Claude account to the watcher.
3. Discover or manually add the Remote Control session URL.
4. Select only the sessions that should receive pause/continue commands.
5. The account watcher monitors usage in the background.
6. At 95% of the 5-hour limit or 98% of the weekly limit, it sends a pause instruction to selected controllable sessions.
7. It resumes selected sessions when no watched limit is still at or above its configured pause threshold.

The browser `sessionKey` is not entered manually in the normal flow. The watcher reads Claude cookies from the Camoufox profile after you log in and uses direct HTTP usage checks whenever possible.

## Local Install

```bash
pipx install ".[full]"
claude-session-watcher fetch-browser
claude-session-watcher serve --open-ui
```

For CLI-only/lite installs:

```bash
pipx install .
csw status
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
docker compose pull
docker compose up -d
```

Open:

```text
http://localhost:47831
```

The Docker image exposes an on-demand browser console for Claude login:

```text
http://localhost:47832/vnc.html?autoconnect=true&resize=scale&path=websockify
```

If you run the UI on a custom host port (for example on NAS), publish a second host
port for noVNC and set `CSW_BROWSER_CONSOLE_PUBLIC_PORT` to that host port so the
wrapper can build a reachable URL.

Click `Open login` in the watcher UI. The UI opens a browser-console tab and starts
Xvfb, x11vnc, noVNC and Camoufox only for that login session. After Claude login and
profile switch, the console tab auto-detects the Claude `sessionKey`, runs `Finish login`,
and closes the browser stack. The `Finish login` and `Close browser` buttons remain
available as fallbacks.
For Google users, the browser-console also provides a `Continue with Google` helper button
that clicks the Google sign-in entry on `claude.ai/new` when visible.

The container stores browser profiles and SQLite state in the `csw-data` volume.
The compose file binds the UI and browser console to `127.0.0.1` on the host by default.

For local image development:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

## Setup Flow

1. Add an account in the web UI.
2. Click `Open login`.
3. Sign in to Claude in the Camoufox window.
4. Click `Discover` or manually add a Claude Remote Control URL as a session.
5. Select the sessions that should receive pause/continue commands.
6. Leave the service running.

The watcher uses the authenticated Camoufox browser profile for usage checks. It does not require you to copy `CLAUDE_SESSION_KEY`.
After login you can close the visible Camoufox window. Later checks read cookies directly and only reopen Camoufox when a browser fallback or Remote Control prompt send is needed.
Account watchers can be edited from the dashboard to change thresholds, check interval, enabled state, and pause/continue messages. Sessions are selected independently; unselected sessions are displayed but never receive pause/continue commands.

Every successful usage check is stored as an account-scoped history sample. The UI and CLI use those samples to estimate burn rate and the projected time when a pause threshold will be reached. Projections are only calculated within the same reset window, so a 5-hour or weekly reset is not treated as negative usage.

## Data Sources

The primary usage source is the authenticated Claude browser profile. Statusline ingest is intentionally not part of the core workflow because this service usually does not run inside the Claude Code terminal. A future optional host bridge may add local Claude Code statusline telemetry, but the default product remains browser/profile based.

`csw probe` can also test the local Claude Code OAuth usage endpoint (`https://api.anthropic.com/api/oauth/usage`) using `CLAUDE_CODE_OAUTH_TOKEN` or `~/.claude/.credentials.json` (`%USERPROFILE%\\.claude\\.credentials.json` on Windows).

## CLI

```bash
csw status
csw status --json
csw watch
csw check --all
csw logs
csw history PC
csw history PC --json
csw sessions PC
csw discover PC
csw probe PC --json
csw probe PC --session session_... --send-message "continue"
csw probe PC --no-oauth
csw add main --account PC --remote-url https://claude.ai/code/...
csw session-enable main
csw session-disable main
csw edit PC --check-interval 120 --pause-template worklog
csw enable PC
csw disable PC
```

Local background process helpers:

```bash
csw start
csw service-status
csw restart
csw stop
```

Run a basic environment check:

```bash
csw doctor
csw doctor --account PC
csw notify-test
```

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
| `CSW_CAMOUFOX_HEADLESS` | `virtual`, `false` in Docker | Camoufox headless mode |
| `CSW_CAMOUFOX_OS` | unset | Optional Camoufox fingerprint OS |
| `CSW_BROWSER_KEEPALIVE` | `false` | Keep Camoufox open between checks |
| `CSW_RESUME_SAFETY_MARGIN_SECONDS` | `120` | Extra wait after a known reset before continue |
| `CSW_UI_TOKEN` | unset | Token required for protected web/API access |
| `CSW_LOCAL_PORT_BIND_ONLY` | `false` | Allows container-internal `0.0.0.0` only when host port is locally bound |
| `CSW_BROWSER_CONSOLE_URL` | unset | noVNC browser console URL used while Docker login browser is active |
| `CSW_BROWSER_CONSOLE_PUBLIC_PORT` | unset, `47832` in Docker | Public host port used to build browser-console URL when `CSW_BROWSER_CONSOLE_URL` is not set |
| `CSW_ENABLE_VNC` | `true` in Docker | Enable on-demand Xvfb, x11vnc and noVNC inside the Docker image |
| `CSW_VNC_PORT` | `6080` in Docker | Container port for the noVNC browser console |
| `CSW_VNC_SCREEN` | `1920x1080x24` in Docker | Virtual display size for Docker browser sessions |
| `CSW_AUTO_FINISH_LOGIN` | `true` | Auto-finish login when the browser-console tab detects a Claude `sessionKey` |
| `CSW_AUTO_START_GOOGLE_LOGIN` | `false` | Auto-click "Continue with Google" once after opening browser-console (best-effort helper) |
| `CSW_AUTO_SWITCH_TO_PRO_PLAN` | `true` | If `claude.ai/code` is disabled, attempt to switch the account profile/plan to Pro automatically during `Finish login` |
| `CSW_NOTIFY_NTFY_URL` | unset | Optional ntfy topic URL for notifications |
| `CSW_NOTIFY_NTFY_TOKEN` | unset | Optional bearer token for protected ntfy topics |

Watcher defaults:

| Setting | Default |
| --- | --- |
| 5-hour pause threshold | `95%` |
| 7-day pause threshold | `98%` |
| Check interval | `60s` |
| Resume safety margin | `120s` |

Pause templates:

| Template | Purpose |
| --- | --- |
| `custom` | Use the configured pause message as-is |
| `minimal` | Stop after a safe checkpoint |
| `worklog` | Ask Claude to update `WORKLOG.md` before pausing |
| `handoff` | Ask Claude to write a handoff for another agent or later resume |

## Security Notes

- Each Claude account should use a separate Camoufox profile.
- The service does not require manually storing `CLAUDE_SESSION_KEY` in SQLite.
- The web UI refuses non-local binds unless `CSW_UI_TOKEN` is set or the Docker local-bind guard is enabled.
- The Docker browser console exposes an interactive Claude browser session. Keep it bound to localhost or protect it behind a trusted reverse proxy.
- Docker users should protect the persistent volume because it contains browser login state.
- Auto-entering account passwords is intentionally not implemented in the MVP. Prefer interactive login.
- Google OAuth pages can still require manual CAPTCHA/2FA confirmation. The helper only triggers the Google entry point.

NAS example:

```yaml
ports:
  - "40062:47831"  # CSW UI
  - "40063:6080"   # noVNC/websockify
environment:
  CSW_HOST: 0.0.0.0
  CSW_PORT: 47831
  CSW_ENABLE_VNC: "true"
  CSW_VNC_PORT: 6080
  CSW_BROWSER_CONSOLE_PUBLIC_PORT: 40063
```

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
