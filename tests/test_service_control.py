from claude_session_watcher.service_control import ServiceStatus, start_service
from claude_session_watcher.settings import Settings


def test_start_service_uses_cli_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    settings = Settings()
    settings.ensure_dirs()

    status_calls = {"count": 0}

    def fake_service_status(_settings):
        status_calls["count"] += 1
        if status_calls["count"] == 1:
            return ServiceStatus(running=False, pid=None, pid_path=_settings.pid_path)
        return ServiceStatus(running=True, pid=4321, pid_path=_settings.pid_path)

    popen_calls: dict[str, object] = {}

    class DummyProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        popen_calls["command"] = command
        popen_calls["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(
        "claude_session_watcher.service_control.service_status",
        fake_service_status,
    )
    monkeypatch.setattr("claude_session_watcher.service_control.subprocess.Popen", fake_popen)
    monkeypatch.setattr("claude_session_watcher.service_control.time.sleep", lambda _seconds: None)

    result = start_service(settings)

    assert result.running is True
    assert result.pid == 4321
    assert popen_calls["command"][-1] == "run"
