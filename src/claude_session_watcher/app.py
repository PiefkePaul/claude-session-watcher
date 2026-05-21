from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .browser import CamoufoxManager
from .formatting import build_ui_watcher
from .models import Watcher
from .settings import Settings
from .store import Store
from .watcher import WatcherService


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "account"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    store = Store(settings.db_path)
    browser = CamoufoxManager(
        headless=settings.camoufox_headless,
        os_name=settings.camoufox_os,
    )
    service = WatcherService(store, browser, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.store = store
        app.state.browser = browser
        app.state.service = service
        service.start()
        yield
        await service.stop()
        await browser.close()

    app = FastAPI(title="Claude Session Watcher", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(settings_path("templates")))
    app.mount("/static", StaticFiles(directory=str(settings_path("static"))), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        accounts = store.list_accounts()
        watchers = store.list_watchers()
        events = store.list_events(limit=50)
        ui_watchers = [build_ui_watcher(watcher) for watcher in watchers]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "accounts": accounts,
                "watchers": ui_watchers,
                "events": events,
                "data_dir": settings.data_dir,
            },
        )

    @app.post("/accounts")
    async def create_account(name: str = Form(...)):
        profile_dir = settings.profiles_dir / _slug(name)
        store.create_account(name=name.strip(), profile_dir=str(profile_dir))
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/login")
    async def open_login(account_id: int):
        account = store.get_account(account_id)
        try:
            await browser.open_login(Path(account.profile_dir))
            store.update_account_status(account_id, "login-opened")
        except Exception as exc:  # noqa: BLE001
            store.update_account_status(account_id, "error", str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return RedirectResponse("/", status_code=303)

    @app.post("/watchers")
    async def create_watcher(
        name: str = Form(...),
        account_id: int = Form(...),
        remote_url: str = Form(...),
        five_hour_threshold: float = Form(95.0),
        seven_day_threshold: float = Form(98.0),
        check_interval_seconds: int = Form(60),
        pause_message: str = Form(
            "Pause after the current safe checkpoint. Do not start new work. "
            "Wait until I send continue."
        ),
        continue_message: str = Form("continue"),
    ):
        watcher = Watcher(
            id=None,
            name=name.strip(),
            account_id=account_id,
            remote_url=remote_url.strip(),
            five_hour_threshold=five_hour_threshold,
            seven_day_threshold=seven_day_threshold,
            check_interval_seconds=check_interval_seconds,
            pause_message=pause_message,
            continue_message=continue_message,
        )
        store.create_watcher(watcher)
        return RedirectResponse("/", status_code=303)

    @app.get("/watchers/{watcher_id}/edit", response_class=HTMLResponse)
    async def edit_watcher(request: Request, watcher_id: int):
        watcher = store.get_watcher(watcher_id)
        return templates.TemplateResponse(
            request,
            "watcher_edit.html",
            {
                "accounts": store.list_accounts(),
                "watcher": watcher,
            },
        )

    @app.post("/watchers/{watcher_id}")
    async def update_watcher(
        watcher_id: int,
        name: str = Form(...),
        account_id: int = Form(...),
        remote_url: str = Form(...),
        enabled: bool = Form(False),
        five_hour_threshold: float = Form(95.0),
        seven_day_threshold: float = Form(98.0),
        check_interval_seconds: int = Form(60),
        pause_message: str = Form(...),
        continue_message: str = Form("continue"),
    ):
        existing = store.get_watcher(watcher_id)
        updated = Watcher(
            id=watcher_id,
            name=name.strip(),
            account_id=account_id,
            remote_url=remote_url.strip(),
            enabled=enabled,
            state=existing.state,
            five_hour_threshold=five_hour_threshold,
            seven_day_threshold=seven_day_threshold,
            resume_threshold=existing.resume_threshold,
            check_interval_seconds=check_interval_seconds,
            pause_message=pause_message,
            continue_message=continue_message,
        )
        store.update_watcher_config(watcher_id, updated)
        service.reschedule_now(watcher_id)
        store.add_event(watcher_id, "info", "Watcher configuration updated")
        return RedirectResponse("/", status_code=303)

    @app.post("/watchers/{watcher_id}/enable")
    async def enable_watcher(watcher_id: int):
        store.set_watcher_enabled(watcher_id, True)
        service.reschedule_now(watcher_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/watchers/{watcher_id}/disable")
    async def disable_watcher(watcher_id: int):
        store.set_watcher_enabled(watcher_id, False)
        service.reschedule_now(watcher_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/watchers/{watcher_id}/check")
    async def check_watcher(watcher_id: int):
        try:
            result = await service.check_now(watcher_id)
            store.add_event(watcher_id, "info", f"Manual check: {result}")
        except Exception as exc:  # noqa: BLE001
            store.add_event(watcher_id, "error", f"Manual check failed: {exc}")
        return RedirectResponse("/", status_code=303)

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


def settings_path(kind: str):
    from importlib.resources import files

    return files("claude_session_watcher").joinpath(kind)


app = create_app()
