"""
Main NiceGUI web application for pd_reloaded.

Uses a single-page-application shell: header and sidebar are rendered once,
only the content area is swapped when navigating between pages.
"""

import os
import sys
import logging
from contextlib import nullcontext
from nicegui import ui, app, Client

# ── Patch NiceGUI Timer to gracefully handle deleted parent slots ───
# NiceGUI's Timer._get_context() raises RuntimeError when the parent
# element has been deleted (e.g. after SPA navigation or disconnect).
# The error fires from NiceGUI internals *before* our callback runs,
# so wrapping callbacks doesn't help.  Instead we patch _get_context
# to silently deactivate the timer and return a no-op context.
from nicegui.elements.timer import Timer as _NiceGuiTimer

_original_get_context = _NiceGuiTimer._get_context

def _safe_get_context(self):
    try:
        return _original_get_context(self)
    except RuntimeError:
        self.active = False
        return nullcontext()

_NiceGuiTimer._get_context = _safe_get_context
# ────────────────────────────────────────────────────────────────────

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.manager import DatabaseManager
from webapp.theme import apply_theme
from webapp.state import AppState

logger = logging.getLogger(__name__)

# Global app state
app_state = AppState()

# ── Page registry ───────────────────────────────────────────────────
_PAGE_MODULES = {}  # populated lazily in create_app


def _get_page_modules():
    """Return {name: module} mapping, importing once."""
    if not _PAGE_MODULES:
        from webapp.pages import (
            dashboard, content_page, activity_page,
            scraper_page, settings_page, logs_page,
        )
        _PAGE_MODULES.update({
            "dashboard": dashboard,
            "content": content_page,
            "activity": activity_page,
            "results": scraper_page,
            "scraper": scraper_page,
            "settings": settings_page,
            "logs": logs_page,
        })
    return _PAGE_MODULES


def create_app(config_dir="."):
    """Initialize and configure the NiceGUI application."""
    app_state.config_dir = config_dir
    app_state.db = DatabaseManager(config_dir)
    app_state.db.initialize(config_dir)

    # Check if we need onboarding
    needs_onboarding = not app_state.db.is_setup_complete()

    # Note whether legacy settings exist — onboarding will offer import
    app_state.has_legacy_settings = app_state.db.has_legacy_settings()

    app_state.needs_onboarding = needs_onboarding

    # Pre-load animetitles cache in background thread so first check is instant
    from threading import Thread as _Thr
    from webapp.anime_check import preload_cache as _preload_anime
    _Thr(target=_preload_anime, daemon=True).start()

    # Pre-load cinemeta feed cache so search suggestions are instant
    from webapp.cinemeta_feed import preload_feed as _preload_feed
    _Thr(target=_preload_feed, daemon=True).start()

    # ── Onboarding (standalone — no SPA shell) ──────────────────────
    from webapp.pages import onboarding

    @ui.page("/onboarding")
    async def onboarding_route(client: Client):
        await onboarding.render(app_state, client)

    # ── API endpoints (must be registered BEFORE the SPA catch-all) ─
    @app.get("/api/plex/callback")
    async def plex_callback():
        from starlette.responses import HTMLResponse
        return HTMLResponse(
            "<html><body style='background:#0F1117;color:#E5E7EB;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            "<div style='text-align:center'>"
            "<p style='font-size:1.4em;color:#E5A00D'>&#10003; Plex Authentication Successful</p>"
            "<p>This window will close automatically.</p>"
            "<script>setTimeout(function(){window.close();},1500);</script>"
            "</div></body></html>"
        )

    @app.get("/api/health")
    async def health():
        return {"status": "healthy", "onboarding_required": app_state.needs_onboarding}

    # ── SPA shell for all other pages ───────────────────────────────
    @ui.page("/")
    @ui.page("/{_path:path}")
    async def spa_route(client: Client, _path: str = ""):
        if app_state.needs_onboarding:
            return ui.navigate.to("/onboarding")

        page = _path.strip("/") or "dashboard"
        pages = _get_page_modules()
        if page not in pages:
            page = "dashboard"

        from webapp.components import create_spa_shell

        def _deactivate_child_timers(element):
            """Recursively deactivate Timer elements to prevent 'parent slot deleted' errors."""
            from nicegui.elements.timer import Timer as _Timer
            for slot in element.slots.values():
                for child in slot.children:
                    if isinstance(child, _Timer):
                        child.active = False
                    _deactivate_child_timers(child)

        async def navigate(page_name: str):
            """Swap only the content area — sidebar stays put."""
            _deactivate_child_timers(content_area)
            content_area.clear()
            # Support "results:query" for search navigation
            search_query = ""
            if page_name.startswith("results:"):
                search_query = page_name.split(":", 1)[1]
                page_name = "results"
            mod = pages.get(page_name)
            if mod:
                with content_area:
                    if page_name == "results" and search_query:
                        await mod.render(app_state, client, search_query=search_query)
                    else:
                        await mod.render(app_state, client)
            url_path = page_name
            if page_name == "results" and search_query:
                from urllib.parse import quote
                url_path = f"results?q={quote(search_query)}"
            ui.run_javascript(
                f"window.history.pushState(null, '', '/{url_path}')")

        content_area = create_spa_shell(app_state, page, navigate, client)

        # Render initial page
        with content_area:
            if page == "results":
                # Extract query from ?q= parameter if present
                from starlette.requests import Request as _Req
                raw_q = client.request.query_params.get("q", "") if hasattr(client, "request") else ""
                await pages[page].render(app_state, client, search_query=raw_q)
            else:
                await pages[page].render(app_state, client)

    return app


def run_app(config_dir=".", host="0.0.0.0", port=8008):
    """Run the NiceGUI application."""
    ui.run(
        title="pd_reloaded",
        host=host,
        port=port,
        reload=False,
        show=False,
        favicon="🎬",
        dark=True,
    )
