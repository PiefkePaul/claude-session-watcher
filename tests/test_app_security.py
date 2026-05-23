from base64 import b64encode

import pytest
from fastapi.testclient import TestClient

from claude_session_watcher.app import create_app
from claude_session_watcher.settings import Settings


def test_public_bind_requires_token(tmp_path):
    settings = Settings(data_dir=tmp_path, host="0.0.0.0")

    with pytest.raises(ValueError):
        create_app(settings)


def test_ui_token_protects_web_ui(tmp_path):
    settings = Settings(data_dir=tmp_path, ui_token="secret")

    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 401
        auth = b64encode(b"csw:secret").decode()
        assert client.get("/", headers={"Authorization": f"Basic {auth}"}).status_code == 200
