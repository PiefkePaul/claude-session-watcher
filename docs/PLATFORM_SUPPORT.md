# Platform Support

## Target Runtime Platforms

Current target platforms for CLI runtime:
- Windows x64
- Linux x64
- macOS arm64 (Apple Silicon)

Native app target platforms (same baseline):
- Windows x64
- Linux x64
- macOS arm64 (Apple Silicon)

## Python Baseline

- Python `>=3.10` required.
- `pip install ".[full]"` recommended for production runtime.
- `pip install ".[native]"` for native app + tray UI.

## Browser Runtime Dependency

The CLI uses Camoufox for authenticated Claude browser context and fallback control paths.

Required once per environment:

```bash
csw fetch-browser
```

## OS Notes

### Windows
- Default data dir: `%LOCALAPPDATA%/claude-session-watcher`
- Headless default: `true`
- Daemon process lifecycle handled with `tasklist` / `taskkill`
- Native OS background integration: Task Scheduler (user scope)

### Linux
- Default data dir: `$XDG_DATA_HOME/claude-session-watcher` (fallback `~/.local/share/claude-session-watcher`)
- Headless default: `virtual`
- Docker/web-console stack uses Linux display tools and is optional for CLI-first mode
- Native OS background integration: `systemd --user` unit

### macOS arm64
- Supported target for CLI runtime and packaging
- Same CLI flow as Linux/Windows
- Must be included in CI matrix and release artifact validation
- Native OS background integration: `launchd` LaunchAgent

## Support Boundaries

- CLI-first mode does not require local web UI.
- Web UI remains optional compatibility surface.
- New feature work should keep CLI behavior identical across supported OSes.

## Validation Checklist (per OS)

1. `csw --help`
2. `csw dashboard --once`
3. `csw account list`
4. `csw watcher doctor`
5. `csw watcher run` (short manual smoke test)
6. Headless OTP login flow (`csw account login ...`)
