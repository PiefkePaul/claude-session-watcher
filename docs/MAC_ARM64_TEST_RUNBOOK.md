# macOS arm64 Test Runbook

Use this runbook on an Apple Silicon Mac to validate native CLI + Camoufox behavior.

Target path:

```text
/Volumes/SanDisk/csw
```

## 1) System/Arch check

```bash
uname -a
uname -m
python3 -c "import platform,sys; print(sys.version); print(platform.machine())"
```

Expected architecture: `arm64`.

## 2) Install

```bash
cd /Volumes/SanDisk/csw
python3 -m venv .venv-cli
source .venv-cli/bin/activate
python -m pip install -U pip
python -m pip install -e ".[full]"
```

## 3) Fetch Camoufox build

```bash
csw fetch-browser
```

## 4) Validate detected runtime target

```bash
csw watcher doctor
```

Look for:
- `runtime: darwin / arm64`
- `camoufox target: mac.arm64`

## 5) CLI smoke

```bash
csw --help
csw account --help
csw session --help
csw watcher --help
csw dashboard --once
```

## 6) Functional smoke (existing account)

```bash
csw account list
csw session list
csw watcher check --all
```

## 7) Optional login test (headless OTP)

```bash
csw account login <account> --email <email>
```

This should keep the user in terminal flow (no required visible browser UI).
