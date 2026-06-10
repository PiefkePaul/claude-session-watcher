# Packaging

This project now includes an initial standalone CLI packaging workflow based on PyInstaller.

## Local Build

Build on the target OS (PyInstaller does not cross-compile):

```bash
python -m pip install -e ".[full,dev]"
python -m pip install pyinstaller
python scripts/build_cli_bundle.py
```

Output:
- `dist/<target>/csw` (Linux/macOS)
- `dist/<target>/csw.exe` (Windows)
- `dist/<target>/BUILD_INFO.txt`

Optional explicit target label:

```bash
python scripts/build_cli_bundle.py --target macos-arm64
```

## CI Packaging Workflow

GitHub Actions workflow:
- [package-cli.yml](../.github/workflows/package-cli.yml)

Builds artifacts for:
- `linux-x64`
- `windows-x64`
- `macos-arm64`

Trigger:
- `workflow_dispatch`
- release tags `v*`

## Notes

- Standalone packaging is currently focused on CLI executable delivery.
- Runtime browser assets are fetched by `csw fetch-browser` on the target machine.
- Native app packaging will later reuse this multi-OS pipeline model.
