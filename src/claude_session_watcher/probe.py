from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .oauth_usage import ClaudeOAuthUsageClient, load_oauth_access_token
from .profile_cookies import load_claude_cookies
from .session_list import ClaudeWebSessionsClient, SessionListAuthError
from .usage import ClaudeUsageClient, UsageAuthError


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    details: dict[str, Any]


async def probe_account(
    profile_dir: Path,
    *,
    session_id: str | None = None,
    send_message: str | None = None,
    include_oauth: bool = True,
    oauth_credentials_path: Path | None = None,
) -> dict[str, ProbeResult]:
    """Run lightweight HTTP probes using claude.ai cookies (no browser automation).

    This is intentionally conservative: GET-only checks. Sending messages is handled
    elsewhere to avoid unintended side effects on user sessions.
    """
    cookies = load_claude_cookies(profile_dir)
    results: dict[str, ProbeResult] = {}

    # Probe usage endpoints (org + usage).
    try:
        usage_client = ClaudeUsageClient(cookies=cookies)
        snapshot = await usage_client.fetch()
        details: dict[str, Any] = {
            "five_hour": (
                None
                if snapshot.five_hour is None
                else {
                    "utilization": snapshot.five_hour.utilization,
                    "resets_at": snapshot.five_hour.resets_at,
                }
            ),
            "seven_day": (
                None
                if snapshot.seven_day is None
                else {
                    "utilization": snapshot.seven_day.utilization,
                    "resets_at": snapshot.seven_day.resets_at,
                }
            ),
            "org_id": snapshot.raw.get("_csw_org_id"),
            "org_name": snapshot.raw.get("_csw_org_name"),
        }
        results["usage"] = ProbeResult(ok=True, details=details)
    except (UsageAuthError, Exception) as exc:  # noqa: BLE001
        results["usage"] = ProbeResult(ok=False, details={"error": str(exc)})

    # Probe session listing.
    raw_sessions: list[dict[str, Any]] = []
    try:
        sessions_client = ClaudeWebSessionsClient(cookies=cookies)
        raw_sessions = await sessions_client.list_all()
        results["sessions"] = ProbeResult(
            ok=True,
            details={
                "count": len(raw_sessions),
                "sample": [
                    {
                        "id": str(item.get("id") or ""),
                        "title": str(item.get("title") or ""),
                        "session_status": str(item.get("session_status") or ""),
                        "connection_status": str(item.get("connection_status") or ""),
                        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
                    }
                    for item in raw_sessions[:5]
                    if isinstance(item, dict)
                ],
            },
        )
    except (SessionListAuthError, Exception) as exc:  # noqa: BLE001
        results["sessions"] = ProbeResult(ok=False, details={"error": str(exc)})

    # Probe events listing for one likely controllable session (or explicit session_id).
    try:
        sessions_client = ClaudeWebSessionsClient(cookies=cookies)
        target: dict[str, Any] | None = None
        explicit_id = (session_id or "").strip() or None
        if explicit_id:
            for item in raw_sessions:
                if isinstance(item, dict) and str(item.get("id") or "") == explicit_id:
                    target = item
                    break
            if target is None:
                target = {"id": explicit_id, "title": explicit_id}
        else:
            for item in raw_sessions:
                if not isinstance(item, dict):
                    continue
                tags = item.get("tags")
                is_remote = isinstance(tags, list) and any(
                    str(tag) == "remote-control-repl" for tag in tags
                )
                if not is_remote:
                    continue
                status = str(item.get("session_status") or "")
                if status == "archived":
                    continue
                target = item
                break
            if target is None and raw_sessions:
                # Fall back to the first session (even if archived) to at least test the endpoint.
                if isinstance(raw_sessions[0], dict):
                    target = raw_sessions[0]
        if not target:
            results["events"] = ProbeResult(
                ok=False,
                details={"error": "No sessions available to probe events."},
            )
        else:
            session_id = str(target.get("id") or "")
            events = await sessions_client.list_events(session_id, limit=1)
            keys: list[str] = []
            if isinstance(events, dict):
                keys = sorted(str(key) for key in events.keys())
            results["events"] = ProbeResult(
                ok=True,
                details={
                    "session_id": session_id,
                    "title": str(target.get("title") or ""),
                    "keys": keys,
                },
            )
    except (SessionListAuthError, Exception) as exc:  # noqa: BLE001
        results["events"] = ProbeResult(ok=False, details={"error": str(exc)})

    # Optional: send a test message (explicit opt-in only).
    if send_message:
        try:
            sessions_client = ClaudeWebSessionsClient(cookies=cookies)
            explicit_id = (session_id or "").strip()
            if not explicit_id:
                raise ValueError("send_message requires an explicit session_id")
            await sessions_client.send_user_message(explicit_id, send_message)
            results["send_message"] = ProbeResult(
                ok=True,
                details={"session_id": explicit_id},
            )
        except (SessionListAuthError, Exception) as exc:  # noqa: BLE001
            results["send_message"] = ProbeResult(ok=False, details={"error": str(exc)})

    # Optional: OAuth usage probe via local Claude Code credentials/token.
    if include_oauth:
        try:
            token, source_path = load_oauth_access_token(oauth_credentials_path)
            oauth_payload = await ClaudeOAuthUsageClient(token).fetch_raw()
            oauth_snapshot = ClaudeUsageClient._parse(oauth_payload)
            results["oauth_usage"] = ProbeResult(
                ok=True,
                details={
                    "source": str(source_path),
                    "five_hour": (
                        None
                        if oauth_snapshot.five_hour is None
                        else {
                            "utilization": oauth_snapshot.five_hour.utilization,
                            "resets_at": oauth_snapshot.five_hour.resets_at,
                        }
                    ),
                    "seven_day": (
                        None
                        if oauth_snapshot.seven_day is None
                        else {
                            "utilization": oauth_snapshot.seven_day.utilization,
                            "resets_at": oauth_snapshot.seven_day.resets_at,
                        }
                    ),
                    "keys": sorted(str(key) for key in oauth_payload.keys()),
                },
            )
        except Exception as exc:  # noqa: BLE001
            results["oauth_usage"] = ProbeResult(ok=False, details={"error": str(exc)})

    # Aggregate endpoint-level capability flags.
    results["capabilities"] = ProbeResult(
        ok=True,
        details={
            "usage_get": _capability_state(results, "usage"),
            "sessions_get": _capability_state(results, "sessions"),
            "events_get": _capability_state(results, "events"),
            "events_post": _capability_state(results, "send_message"),
            "oauth_usage_get": _capability_state(results, "oauth_usage"),
        },
    )

    return results


def _capability_state(results: dict[str, ProbeResult], key: str) -> dict[str, Any]:
    result = results.get(key)
    if result is None:
        return {"supported": None, "checked": False}
    return {
        "supported": bool(result.ok),
        "checked": True,
        "error": None if result.ok else result.details.get("error"),
    }
