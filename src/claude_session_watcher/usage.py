from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx


class UsageError(Exception):
    pass


class UsageAuthError(UsageError):
    pass


@dataclass(frozen=True, slots=True)
class ClaudeCookie:
    name: str
    value: str
    domain: str = "claude.ai"
    path: str = "/"


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
    def __init__(
        self,
        session_key: str | None = None,
        *,
        cookies: list[ClaudeCookie] | None = None,
        org_id: str | None = None,
    ):
        if not session_key and not cookies:
            raise UsageError("Missing Claude browser cookies")
        self.cookies = cookies or [ClaudeCookie(name="sessionKey", value=str(session_key))]
        self.org_id = org_id

    async def _client(self) -> httpx.AsyncClient:
        headers = {
            "accept": "application/json, text/plain, */*",
            "origin": "https://claude.ai",
            "referer": "https://claude.ai/code",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) "
                "Gecko/20100101 Firefox/139.0"
            ),
        }
        client = httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=False)
        for cookie in self.cookies:
            client.cookies.set(
                cookie.name,
                cookie.value,
                domain=cookie.domain,
                path=cookie.path or "/",
            )
        return client

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise UsageAuthError(f"Claude usage request was rejected: HTTP {response.status_code}")
        response.raise_for_status()

    async def _detect_org_id(self) -> str:
        async with await self._client() as client:
            response = await client.get("https://claude.ai/api/organizations")
            self._raise_for_response(response)
            data = response.json()
        if not isinstance(data, list) or not data:
            raise UsageError("Could not detect Claude organization")
        org_id = data[0].get("uuid") or data[0].get("id")
        if not org_id:
            raise UsageError("Claude organization response did not contain an id")
        return str(org_id)

    async def fetch_raw(self) -> dict[str, Any]:
        if self.org_id:
            orgs: list[dict[str, Any]] = [{"uuid": self.org_id}]
        else:
            async with await self._client() as client:
                response = await client.get("https://claude.ai/api/organizations")
                self._raise_for_response(response)
                data = response.json()
            if not isinstance(data, list) or not data:
                raise UsageError("Could not detect Claude organization")
            orgs = [org for org in data if isinstance(org, dict)]

        async with await self._client() as client:
            errors: list[str] = []
            for org in orgs:
                org_id = org.get("uuid") or org.get("id")
                if not org_id:
                    continue
                response = await client.get(f"https://claude.ai/api/organizations/{org_id}/usage")
                try:
                    self._raise_for_response(response)
                except UsageAuthError:
                    raise
                except httpx.HTTPStatusError as exc:
                    errors.append(f"{org_id}: HTTP {exc.response.status_code}")
                    continue
                usage = response.json()
                if isinstance(usage, dict) and ("five_hour" in usage or "seven_day" in usage):
                    usage["_csw_org_id"] = str(org_id)
                    usage["_csw_org_name"] = org.get("name")
                    return usage
                errors.append(f"{org_id}: usage payload missing expected keys")
        detail = "; ".join(errors) if errors else "no usable organization ids found"
        raise UsageError(f"Could not read Claude usage: {detail}")

    async def fetch(self) -> UsageSnapshot:
        return self._parse(await self.fetch_raw())

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
