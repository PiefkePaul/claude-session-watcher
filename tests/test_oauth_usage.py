from __future__ import annotations

import json

import pytest

from claude_session_watcher.oauth_usage import (
    OAuthUsageError,
    default_oauth_credentials_path,
    load_oauth_access_token,
)


def test_default_oauth_credentials_path_uses_claude_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = default_oauth_credentials_path()
    assert path == tmp_path / ".credentials.json"


def test_load_oauth_access_token_prefers_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-from-env")
    token, source = load_oauth_access_token()
    assert token == "sk-ant-oat01-from-env"
    assert str(source) == "<env:CLAUDE_CODE_OAUTH_TOKEN>"


def test_load_oauth_access_token_reads_nested_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    creds_path = tmp_path / ".credentials.json"
    payload = {
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-nested",
            "refreshToken": "sk-ant-ort01-refresh",
        }
    }
    creds_path.write_text(json.dumps(payload), encoding="utf-8")
    token, source = load_oauth_access_token(creds_path)
    assert token == "sk-ant-oat01-nested"
    assert source == creds_path


def test_load_oauth_access_token_raises_when_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    creds_path = tmp_path / ".credentials.json"
    creds_path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    with pytest.raises(OAuthUsageError):
        load_oauth_access_token(creds_path)

