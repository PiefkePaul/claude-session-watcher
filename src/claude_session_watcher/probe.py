from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    return results
