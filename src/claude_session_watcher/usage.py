from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx


class UsageError(Exception):
    pass


class UsageAuthError(UsageError):
    pass


class UsageBlockedError(UsageError):
    """The request was blocked by bot protection (e.g. a Cloudflare challenge).

    The session itself may be perfectly valid — callers should fall back to a
    real browser instead of treating this as an authentication failure.
    """


class UsageLoginRequiredError(UsageError):
    pass


def is_cloudflare_challenge(response: httpx.Response) -> bool:
    """Detect a Cloudflare bot-challenge response (as opposed to a real API error).

    Args:    response (httpx.Response): HTTP response to inspect.
    Returns: bool: True when Cloudflare served a challenge page.
    Depends: httpx
    """
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if "text/html" not in response.headers.get("content-type", "").lower():
        return False
    try:
        text = response.text[:2000].lower()
    except Exception:  # noqa: BLE001 - body may not be decodable
        return False
    return "just a moment" in text or "challenge-platform" in text


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
            if is_cloudflare_challenge(response):
                raise UsageBlockedError(
                    "Claude usage request was blocked by a Cloudflare challenge "
                    "(bot protection, not an auth problem)"
                )
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
                if isinstance(usage, dict):
                    snapshot = self._parse(usage)
                    if snapshot.five_hour or snapshot.seven_day:
                        normalized_usage = dict(snapshot.raw)
                        normalized_usage["_csw_org_id"] = str(org_id)
                        normalized_usage["_csw_org_name"] = org.get("name")
                        return normalized_usage
                errors.append(f"{org_id}: usage payload missing expected keys")
        detail = "; ".join(errors) if errors else "no usable organization ids found"
        raise UsageError(f"Could not read Claude usage: {detail}")

    async def fetch(self) -> UsageSnapshot:
        return self._parse(await self.fetch_raw())

    @classmethod
    def _parse_section(cls, data: dict[str, Any], key: str) -> LimitSection | None:
        section = cls._find_limit_section(data, key)
        return cls._section_to_limit(section)

    @classmethod
    def _find_limit_section(cls, value: Any, key: str, *, depth: int = 0) -> dict[str, Any] | None:
        if depth > 5:
            return None
        if isinstance(value, dict):
            for section_key, section_value in value.items():
                if cls._matches_limit_key(section_key, key) and isinstance(section_value, dict):
                    return section_value

            for section_value in value.values():
                if cls._section_label_matches(section_value, key):
                    return section_value

            for section_key, section_value in value.items():
                if cls._is_usage_container_key(section_key):
                    found = cls._find_limit_section(section_value, key, depth=depth + 1)
                    if found is not None:
                        return found

            for section_value in value.values():
                if isinstance(section_value, dict | list):
                    found = cls._find_limit_section(section_value, key, depth=depth + 1)
                    if found is not None:
                        return found
        elif isinstance(value, list):
            for item in value:
                if cls._section_label_matches(item, key):
                    return item
            for item in value:
                if isinstance(item, dict | list):
                    found = cls._find_limit_section(item, key, depth=depth + 1)
                    if found is not None:
                        return found
        return None

    @classmethod
    def _section_to_limit(cls, section: dict[str, Any] | None) -> LimitSection | None:
        if not isinstance(section, dict):
            return None
        utilization_float = cls._section_utilization(section)
        if utilization_float is None:
            utilization_float = 0.0
        resets_at = cls._section_reset(section)
        return LimitSection(
            utilization=utilization_float,
            resets_at=resets_at,
        )

    @classmethod
    def _parse(cls, data: dict[str, Any]) -> UsageSnapshot:
        five_source = cls._find_limit_section(data, "five_hour")
        seven_source = cls._find_limit_section(data, "seven_day")
        five_hour = cls._section_to_limit(five_source)
        seven_day = cls._section_to_limit(seven_source)
        raw = dict(data)
        if five_hour:
            raw["five_hour"] = cls._canonical_section(five_source, five_hour)
        if seven_day:
            raw["seven_day"] = cls._canonical_section(seven_source, seven_day)
        return UsageSnapshot(
            raw=raw,
            five_hour=five_hour,
            seven_day=seven_day,
        )

    @classmethod
    def _canonical_section(
        cls,
        source: dict[str, Any] | None,
        limit: LimitSection,
    ) -> dict[str, Any]:
        section = dict(source) if isinstance(source, dict) else {}
        section["utilization"] = limit.utilization
        section["resets_at"] = limit.resets_at
        return section

    @classmethod
    def _section_utilization(cls, section: dict[str, Any]) -> float | None:
        for key in (
            "utilization",
            "used_percentage",
            "used_percent",
            "usage_percentage",
            "usage_percent",
            "percent_used",
            "percentage_used",
            "percentage",
            "percent",
        ):
            value = cls._get_case_insensitive(section, key)
            parsed = cls._to_float(value)
            if parsed is not None:
                return parsed

        ratio = cls._to_float(cls._get_case_insensitive(section, "ratio"))
        if ratio is None:
            ratio = cls._to_float(cls._get_case_insensitive(section, "usage_ratio"))
        if ratio is not None:
            return ratio * 100 if 0 <= ratio <= 1 else ratio

        used = cls._first_number(section, ("used", "consumed", "current", "value"))
        limit = cls._first_number(section, ("limit", "maximum", "max", "total", "quota"))
        if used is not None and limit and limit > 0:
            return (used / limit) * 100

        remaining = cls._first_number(section, ("remaining", "available"))
        if remaining is not None and limit and limit > 0:
            return max(0.0, ((limit - remaining) / limit) * 100)
        return None

    @classmethod
    def _section_reset(cls, section: dict[str, Any]) -> str | None:
        for key in (
            "resets_at",
            "reset_at",
            "resetsAt",
            "resetAt",
            "reset_time",
            "resetTime",
            "reset",
        ):
            value = cls._get_case_insensitive(section, key)
            reset = cls._normalize_reset(value)
            if reset:
                return reset

        for key in ("resets", "window"):
            value = cls._get_case_insensitive(section, key)
            if isinstance(value, dict):
                for nested_key in ("at", "time", "resets_at", "reset_at"):
                    reset = cls._normalize_reset(cls._get_case_insensitive(value, nested_key))
                    if reset:
                        return reset
        return None

    @staticmethod
    def _normalize_reset(value: Any) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, int | float):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            try:
                return datetime.fromtimestamp(timestamp, UTC).isoformat()
            except (OSError, OverflowError, ValueError):
                return str(value)
        return str(value)

    @classmethod
    def _section_label_matches(cls, value: Any, key: str) -> bool:
        if not isinstance(value, dict):
            return False
        for label_key in ("key", "name", "type", "window", "window_name", "bucket", "limit"):
            label = cls._get_case_insensitive(value, label_key)
            if label is not None and cls._matches_limit_key(label, key):
                return True
        return False

    @classmethod
    def _is_usage_container_key(cls, key: Any) -> bool:
        normalized = cls._normalize_key(key)
        return normalized in {
            "ratelimit",
            "ratelimits",
            "limits",
            "usage",
            "quota",
            "quotas",
            "usagequota",
            "usagelimits",
        }

    @classmethod
    def _matches_limit_key(cls, value: Any, key: str) -> bool:
        normalized = cls._normalize_key(value)
        if key == "five_hour":
            aliases = {"fivehour", "5hour", "5h", "fiveh"}
            return normalized in aliases or "fivehour" in normalized or "5hour" in normalized
        aliases = {"sevenday", "7day", "7d", "weekly", "week"}
        return (
            normalized in aliases
            or "sevenday" in normalized
            or "7day" in normalized
            or "weekly" in normalized
        )

    @staticmethod
    def _normalize_key(value: Any) -> str:
        return "".join(char for char in str(value).lower() if char.isalnum())

    @staticmethod
    def _get_case_insensitive(section: dict[str, Any], key: str) -> Any:
        if key in section:
            return section[key]
        normalized = key.lower()
        for section_key, value in section.items():
            if str(section_key).lower() == normalized:
                return value
        return None

    @classmethod
    def _first_number(cls, section: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            parsed = cls._to_float(cls._get_case_insensitive(section, key))
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
