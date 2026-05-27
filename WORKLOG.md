# Session Watcher UI — Worklog

## Status: Aktiv
**Letzte Änderung:** 2026-05-27  
**Aktueller CSS-Stand:** `?v=9`

---

## Erledigte Tasks

### ✅ Bootstrap.ps1 Fix
`2>&1 | Out-Null` → `2>$null` für alle nativen Executables (gh, git, cocoindex).  
`Invoke-ModeResume` schlägt nicht mehr fehl wenn Directory bereits existiert.

### ✅ UI-Redesign (Orbital Operations Theme)
- Fonts: Oxanium (Display) + JetBrains Mono (Data)
- CSS Variables: `--bg #070a12`, `--cyan #00c8ff`, `--green #1fe87c`, `--amber #ffb03a`, `--red #ff3d5a`
- Account Cards mit Status-Orb, Progress Bars, Stats Strip, Actions Row, Settings Drawer
- Log Panel (Slide-in von rechts, Filter-Buttons)
- Delete Confirm Modal
- Session Manager Modal mit Toggle-Switches

### ✅ Backend: Delete-Endpoints
`store.py`: `delete_session()` + `delete_account()`  
`app.py`: `POST /sessions/{id}/delete` + `POST /accounts/{id}/delete`

### ✅ Fixes Runde 1–3 (v5–v8)
- Fluid Typography: `html { font-size: clamp(16px, 0.35vw + 11px, 21px) }`
- Fluid Layout: `.page` kein `max-width`, `padding: clamp()`
- Auto-fit Grid: `.accounts-grid { repeat(auto-fit, minmax(620px, 1fr)) }`
- Session-Liste zeigt nur `watch_enabled=True`
- Timestamps: JS-Formatter `formatTs()`
- Session Manager Modal: Sortierung nach Status/Kind/Title
- Theme-Switcher: Dark / Light / High Contrast
- Modal Badge-Alignment: Fixed Grid + min-widths

### ✅ Auth Buttons: Conditional Visibility (v8)
- `is_authed` → "Re-auth" statt "Open Login"
- `browser_open` → Console/Finish Login/Close Browser nur wenn Browser offen

### ✅ Live Polling (v9) — 2026-05-27
- Neuer Backend-Endpoint: `GET /api/live` → gibt alle Account-Daten inkl. Browser-State zurück
- Frontend pollt alle **8 Sekunden** (kein manueller Refresh mehr nötig)
- Live-Dot zeigt: grün = ok, amber = stale >30s, rot = fehler
- Aktualisiert: Status-Orb, Tag, Usage Bars, Stats Strip, Burn Rate, Auth-Buttons, Watcher-Toggle
- `[hidden] { display: none !important; }` in CSS hinzugefügt

### ✅ Camoufox Multi-Tab Fix (2026-05-27)
- **Root Cause**: `_get_or_open_page()` öffnete immer neue Tabs, auch wenn leere Tabs existierten
- **Fix**: Methode prüft zuerst auf passendes URL, dann auf about:blank — erst dann neuer Tab
- Redundantes `if/else` in `open_login()` bereinigt (beide Branches waren identisch)

### ✅ Browser-Console Internal Server Error Fix (2026-05-27)
- `settings.py` war im Container veraltet → fehlte `auto_start_google_login` Attribut
- Fix: `settings.py` via `docker cp` deployed + Container neu gestartet

### ✅ VNC-free Screenshot-Proxy + Native Login Page (2026-05-27)

**Implementierte Endpoints:**
```
GET  /api/accounts/{id}/browser-screenshot?page_idx=-1  → JPEG (Cache-Control: no-store)
GET  /api/accounts/{id}/browser-pages                   → [{index, url, title}]
POST /api/accounts/{id}/browser-input?page_idx=-1       → Maus/Tastatur weiterleiten
POST /api/accounts/{id}/fill-login                      → Body: {email, password}
WS   /ws/accounts/{id}/browser-stream                   → ~12fps JPEG frames + JSON metadata
```

**`browser.py` neue Methoden:**
- `screenshot(profile_dir, page_index)` → JPEG bytes (nur wenn Profil bereits offen)
- `page_infos(profile_dir)` → [{index, url, title}] (nur wenn Profil bereits offen)
- `send_input(profile_dir, event, page_index)` → click/dblclick/key/type/scroll
- `fill_login_form(profile_dir, email, password)` → email + password Felder befüllen

**WebSocket-Streaming (`app.py`):**
- `push_frames()`: ~12fps JPEG, frame-deduplication via Hash
- Login-Detection: cheap file-check jede Frame
- Page-Infos: alle 6 Frames (~2fps), weniger Playwright-Queries
- Guard: `is_profile_open()` verhindert versehentliches Browser-Öffnen

**`browser_console.html` — Native Login Proxy:**
- **Primär-Ansicht**: Native "Sign in to Claude" Form-Karte
  - Google-Button (offizielles Branding: weiß/dunkel je Theme)
  - Email → Continue → Password → Sign in (zwei Schritte)
  - "Show browser window ↗" Escape-Hatch
- **Canvas-Ansicht**: WebSocket-Stream auf `<canvas>` (~12fps, kein Flicker)
  - Tab-Bar bei >1 Seite (z.B. Google OAuth Popup)
  - Maus + Keyboard weiterleitung via WebSocket
  - "← Back to Form" Button in Header
- **Automatische Übergänge**:
  - Google klick → Start Google Login in Camoufox → Canvas-Ansicht
  - Email/Pass Submit → Camoufox befüllt → Poll 8x → Canvas wenn 2FA
  - Login detected → Success-Overlay → `window.close()` nach 1.4s
- **Kein VNC mehr nötig** für den Login-Flow (Camoufox headless=True + screenshot())

---

## Deployment-Workflow

```powershell
# Nach Python-Änderungen: deploy + restart
$base = "D:\Development\claude-session-watcher\src\claude_session_watcher"
$pkg  = "csw-dockerhub-test:/usr/local/lib/python3.12/site-packages/claude_session_watcher"

docker cp "$base\static\styles.css"               "${pkg}/static/styles.css"
docker cp "$base\templates\index.html"             "${pkg}/templates/index.html"
docker cp "$base\templates\browser_console.html"   "${pkg}/templates/browser_console.html"
docker cp "$base\app.py"                           "${pkg}/app.py"
docker cp "$base\browser.py"                       "${pkg}/browser.py"
docker cp "$base\settings.py"                      "${pkg}/settings.py"

docker restart csw-dockerhub-test
# Browser: http://127.0.0.1:47851/?v=N (N hochzählen bei CSS-Änderungen)
```

**Warum docker cp:** App nutzt `importlib.resources.files("claude_session_watcher")`.  
**Python-Änderungen brauchen Container-Restart** (uvicorn ohne --reload).

---

## Geänderte Dateien (alle Sessions)

| Datei | Änderungen |
|-------|-----------|
| `src/claude_session_watcher/static/styles.css` | `[hidden]` Rule, Live-Dot Error States (v9) |
| `src/claude_session_watcher/templates/index.html` | Live Polling JS, Actions Row mit `hidden` Attrs, CSS v9 |
| `src/claude_session_watcher/templates/browser_console.html` | **Komplett neu**: Native Login Form + WebSocket Canvas |
| `src/claude_session_watcher/app.py` | `/api/live`, Screenshot-Proxy Endpoints, WebSocket `/ws/accounts/{id}/browser-stream` |
| `src/claude_session_watcher/browser.py` | `screenshot()`, `page_infos()`, `send_input()`, `fill_login_form()`, `_get_or_open_page` Fix |
| `src/claude_session_watcher/settings.py` | Im Container deployed (war veraltet) |

---

### ✅ OTP Email Login Automation (2026-05-27)

**Account-Deletion Cookie-Fix** bereits erledigt (shutil.rmtree + close_profile).

**Neuer Email-OTP-Flow (vollständig Camoufox-basiert — undetectable by design):**

`browser.py` neue Methoden:
- `start_email_login(profile_dir, email)` → öffnet Camoufox headless=True, navigiert zu /login, füllt Email, klickt Continue, wartet auf code_form
- `submit_otp(profile_dir, code)` → füllt `[data-testid="code"]`, klickt Continue, wartet auf logged_in
- `get_login_page_state(profile_dir)` → gibt 'email_form'|'code_form'|'logged_in'|'new_account_setup'|'unknown'|'browser_closed' zurück
- `_detect_login_state(page)` → liest DOM/URL für State-Detection
- `_wait_for_login_state_change(page, from_state, timeout_ms)` → polling bis State-Wechsel
- `_accept_cookies_banner` → `[data-testid="consent-accept"]` hinzugefügt (Claude-spezifisch)

`app.py` neue Endpoints:
- `POST /api/accounts/{id}/start-email-login` body: `{email}` → startet OTP-Flow via Camoufox
- `POST /api/accounts/{id}/submit-otp` body: `{code}` → übermittelt 6-stelligen Code an Camoufox
- `GET /api/accounts/{id}/login-page-state` → aktueller Login-State des Browsers

`browser_console.html` — Kompletter Form-Rewrite:
- **Password-Feld ENTFERNT** (Claude nutzt kein Password, nur OTP)
- Step 1: Email eingeben → "Continue" → `start-email-login` API (Camoufox im Hintergrund)
- Step 2: OTP-Input mit `letter-spacing` + Auto-Submit bei 6 Digits → `submit-otp` API
- Lade-Spinner während Camoufox arbeitet
- "← Use a different email" Button (zurück zu Step 1)
- Canvas-Fallback für Google-Login / new_account_setup / Fehler
- Init prüft `login-page-state` → springt direkt zum OTP-Step wenn Browser schon auf code_form ist

**Automation Level 1 erreicht**: Camoufox läuft headless=True, kein VNC nötig, kein sichtbares Fenster.  
**Undetectability** bleibt gewährleistet: Alle Browser-Aktionen (Email-Fill, OTP-Fill, Button-Clicks) laufen weiterhin durch Camoufox (Fingerprint-Injection, Anti-Detection).

---

### ✅ Stability + Performance (2026-05-27)

**Stabilitäts-Fixes (NS_BINDING_ABORTED + Timing-Races):**

`browser.py` — `_navigate_to_login()` komplett neu:
- Wenn schon auf `claude.ai/login` oder anderer claude.ai URL → kein `goto()` nötig
- `NS_BINDING_ABORTED`: wait 1.2s → URL prüfen → wenn claude.ai vorhanden: return (Browser hat selbst navigiert)
- `Target closed`: 2s warten → retry (bis zu 4 Versuche)
- Nach allen Versuchen: beste verfügbare Seite zurückgeben statt Exception

`browser.py` — `start_email_login()`:
- **Pre-flight**: Prüft alle offenen Seiten VOR der Navigation → direkt `code_form`/`logged_in`/`new_account_setup` zurückgeben wenn bereits vorhanden (Doppelklick-Safety)
- **Post-nav state check**: nach `_navigate_to_login` sofort State prüfen (Browser könnte schon weitergelaufen sein)
- **Fallback bei jedem Fehler**: State prüfen bevor Fehler zurückgegeben wird
- Timeouts erhöht: email-field 20s, continue-button 10s, state-change 25s

`browser_console.html` — Error Recovery:
- `emailLoginFlight` guard: Double-Click ignoriert während Request läuft
- `recoverFromApiError()`: Bei API-Fehler ERST `login-page-state` abfragen → wenn `code_form`/`logged_in` trotzdem gefunden: silent transition ohne Fehlermeldung
- **KEIN automatischer Canvas-Redirect bei API-Fehler** (war das Hauptproblem)

**Performance-Verbesserungen:**

`app.py` — `push_frames()` WebSocket:
- **Adaptive FPS**: 12fps bei Änderungen, 3fps nach ≥5 identischen Frames → ~75% CPU-Einsparung bei statischem Browser
- **`has_session_key` gecacht**: 1.5s TTL → von 12 File-Reads/Sek auf 1 alle 1.5s reduziert
- Cache-Invalidierung bei State-Änderung für sofortige Login-Detection

`browser.py` — neu: `prewarm(profile_dir)`:
- Öffnet Camoufox headless ohne Navigation
- No-op wenn Browser bereits offen

`app.py` — neu: `POST /api/accounts/{id}/prewarm`:
- Fire-and-forget Endpoint für Browser-Vorwärmung

`browser_console.html` — prewarm-on-typing:
- Beim ersten Keystroke im Email-Feld → `POST /prewarm` im Hintergrund
- Versteckt Camoufox-Startup-Latenz hinter Tipp-Zeit des Nutzers

**Google Login** aus `browser_console.html` entfernt (Button, Handler, `autoStartGoogleLogin`).  
**VNC-Reste** aus `browser_console.html` entfernt.

---

## Offene Aufgaben

1. **OTP-Flow testen** — erneut testen ob NS_BINDING_ABORTED fix greift
   - `http://127.0.0.1:47851/browser-console?account_id=X`
   - Email eingeben → Code aus Mail eingeben → Erfolg prüfen

2. **Google Login Fix** — noch defekt, später

3. **Git Push** — nach erfolgreichem Test
   - Warten auf grünes Licht vom User

4. **CLI Design Plan** — noch ausstehend

---

## Letzte bekannte URL

http://127.0.0.1:47851/
