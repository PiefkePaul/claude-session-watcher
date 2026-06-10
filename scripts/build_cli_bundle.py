from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _exe_name() -> str:
    return "csw.exe" if sys.platform == "win32" else "csw"


def _target_default() -> str:
    machine = platform.machine().lower()
    machine_alias = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)
    if sys.platform == "win32":
        return f"windows-{machine_alias}"
    if sys.platform == "darwin":
        return f"macos-{machine_alias}"
    if sys.platform.startswith("linux"):
        return f"linux-{machine_alias}"
    return f"{sys.platform}-{machine_alias}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build standalone csw CLI binary with PyInstaller")
    parser.add_argument(
        "--target",
        default=_target_default(),
        help="Target label used for dist/<target> output directory",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = Path(__file__).resolve().parents[1]
    dist_root = root / "dist"
    build_root = root / "build"
    spec_file = root / "csw.spec"
    binary_name = _exe_name()

    shutil.rmtree(build_root, ignore_errors=True)
    if spec_file.exists():
        spec_file.unlink()

    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "csw",
        "--collect-all",
        "claude_session_watcher",
        "--collect-all",
        "camoufox",
        str(root / "scripts" / "csw_entry.py"),
    ]
    subprocess.check_call(pyinstaller_cmd, cwd=root)

    built_binary = dist_root / binary_name
    if not built_binary.exists():
        raise SystemExit(f"Expected binary was not created: {built_binary}")

    target_dir = dist_root / args.target
    target_dir.mkdir(parents=True, exist_ok=True)
    target_binary = target_dir / binary_name
    if target_binary.exists():
        target_binary.unlink()
    shutil.move(str(built_binary), str(target_binary))

    build_info = target_dir / "BUILD_INFO.txt"
    build_info.write_text(
        "\n".join(
            [
                f"target={args.target}",
                f"python={sys.version}",
                f"platform={sys.platform}",
                f"machine={platform.machine()}",
                f"binary={target_binary.name}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Built: {target_binary}")
    print(f"Metadata: {build_info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
