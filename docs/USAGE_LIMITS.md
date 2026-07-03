# Usage Limit Detection

CSW monitors Claude account usage per configured account watcher and stores normalized
5-hour and 7-day limit snapshots.

## Sources

Runtime checks use the Claude web usage endpoint with authenticated browser cookies. If
cookie-based HTTP access is unavailable, CSW falls back to browser-driven usage fetching
through the same Camoufox profile.

`csw session probe <account>` also checks the local Claude Code OAuth usage endpoint when
credentials are available. Use `--no-oauth` to skip that optional probe or
`--oauth-credentials <path>` to point at a specific `.credentials.json` file.

## Supported Payload Shapes

CSW accepts both older and newer Claude usage response shapes and normalizes them before
storing or displaying values:

- top-level `five_hour` / `seven_day` sections with `utilization` and `resets_at`
- nested `rate_limits`, `rateLimits`, or `limits` containers
- list-style sections labeled by `window`, `name`, `type`, `bucket`, or `limit`
- percentage fields such as `used_percentage`, `usage_percent`, and `percent_used`
- ratio fields, and `used` plus `limit` counters
- reset aliases such as `reset_at`, `resetAt`, `reset_time`, and `resetTime`

Downstream UI, dashboard, history, and insight calculations read normalized
`five_hour` / `seven_day` sections.

## Troubleshooting

Run:

```bash
csw session probe <account> --json
csw watcher doctor --account <account>
csw watcher check <account>
```

If usage reads as unknown, verify that the account is logged in, that Claude Code is enabled
for the active Claude organization/profile, and that the probe output contains usable
`usage` or `oauth_usage` details.
