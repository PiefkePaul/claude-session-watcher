from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


@dataclass(frozen=True)
class Targets:
    ui_base: str
    api_base: str
    vnc_url: str
    container: str


def _run(*args: str) -> str:
    completed = subprocess.run(
        list(args),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def docker_process_count(container: str) -> int:
    out = _run(
        "docker",
        "exec",
        container,
        "sh",
        "-lc",
        "ps -ef | grep -E 'Xvfb|x11vnc|websockify|camoufox-bin' | grep -v grep | wc -l",
    )
    return int(out.strip() or "0")


def vnc_available(url: str) -> bool:
    try:
        response = requests.get(url, timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


def wait_for_health(container: str, timeout_s: int = 90) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = _run("docker", "inspect", "-f", "{{.State.Health.Status}}", container)
        if status == "healthy":
            return
        time.sleep(2)
    raise RuntimeError(f"Container not healthy after {timeout_s}s")


def api_get_json(url: str, timeout_s: int = 10) -> dict:
    response = requests.get(url, timeout=timeout_s)
    response.raise_for_status()
    return response.json()


def api_post_json(url: str, timeout_s: int = 30) -> dict:
    response = requests.post(url, timeout=timeout_s)
    if not response.ok:
        raise RuntimeError(f"POST {url} failed: {response.status_code} {response.text[:1000]}")
    return response.json()


def wait_for_browser_state(
    targets: Targets,
    *,
    account_id: int,
    predicate,
    timeout_s: int = 30,
) -> dict:
    deadline = time.time() + timeout_s
    last: dict | None = None
    url = f"{targets.api_base}/api/accounts/{account_id}/browser-state"
    while time.time() < deadline:
        last = api_get_json(url)
        if predicate(last):
            return last
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for browser state, last={json.dumps(last, indent=2)}")


def ensure_account(page, *, name: str) -> None:
    page.goto("/", wait_until="domcontentloaded")
    # Only treat it as existing if the account heading exists.
    if page.get_by_role("heading", name=name).count() > 0:
        return
    page.get_by_placeholder("work-account").fill(name)
    page.get_by_role("button", name="Add account").click()
    page.wait_for_load_state("domcontentloaded")


def get_account_id_by_name(targets: Targets, name: str) -> int:
    accounts = requests.get(f"{targets.api_base}/api/accounts", timeout=10).json()
    for account in accounts:
        if account.get("name") == name:
            return int(account["id"])
    raise RuntimeError(f"Account named {name!r} not found in /api/accounts")


def main() -> None:
    targets = Targets(
        ui_base="http://127.0.0.1:47833",
        api_base="http://127.0.0.1:47833",
        vnc_url="http://127.0.0.1:47834/vnc.html?autoconnect=true&resize=scale&path=websockify",
        container="csw-pw-verify",
    )
    image = "claude-session-watcher:novnc-ondemand-test2"

    print("Starting container...")
    subprocess.run(["docker", "rm", "-f", targets.container], capture_output=True, text=True)
    success = False
    try:
        _run(
            "docker",
            "run",
            "-d",
            "--name",
            targets.container,
            "-p",
            "127.0.0.1:47833:47831",
            "-p",
            "127.0.0.1:47834:6080",
            "-e",
            "CSW_LOCAL_PORT_BIND_ONLY=true",
            "-e",
            f"CSW_BROWSER_CONSOLE_URL={targets.vnc_url}",
            image,
        )
        wait_for_health(targets.container)

        assert docker_process_count(targets.container) == 0, (
            "Expected no browser/VNC processes before any login"
        )
        assert not vnc_available(targets.vnc_url), "Expected noVNC to be unavailable before login"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(base_url=targets.ui_base)
            page = context.new_page()

            ensure_account(page, name="Main")
            account_id = get_account_id_by_name(targets, "Main")

            # When no browser window is open, the account row should not expose a
            # Browser console link.
            assert page.get_by_role("link", name="Browser console").count() == 0

            print("Testing Open login -> wrapper popup...")
            with page.expect_popup() as popup_info:
                page.get_by_role("button", name="Open login").first.click()
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded")
            popup.wait_for_url(f"**/browser-console?account_id={account_id}&wait=1")

            state = wait_for_browser_state(
                targets,
                account_id=account_id,
                predicate=lambda s: bool(s and s.get("browser_open") and s.get("vnc_ready")),
                timeout_s=40,
            )
            assert docker_process_count(targets.container) > 0, (
                "Expected browser/VNC processes after Open login"
            )
            assert vnc_available(targets.vnc_url), "Expected noVNC to be available after Open login"

            try:
                popup.wait_for_selector("iframe.console-frame.visible", timeout=30_000)
            except PlaywrightTimeoutError as exc:
                raise RuntimeError(
                    "Wrapper did not render console iframe. "
                    f"state={json.dumps(state, indent=2)}"
                ) from exc

            print("Testing that Open login stays open across a watcher tick...")
            # Historically, a background usage check could close the profile shortly after opening,
            # effectively crashing the VNC session. Waiting long enough to cross the default
            # 60s check interval ensures we catch that regression.
            time.sleep(70)
            state_after = api_get_json(
                f"{targets.api_base}/api/accounts/{account_id}/browser-state"
            )
            assert state_after.get("browser_open"), (
                "Expected browser to still be open, got: " f"{state_after}"
            )
            assert state_after.get("display_running"), (
                f"Expected display to still be running, got: {state_after}"
            )
            assert vnc_available(targets.vnc_url), "Expected noVNC to still be reachable"

            print("Testing closing popup window does not stop browser...")
            popup.close()
            wait_for_browser_state(
                targets,
                account_id=account_id,
                predicate=lambda s: bool(s and s.get("browser_open") and s.get("display_running")),
                timeout_s=20,
            )
            assert docker_process_count(targets.container) > 0, (
                "Expected browser/VNC processes to remain after closing popup tab"
            )

            print("Testing Close browser from wrapper...")
            close_resp = api_post_json(
                f"{targets.api_base}/api/accounts/{account_id}/close-browser"
            )
            assert close_resp.get("browser_open") is False
            wait_for_browser_state(
                targets,
                account_id=account_id,
                predicate=lambda s: bool(
                    s and not s.get("browser_open") and not s.get("display_running")
                ),
                timeout_s=40,
            )
            assert docker_process_count(targets.container) == 0, (
                "Expected all browser/VNC processes stopped after wrapper close"
            )
            assert not vnc_available(targets.vnc_url), (
                "Expected noVNC to be unavailable after Close browser"
            )

            print("Testing close-browser idempotency...")
            api_post_json(f"{targets.api_base}/api/accounts/{account_id}/close-browser")
            assert docker_process_count(targets.container) == 0

            print("Testing Close browser from main UI...")
            page.reload(wait_until="domcontentloaded")
            with page.expect_popup() as popup_info2:
                page.get_by_role("button", name="Open login").first.click()
            popup2 = popup_info2.value
            popup2.wait_for_load_state("domcontentloaded")
            wait_for_browser_state(
                targets,
                account_id=account_id,
                predicate=lambda s: bool(
                    s and s.get("browser_open") and s.get("display_running")
                ),
                timeout_s=40,
            )
            assert docker_process_count(targets.container) > 0
            assert vnc_available(targets.vnc_url)

            print("Testing Open login twice resets cleanly...")
            api_post_json(f"{targets.api_base}/api/accounts/{account_id}/login")
            wait_for_browser_state(
                targets,
                account_id=account_id,
                predicate=lambda s: bool(s and s.get("browser_open") and s.get("display_running")),
                timeout_s=40,
            )
            assert docker_process_count(targets.container) > 0

            page.get_by_role("button", name="Close browser").first.click()
            wait_for_browser_state(
                targets,
                account_id=account_id,
                predicate=lambda s: bool(
                    s and not s.get("browser_open") and not s.get("display_running")
                ),
                timeout_s=40,
            )
            assert docker_process_count(targets.container) == 0
            assert not vnc_available(targets.vnc_url)

            print("Testing 3x open/close loop for stray processes...")
            for idx in range(3):
                api_post_json(f"{targets.api_base}/api/accounts/{account_id}/login")
                wait_for_browser_state(
                    targets,
                    account_id=account_id,
                    predicate=lambda s: bool(
                        s and s.get("browser_open") and s.get("display_running")
                    ),
                    timeout_s=40,
                )
                assert docker_process_count(targets.container) > 0
                api_post_json(f"{targets.api_base}/api/accounts/{account_id}/close-browser")
                wait_for_browser_state(
                    targets,
                    account_id=account_id,
                    predicate=lambda s: bool(
                        s and not s.get("browser_open") and not s.get("display_running")
                    ),
                    timeout_s=40,
                )
                assert docker_process_count(targets.container) == 0, (
                    f"Loop {idx}: expected all processes stopped"
                )

            try:
                popup2.close()
            except Exception:
                pass

            context.close()
            browser.close()

        print("OK: Open login + wrapper popup + close paths verified.")
        success = True
    finally:
        if not success:
            try:
                print("==== docker ps (debug) ====")
                print(_run("docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Ports}}"))
                print("==== docker logs (tail) ====")
                print(_run("docker", "logs", "--tail", "200", targets.container))
                print("==== process list ====")
                print(
                    _run(
                        "docker",
                        "exec",
                        targets.container,
                        "sh",
                        "-lc",
                        (
                            "ps -ef | grep -E 'Xvfb|x11vnc|websockify|camoufox-bin' "
                            "| grep -v grep || true"
                        ),
                    )
                )
            except Exception:
                pass
        subprocess.run(["docker", "rm", "-f", targets.container], capture_output=True, text=True)


if __name__ == "__main__":
    main()
