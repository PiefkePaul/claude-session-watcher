from __future__ import annotations

import httpx
import pytest

from claude_session_watcher.session_list import ClaudeWebSessionsClient
from claude_session_watcher.usage import ClaudeCookie


@pytest.mark.asyncio
async def test_send_user_message_uses_user_event_payload(monkeypatch):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "hello from test"},
                    }
                ]
            },
        )

    def fake_client(self):
        transport = httpx.MockTransport(handler)
        return httpx.AsyncClient(transport=transport, headers=self._headers())

    monkeypatch.setattr(ClaudeWebSessionsClient, "_client", fake_client)

    client = ClaudeWebSessionsClient(
        cookies=[ClaudeCookie(name="sessionKey", value="test", domain=".claude.ai", path="/")]
    )
    await client.send_user_message("session_01TEST", "hello from test")

    assert captured["method"] == "POST"
    assert str(captured["url"]).endswith("/v1/sessions/session_01TEST/events")
    body = str(captured["json"])
    assert '"type":"user"' in body
    assert '"role":"user"' in body
    assert '"content":"hello from test"' in body
    assert '"session_id":"session_01TEST"' in body
