# Native App

The native app runs without the web server and uses a local PySide6 desktop UI.

## Install

```bash
pip install ".[native]"
csw fetch-browser
```

## Launch

```bash
csw native launch
```

The app includes:
- local dashboard UI (accounts, watchers, sessions, events)
- tray icon with quick actions (open, hide, start/stop watcher, refresh, quit)
- quick settings for:
  - `CSW_BROWSER_KEEPALIVE`
  - `CSW_AUTO_SWITCH_TO_PRO_PLAN`

## OS Service Controls

```bash
csw native backend
csw native status
csw native open
csw native quit
csw native mode
csw native mode-set temporary
csw native mode-set installed
csw native autostart status
csw native autostart on
csw native autostart off
csw native service-install
csw native service-start
csw native service-stop
csw native service-restart
csw native service-uninstall
```

Backend mapping:
- Linux -> `systemd --user`
- macOS -> `launchd` user LaunchAgent
- Windows -> Task Scheduler task (user scope)

Desktop-agent controls:
- `open` starts the agent when needed or focuses the existing app window.
- `quit` requests the running native app agent to exit.
- `mode` shows whether the desktop agent is temporary or installed.
- `mode-set temporary|installed` changes the desktop mode.
- `autostart on|off|status` manages desktop-agent autostart.

## Notes

- On Windows, task creation can fail due local policy restrictions (`Access denied`).
- On macOS and Linux, install/start commands are user-scoped and do not require system-wide root services.
- The existing `csw watcher start|stop|status` local daemon commands remain available.
