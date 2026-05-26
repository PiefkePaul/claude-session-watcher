from types import SimpleNamespace

from starlette.datastructures import URL

from claude_session_watcher.app import _resolve_console_url
from claude_session_watcher.display import DisplayState
from claude_session_watcher.settings import Settings


def _request(url: str):
    return SimpleNamespace(url=URL(url))


def _display_state(vnc_port: int = 6080) -> DisplayState:
    return DisplayState(
        enabled=True,
        running=True,
        ready=True,
        display=":99",
        vnc_port=vnc_port,
        console_url=None,
    )


def test_resolve_console_url_rewrites_loopback_to_request_host():
    settings = Settings(
        browser_console_url=(
            "http://127.0.0.1:47832/vnc.html?autoconnect=true&resize=scale&path=websockify"
        )
    )
    request = _request("http://192.168.178.254:40062/browser-console?account_id=1")

    resolved = _resolve_console_url(request, settings, _display_state())

    assert resolved == (
        "http://192.168.178.254:47832/vnc.html"
        "?autoconnect=true&resize=scale&path=websockify"
    )


def test_resolve_console_url_uses_public_port_override():
    settings = Settings(browser_console_public_port=40063)
    request = _request("http://192.168.178.254:40062/browser-console?account_id=1")

    resolved = _resolve_console_url(request, settings, _display_state(vnc_port=6080))

    assert resolved == (
        "http://192.168.178.254:40063/vnc.html"
        "?autoconnect=true&resize=scale&path=websockify"
    )


def test_resolve_console_url_keeps_local_loopback_for_local_requests():
    settings = Settings(
        browser_console_url=(
            "http://127.0.0.1:47832/vnc.html?autoconnect=true&resize=scale&path=websockify"
        )
    )
    request = _request("http://127.0.0.1:47831/browser-console?account_id=1")

    resolved = _resolve_console_url(request, settings, _display_state())

    assert resolved == settings.browser_console_url
