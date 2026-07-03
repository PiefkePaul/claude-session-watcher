# Claude Session Watcher

CLI-first watcher for Claude Code Remote Control sessions.

The default product flow now runs fully in the terminal:
- no required local web server
- no required visible browser window
- account login, session management, watcher control, and status in CLI

## Install

### Core CLI

```bash
pip install .
csw --help
```

### Full feature set (recommended)

```bash
pip install ".[full]"
csw fetch-browser
```

`[full]` includes Camoufox and optional web dependencies.

### Native desktop app

```bash
pip install ".[native]"
csw native launch
```

`[native]` includes PySide6 and Camoufox.

## Quickstart (CLI-only)

1. Create account and watcher defaults:

```bash
csw account add PC
```

2. Login headless via OTP:

```bash
csw account login PC --email you@example.com
```

3. Discover sessions:

```bash
csw session discover PC
csw session list PC
```

4. Select target sessions:

```bash
csw session enable <session-id-or-key-or-title>
```

5. Run watcher:

```bash
csw watcher run
```

or as daemon:

```bash
csw watcher start
csw watcher status
```

## Command Model

Primary command groups:
- `csw account ...`
- `csw session ...`
- `csw watcher ...`
- `csw dashboard ...`
- `csw config ...`
- `csw native ...`

Detailed command reference: [docs/CLI_COMMANDS.md](docs/CLI_COMMANDS.md)
Native app guide: [docs/NATIVE_APP.md](docs/NATIVE_APP.md)
Usage limit detection: [docs/USAGE_LIMITS.md](docs/USAGE_LIMITS.md)

Legacy top-level commands are still available for compatibility but hidden from the default help output.

## Cross-Platform Runtime

Supported runtime targets:
- Windows x64
- Linux x64
- macOS arm64 (Apple Silicon)

Planned/tested matrix and constraints: [docs/PLATFORM_SUPPORT.md](docs/PLATFORM_SUPPORT.md)
Apple Silicon test runbook: [docs/MAC_ARM64_TEST_RUNBOOK.md](docs/MAC_ARM64_TEST_RUNBOOK.md)
Packaging details: [docs/PACKAGING.md](docs/PACKAGING.md)

## Native Background Service

`csw native` exposes OS-specific background integration:
- Linux: `systemd --user`
- macOS: `launchd` user agent
- Windows: Task Scheduler task (user scope)

Commands:

```bash
csw native backend
csw native status
csw native service-install
csw native service-start
csw native service-stop
csw native service-restart
csw native service-uninstall
```

## Packaging Plan

The project is moving to standalone OS-specific packages:
- one package for Windows
- one package for Linux
- one package for macOS

Implementation plan: [docs/PACKAGING_IMPLEMENTATION_PLAN.md](docs/PACKAGING_IMPLEMENTATION_PLAN.md)

## Optional Web UI (Legacy/Advanced)

A local FastAPI UI still exists for compatibility:

```bash
csw serve --open-ui
```

CLI remains the primary path.

## Usage Limit Detection

CSW checks usage per Claude account and normalizes Claude's 5-hour and 7-day limit data
before storing samples or deciding whether to pause/continue sessions.

Supported usage payloads include the older `five_hour` / `seven_day` sections and newer
`rate_limits` / `rateLimits` shapes with fields such as `used_percentage` and `resets_at`.

Probe and troubleshooting commands:

```bash
csw session probe <account> --json
csw watcher doctor --account <account>
csw watcher check <account>
```

Details: [docs/USAGE_LIMITS.md](docs/USAGE_LIMITS.md)

## Configuration

Main environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `CSW_DATA_DIR` | platform data dir | SQLite DB, logs, profiles |
| `CSW_CAMOUFOX_HEADLESS` | `true` on Windows/macOS, `virtual` on Linux | Headless mode |
| `CSW_CAMOUFOX_OS` | unset | Optional Camoufox fingerprint OS |
| `CSW_BROWSER_KEEPALIVE` | `false` | Keep browser context alive between checks |
| `CSW_RESUME_SAFETY_MARGIN_SECONDS` | `120` | Safety delay after reset before continue |
| `CSW_NOTIFY_NTFY_URL` | unset | Optional ntfy topic URL |
| `CSW_NOTIFY_NTFY_TOKEN` | unset | Optional ntfy token |

For full variable list, see `src/claude_session_watcher/settings.py`.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT
