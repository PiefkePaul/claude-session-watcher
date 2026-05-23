from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    event_type: str
    title: str
    message: str
    level: str = "info"


class Notifier(Protocol):
    async def notify(self, event: NotificationEvent) -> None:
        pass


class NoopNotifier:
    async def notify(self, event: NotificationEvent) -> None:
        return None


class NtfyNotifier:
    def __init__(self, url: str, *, token: str | None = None):
        self.url = url
        self.token = token

    async def notify(self, event: NotificationEvent) -> None:
        headers = {
            "Title": event.title,
            "Tags": _tags_for_level(event.level),
            "Priority": _priority_for_level(event.level),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self.url, content=event.message, headers=headers)
            response.raise_for_status()


def notifier_from_settings(settings) -> Notifier:
    url = getattr(settings, "notify_ntfy_url", None)
    if not url:
        return NoopNotifier()
    return NtfyNotifier(url, token=getattr(settings, "notify_ntfy_token", None))


def _priority_for_level(level: str) -> str:
    if level == "error":
        return "high"
    if level == "warning":
        return "default"
    return "low"


def _tags_for_level(level: str) -> str:
    if level == "error":
        return "warning"
    if level == "warning":
        return "hourglass"
    return "white_check_mark"
