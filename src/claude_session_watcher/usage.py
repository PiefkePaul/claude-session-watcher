from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx


class UsageError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LimitSection:
    utilization: float
    resets_at: str | None

    @property
    def seconds_until_reset(self) -> int | None:
        if not self.resets_at:
            return None
        try:
            reset = datetime.fromisoformat(self.resets_at.replace("Z", "+00:00"))
            return max(0, int((reset - datetime.now(UTC)).total_seconds()))
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    raw: dict[str, Any]
    five_hour: LimitSection | None
    seven_day: LimitSection | None

    def is_pause_required(
        self,
        five_hour_threshold: float,
        seven_day_threshold: float,
    ) -> str | None:
        reasons: list[tuple[float, str]] = []
        if self.five_hour and self.five_hour.utilization >= five_hour_threshold:
            reasons.append(
                (
                    self.five_hour.utilization,
                    f"5-hour limit at {self.five_hour.utilization:.1f}%",
                )
            )
        if self.seven_day and self.seven_day.utilization >= seven_day_threshold:
            reasons.append(
                (
                    self.seven_day.utilization,
                    f"7-day limit at {self.seven_day.utilization:.1f}%",
                )
            )
        if not reasons:
            return None
        return max(reasons, key=lambda item: item[0])[1]

    def is_resume_ready(self, five_hour_threshold: float, seven_day_threshold: float) -> bool:
        return self.is_pause_required(five_hour_threshold, seven_day_threshold) is None


class ClaudeUsageClient:
    def __init__(self, session_key: str, org_id: str | None = None):
        if not session_key:
            raise UsageError("Missing Claude sessionKey cookie")
        self.session_key = session_key
        self.org_id = org_id

    async def _client(self) -> httpx.AsyncClient:
        headers = {
            "accept": "application/json",
            "referer": "https://claude.ai/",
            "user-agent": "Mozilla/5.0",
        }
        cookies = {"sessionKey": self.session_key}
        return httpx.AsyncClient(headers=headers, cookies=cookies, timeout=15)

    async def _detect_org_id(self) -> str:
        async with await self._client() as client:
            response = await client.get("https://claude.ai/api/organizations")
            if response.status_code == 401:
                raise UsageError("Claude browser session is expired")
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, list) or not data:
            raise UsageError("Could not detect Claude organization")
        org_id = data[0].get("uuid") or data[0].get("id")
        if not org_id:
            raise UsageError("Claude organization response did not contain an id")
        return str(org_id)

    async def fetch(self) -> UsageSnapshot:
        org_id = self.org_id or await self._detect_org_id()
        async with await self._client() as client:
            response = await client.get(f"https://claude.ai/api/organizations/{org_id}/usage")
            if response.status_code == 401:
                raise UsageError("Claude browser session is expired")
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise UsageError("Usage API returned an unexpected payload")
        return self._parse(data)

    @staticmethod
    def _parse_section(data: dict[str, Any], key: str) -> LimitSection | None:
        section = data.get(key)
        if not isinstance(section, dict):
            return None
        utilization = section.get("utilization", 0)
        try:
            utilization_float = float(utilization)
        except (TypeError, ValueError):
            utilization_float = 0.0
        resets_at = section.get("resets_at")
        return LimitSection(
            utilization=utilization_float,
            resets_at=str(resets_at) if resets_at else None,
        )

    @classmethod
    def _parse(cls, data: dict[str, Any]) -> UsageSnapshot:
        return UsageSnapshot(
            raw=data,
            five_hour=cls._parse_section(data, "five_hour"),
            seven_day=cls._parse_section(data, "seven_day"),
        )
