# Packaging Implementation Plan

This plan defines how CLI and future native app are delivered as full OS-specific packages.

## Architecture Baseline (Binding)

The project has three independent product variants. This boundary is mandatory:

1. CLI Variant (`csw-cli`)
   - Terminal-only UX.
   - No tray dependency.
   - No native desktop UI dependency.
   - Must stay usable as standalone automation tool.

2. Desktop Variant (`csw-desktop`)
   - Primary UX is: background service + tray icon + native UI.
   - Should be usable without requiring CLI usage by end users.
   - Optional helper commands for desktop operations are allowed, but not required for normal use.

3. Docker Variant (`csw-docker`)
   - Remains an independent deployment model.
   - Keeps its own web-based surface and runtime model.
   - Must not be coupled to desktop-specific runtime assumptions.

Non-goal: merging all UX models into one mandatory command surface.

## Goals

1. Preserve strict separation between CLI, Desktop, and Docker variants.
2. CLI runs on Windows, Linux, macOS arm64 with identical command behavior.
3. Users get one runnable package per OS and variant that includes required runtime parts.
4. Native desktop app reuses shared core logic without forcing CLI-centric UX.

## Phase 0: Variant Boundary Contract (must stay active)

- Keep separate entrypoints and packaging identities per variant.
- Keep dependency profiles variant-specific:
  - CLI does not require desktop UI dependencies.
  - Desktop bundles service + tray + native UI stack.
  - Docker keeps web stack independently.
- Enforce this in CI smoke tests per variant.

Exit criteria:
- A change in one variant does not require runtime dependencies of another variant.
- Release artifacts are produced per variant and per OS where applicable.

## Phase 1: CLI Contract Stabilization (in progress)

- Keep command model centered on:
  - `account`
  - `session`
  - `watcher`
  - `dashboard`
  - `config`
- Keep legacy aliases for compatibility, hidden from default help.
- Keep machine-readable output flags stable (`--json`).

Exit criteria:
- `csw --help` reflects only primary model.
- All core workflows covered through grouped commands.

## Phase 2: Cross-OS Runtime Hardening

- Isolate platform-specific process logic behind stable helpers.
- Keep path handling and defaults OS-safe.
- Ensure headless login and watcher loop behave equally on all targets.
- Guard optional Linux-only display stack behind explicit non-default paths.

Exit criteria:
- Runtime smoke tests pass on Windows/Linux/macOS arm64.

## Phase 3: Build Artifacts per OS

Planned artifact set:
- `csw-windows-x64`
- `csw-linux-x64`
- `csw-macos-arm64`

Suggested packaging track:
- Build standalone executables (PyInstaller or equivalent) per OS runner.
- Bundle required project resources and startup checks.
- Keep first-run dependency checks explicit and user-readable.

Exit criteria:
- Downloaded artifact starts and runs core CLI commands without local source checkout.

## Phase 4: CI/CD Matrix and Release Gates

Add CI matrix jobs:
- `windows-latest`
- `ubuntu-latest`
- `macos-latest`

Required gates:
1. Lint
2. Unit tests
3. CLI smoke tests
4. Artifact build + artifact self-check

Exit criteria:
- Release tags produce validated artifacts for all target OSes.

## Phase 5: Desktop Variant Hardening

- Extract shared domain/core module used by both CLI and native app.
- Keep desktop app as a thin shell over shared core services.
- Keep desktop UX centered on service+tray+native UI (not on CLI-first flows).
- Reuse OS-targeted packaging strategy and CI release gates.

Exit criteria:
- Desktop packages are OS-specific, self-contained, and operable without CLI usage.

## Open Technical Decisions

1. Final executable packager (`PyInstaller` vs `Nuitka`).
2. Code-signing/notarization strategy for macOS and Windows.
3. Browser dependency bootstrap strategy:
   - bundled binary
   - first-run fetch
   - hybrid cache strategy

## Checkpoint Discipline

Every phase execution must update `WORKLOG.md` with:
- completed tasks
- current blockers
- next concrete step
- command/tests run and result summary
