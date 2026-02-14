"""
Main NiceGUI web application for plex_debrid.

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
            scraper_page, settings_page, logs_page, series_page, movies_page,
        )
        _PAGE_MODULES.update({
            "dashboard": dashboard,
            "content": content_page,
            "activity": activity_page,
            "results": scraper_page,
            "scraper": scraper_page,
            "settings": settings_page,
            "logs": logs_page,
            "series": series_page,
            "movies": movies_page,
        })
    return _PAGE_MODULES


def create_app(config_dir="."):
    """Initialize and configure the NiceGUI application."""
    app_state.config_dir = config_dir
    app_state.db = DatabaseManager(config_dir)
    app_state.db.initialize(config_dir)
    try:
        removed = app_state.db.deduplicate_content_items()
        if removed:
            logger.info("Cleaned up %d duplicate content rows on startup", removed)
    except Exception as e:
        logger.debug("Startup content dedup cleanup failed: %s", e)

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

    # ── Debug / logs page (standalone, not inside SPA shell) ────────
    @ui.page("/debug/logs")
    async def debug_logs_page(client: Client):
        from webapp.log_config import get_log_lines
        from webapp.theme import apply_theme, COLORS
        apply_theme()

        ui.add_head_html("""
        <style>
          .log-line { font-family: 'JetBrains Mono', 'Fira Code', monospace;
                      font-size: 12px; line-height: 1.5; white-space: pre-wrap;
                      word-break: break-all; }
        </style>
        """)

        with ui.column().classes("p-4 gap-2 w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label("Application Logs").classes("text-xl font-bold").style(
                    f"color: {COLORS['primary']}")
                with ui.row().classes("gap-2"):
                    lines_select = ui.select(
                        {200: "200 lines", 500: "500 lines", 1000: "1 000 lines", 2000: "All"},
                        value=500, label="Lines",
                    ).classes("w-36").props("outlined dense dark color=amber")
                    ui.button("Back", icon="arrow_back",
                              on_click=lambda: ui.navigate.to("/logs")).props("flat color=grey")

            log_area = ui.column().classes(
                "w-full gap-0 p-3 overflow-y-auto"
            ).style(
                f"background: {COLORS['background']}; border: 1px solid rgba(255,255,255,0.08); "
                "max-height: 80vh; border-radius: 4px"
            )

            def _refresh():
                log_area.clear()
                lines = get_log_lines(last_n=lines_select.value)
                with log_area:
                    if not lines:
                        ui.label("No log entries yet.").classes("text-sm").style(
                            f"color: {COLORS['text_muted']}")
                    else:
                        for line in lines:
                            ui.label(line).classes("log-line").style(
                                f"color: {COLORS['text_muted']}")
                # Auto-scroll to bottom
                ui.run_javascript(
                    f"document.getElementById('c{log_area.id}')?.scrollTo(0, 999999)"
                )

            _refresh()
            # Auto-refresh every 3 seconds
            ui.timer(3.0, _refresh)

            lines_select.on("update:model-value", lambda _: _refresh())

    # ── SPA shell for all other pages ───────────────────────────────
    @ui.page("/")
    @ui.page("/{_path:path}")
    async def spa_route(client: Client, _path: str = ""):
        if app_state.needs_onboarding:
            return ui.navigate.to("/onboarding")

        page = _path.strip("/") or "dashboard"
        series_imdb = ""
        movie_imdb = ""
        shell_page = page
        if page.startswith("series/"):
            series_imdb = page.split("/", 1)[1]
            shell_page = "series"
            page = "series"
        elif page.startswith("movies/"):
            movie_imdb = page.split("/", 1)[1]
            shell_page = "movies"
            page = "movies"

        pages = _get_page_modules()
        if page not in pages:
            page = "dashboard"
            shell_page = "dashboard"

        from webapp.components import create_spa_shell

        def _deactivate_child_timers(element):
            from nicegui.elements.timer import Timer as _Timer
            for slot in element.slots.values():
                for child in slot.children:
                    if isinstance(child, _Timer):
                        child.active = False
                    _deactivate_child_timers(child)

        async def navigate(page_name: str):
            _deactivate_child_timers(content_area)
            content_area.clear()
            search_query = ""
            series_target = ""
            movie_target = ""
            nav_shell_page = page_name
            if page_name.startswith("results:"):
                search_query = page_name.split(":", 1)[1]
                page_name = "results"
                nav_shell_page = "dashboard"
            elif page_name.startswith("series/"):
                series_target = page_name.split("/", 1)[1]
                page_name = "series"
                nav_shell_page = "series"
            elif page_name.startswith("movies/"):
                movie_target = page_name.split("/", 1)[1]
                page_name = "movies"
                nav_shell_page = "movies"
            mod = pages.get(page_name)
            if mod:
                with content_area:
                    if page_name == "results" and search_query:
                        await mod.render(app_state, client, search_query=search_query)
                    elif page_name == "series" and series_target:
                        await mod.render(app_state, client, imdb_id=series_target)
                    elif page_name == "movies" and movie_target:
                        await mod.render(app_state, client, imdb_id=movie_target)
                    else:
                        await mod.render(app_state, client)
            url_path = page_name
            if page_name == "results" and search_query:
                from urllib.parse import quote
                url_path = f"results?q={quote(search_query)}"
            elif page_name == "series" and series_target:
                url_path = f"series/{series_target}"
            elif page_name == "movies" and movie_target:
                url_path = f"movies/{movie_target}"
            ui.run_javascript(
                f"window.history.pushState(null, '', '/{url_path}')")

        content_area = create_spa_shell(app_state, shell_page, navigate, client)

        with content_area:
            if page == "results":
                from starlette.requests import Request as _Req
                raw_q = client.request.query_params.get("q", "") if hasattr(client, "request") else ""
                await pages[page].render(app_state, client, search_query=raw_q)
            elif page == "series":
                await pages[page].render(app_state, client, imdb_id=series_imdb)
            elif page == "movies":
                await pages[page].render(app_state, client, imdb_id=movie_imdb)
            else:
                await pages[page].render(app_state, client)

    return app


def run_app(config_dir=".", host="0.0.0.0", port=8008):
    """Run the NiceGUI application."""
    ui.run(
        title="plex_debrid",
        host=host,
        port=port,
        reload=False,
        show=False,
        favicon="🎬",
        dark=True,
    )
