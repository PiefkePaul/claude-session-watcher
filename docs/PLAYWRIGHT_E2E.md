# Playwright E2E Tests (Docker)

Stand: 2026-05-26 (Europe/Berlin)

Diese Tests laufen gegen ein lokal gebautes Image `claude-session-watcher:novnc-ondemand-test` und starten dafuer
einen frischen Container auf `127.0.0.1:47833` (UI) und `127.0.0.1:47834` (noVNC).

Script: `scripts/playwright_verify_login_flow.py`

## Abgedeckte Faelle

- noVNC ist vor `Open login` nicht erreichbar
- `Open login` oeffnet den Wrapper-Tab `/browser-console?...&wait=1`
- Wrapper rendert das noVNC iframe (Console sichtbar)
- Popup/Wrapper-Tab schliessen beendet den Browser nicht (Service bleibt aktiv)
- `Close browser` ueber API stoppt Camoufox + Xvfb + x11vnc + websockify (Prozessanzahl geht auf 0)
- `Close browser` ist idempotent (zweites Close bleibt stabil)
- `Close browser` aus der Main-UI stoppt ebenfalls alles (Prozessanzahl 0, noVNC wieder nicht erreichbar)
- `Open login` zweimal hintereinander ist stabil (zweites `Open login` liefert keinen 500 mehr)
- 3x `Open login`/`Close browser` Loop: danach keine stray Prozesse (Prozessanzahl bleibt 0)

## Ergebnis

- PASS: Alle oben gelisteten Checks liefen erfolgreich durch.
