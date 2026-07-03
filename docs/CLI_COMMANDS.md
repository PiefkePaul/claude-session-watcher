# CLI Commands

This is the canonical CLI reference for `csw`.

## Dashboard

```bash
csw dashboard --once
csw dashboard --interval 3
csw dashboard --once --json
```

## Account

```bash
csw account list [--json]
csw account add <name> [--profile-dir <path>] [watcher-options]
csw account remove <account> [--purge-profile]
csw account login <account> --email <mail> [--otp <code>] [--no-close-browser]
```

`<account>` accepts account id or account name.

## Session

```bash
csw session list [account] [--json]
csw session add <title> --account <account> --remote-url <url> [--watch]
csw session enable <session>
csw session disable <session>
csw session discover <account>
csw session probe <account> [--json] [--session <session_id>] [--send-message "..."] [--no-oauth] [--oauth-credentials <path>]
```

`<session>` accepts session id, session key, or session title.

`session probe` checks Claude web usage, session listing, event access, and optionally the
local Claude Code OAuth usage endpoint. See [USAGE_LIMITS.md](USAGE_LIMITS.md) for usage
source and payload compatibility details.

## Watcher

```bash
csw watcher run
csw watcher start
csw watcher stop
csw watcher restart
csw watcher status
csw watcher check [account] [--all]
csw watcher logs [account] [--limit 30]
csw watcher history [account] [--limit 20] [--json]
csw watcher doctor [--account <account>]
csw watcher notify-test
```

## Config

```bash
csw config show
csw config set <key> <value>
```

Supported keys:
- `host`
- `port`
- `browser-keepalive`
- `check-jitter`
- `resume-margin`
- `camoufox-headless`

## Native App + OS Service

```bash
csw native launch
csw native backend
csw native status
csw native open
csw native quit
csw native mode
csw native mode-set <temporary|installed>
csw native autostart <on|off|status>
csw native service-install
csw native service-uninstall
csw native service-start
csw native service-stop
csw native service-restart
```

## Browser Binary

```bash
csw fetch-browser
```

## Watcher Option Flags

Used by `account add`:

```bash
--five-hour-threshold <float>
--seven-day-threshold <float>
--check-interval <int>
--pause-template <custom|minimal|worklog|handoff>
--pause-message <text>
--continue-message <text>
```

## Usage Limit Compatibility

CSW normalizes supported Claude usage payloads into 5-hour and 7-day sections before UI,
history, insight, pause, and continue decisions. It supports both older `five_hour` /
`seven_day` payloads and newer `rate_limits` / `rateLimits` shapes with fields such as
`used_percentage` and `resets_at`.

Troubleshooting commands:

```bash
csw session probe <account> --json
csw watcher doctor --account <account>
csw watcher check <account>
```

For details, see [USAGE_LIMITS.md](USAGE_LIMITS.md).

## Legacy Aliases

Legacy top-level commands are still supported for compatibility but are hidden from default help output.
