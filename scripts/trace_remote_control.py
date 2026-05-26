from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from camoufox.async_api import AsyncCamoufox


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trace Claude remote control network traffic in Camoufox."
    )
    parser.add_argument("--profile-dir", required=True, help="Camoufox/Firefox profile directory")
    parser.add_argument("--remote-url", required=True, help="Claude remote control URL")
    parser.add_argument("--message", default=None, help="Optional message to send via editor")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--wait-ms", type=int, default=7000, help="Wait after action (ms)")
    parser.add_argument("--max-body", type=int, default=3000, help="Max chars per body payload")
    parser.add_argument("--out", default=None, help="Optional output JSON file path")
    return parser


def _keep_url(url: str) -> bool:
    interesting = (
        "/v1/sessions/",
        "/api/organizations",
        "/api/oauth/",
        "/v1/environments/",
        "/session_ingress/ws/",
        "/code/",
    )
    return any(part in url for part in interesting)


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            value = str(value)
    if not isinstance(value, str):
        value = str(value)
    return value[:limit]


async def _find_editor(page) -> Any:
    selectors = [
        "[data-testid*='prompt' i]",
        "[data-testid*='composer' i]",
        "textarea",
        "[role='textbox']",
        "[contenteditable='true']",
        ".ProseMirror[contenteditable='true']",
    ]
    deadline = asyncio.get_running_loop().time() + 40.0
    while asyncio.get_running_loop().time() < deadline:
        best = None
        best_score = None
        for frame in page.frames:
            for selector in selectors:
                try:
                    locator = frame.locator(selector)
                    count = await locator.count()
                except Exception:  # noqa: BLE001
                    continue
                if not count:
                    continue
                for idx in range(count):
                    candidate = locator.nth(idx)
                    try:
                        box = await candidate.bounding_box()
                    except Exception:  # noqa: BLE001
                        continue
                    if not box:
                        continue
                    if box.get("width", 0) < 120 or box.get("height", 0) < 18:
                        continue
                    try:
                        editable = await candidate.is_editable()
                    except Exception:  # noqa: BLE001
                        editable = True
                    if not editable:
                        continue
                    score = float(box.get("y", 0) + box.get("height", 0))
                    if best_score is None or score > best_score:
                        best_score = score
                        best = candidate
        if best is not None:
            try:
                await best.scroll_into_view_if_needed(timeout=2_000)
            except Exception:  # noqa: BLE001
                pass
            return best
        await page.wait_for_timeout(250)
    raise RuntimeError("Could not find Claude prompt editor")


async def trace_remote_control(
    *,
    profile_dir: Path,
    remote_url: str,
    message: str | None,
    headless: bool,
    wait_ms: int,
    max_body: int,
) -> dict[str, Any]:
    logs: list[dict[str, Any]] = []
    websockets: list[dict[str, Any]] = []

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(profile_dir),
        headless=headless,
        humanize=False,
    ) as context:
        def on_request(req) -> None:
            if not _keep_url(req.url):
                return
            post_data = None
            try:
                post_data = req.post_data
            except Exception:  # noqa: BLE001
                post_data = None
            logs.append(
                {
                    "kind": "request",
                    "method": req.method,
                    "resource_type": req.resource_type,
                    "url": req.url,
                    "post_data": _clip(post_data, max_body),
                }
            )

        async def on_response(resp) -> None:
            req = resp.request
            if not _keep_url(req.url):
                return
            body = None
            try:
                body = await resp.text()
            except Exception:  # noqa: BLE001
                body = None
            logs.append(
                {
                    "kind": "response",
                    "status": resp.status,
                    "method": req.method,
                    "url": req.url,
                    "body": _clip(body, max_body),
                }
            )

        def on_websocket(ws) -> None:
            if not _keep_url(ws.url):
                return
            entry: dict[str, Any] = {"url": ws.url, "events": []}
            websockets.append(entry)

            def on_sent(frame) -> None:
                payload = frame.get("payload") if isinstance(frame, dict) else frame
                entry["events"].append({"dir": "sent", "payload": _clip(payload, max_body)})

            def on_received(frame) -> None:
                payload = frame.get("payload") if isinstance(frame, dict) else frame
                entry["events"].append({"dir": "recv", "payload": _clip(payload, max_body)})

            ws.on("framesent", on_sent)
            ws.on("framereceived", on_received)

        context.on("request", on_request)
        context.on("response", lambda r: asyncio.create_task(on_response(r)))
        context.on("websocket", on_websocket)

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(remote_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2_000)

        if message:
            editor = await _find_editor(page)
            await editor.fill(message)
            await editor.press("Enter")

        await page.wait_for_timeout(wait_ms)

    return {
        "remote_url": remote_url,
        "message": message,
        "captured_at": datetime.now(UTC).isoformat(),
        "request_response_count": len(logs),
        "websocket_count": len(websockets),
        "logs": logs,
        "websockets": websockets,
    }


def main() -> int:
    args = _build_parser().parse_args()
    message = args.message
    if message == "__auto__":
        message = f"csw remote trace {datetime.now(UTC).isoformat()}"
    result = asyncio.run(
        trace_remote_control(
            profile_dir=Path(args.profile_dir),
            remote_url=args.remote_url,
            message=message,
            headless=bool(args.headless),
            wait_ms=args.wait_ms,
            max_body=args.max_body,
        )
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
