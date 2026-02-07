"""
Main NiceGUI web application for pd_reloaded.
"""

import os
import sys
import logging
import asyncio
from nicegui import ui, app, Client

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.manager import DatabaseManager
from webapp.theme import apply_theme
from webapp.state import AppState

logger = logging.getLogger(__name__)

# Global app state
app_state = AppState()


def create_app(config_dir="."):
    """Initialize and configure the NiceGUI application."""
    app_state.config_dir = config_dir
    app_state.db = DatabaseManager(config_dir)
    app_state.db.initialize(config_dir)

    # Check if we need onboarding
    needs_onboarding = not app_state.db.is_setup_complete()

    # Try to migrate legacy settings if they exist and DB not set up
    if needs_onboarding and app_state.db.has_legacy_settings():
        logger.info("Found legacy settings.json, migrating to database...")
        app_state.db.migrate_from_json()
        needs_onboarding = not app_state.db.is_setup_complete()

    app_state.needs_onboarding = needs_onboarding

    # Register pages
    from webapp.pages import onboarding, dashboard, settings_page, scraper_page, content_page, logs_page

    @ui.page("/")
    async def index(client: Client):
        if app_state.needs_onboarding:
            return ui.navigate.to("/onboarding")
        return ui.navigate.to("/dashboard")

    @ui.page("/onboarding")
    async def onboarding_route(client: Client):
        await onboarding.render(app_state, client)

    @ui.page("/dashboard")
    async def dashboard_route(client: Client):
        if app_state.needs_onboarding:
            return ui.navigate.to("/onboarding")
        await dashboard.render(app_state, client)

    @ui.page("/content")
    async def content_route(client: Client):
        if app_state.needs_onboarding:
            return ui.navigate.to("/onboarding")
        await content_page.render(app_state, client)

    @ui.page("/scraper")
    async def scraper_route(client: Client):
        if app_state.needs_onboarding:
            return ui.navigate.to("/onboarding")
        await scraper_page.render(app_state, client)

    @ui.page("/settings")
    async def settings_route(client: Client):
        if app_state.needs_onboarding:
            return ui.navigate.to("/onboarding")
        await settings_page.render(app_state, client)

    @ui.page("/logs")
    async def logs_route(client: Client):
        if app_state.needs_onboarding:
            return ui.navigate.to("/onboarding")
        await logs_page.render(app_state, client)

    # API endpoints for Plex OAuth callback
    @app.get("/api/plex/callback")
    async def plex_callback():
        from starlette.responses import HTMLResponse
        return HTMLResponse(
            "<html><body style='background:#0F1117;color:#E5E7EB;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            "<div style='text-align:center'>"
            "<p style='font-size:1.4em;color:#E5A00D'>&#10003; Plex Authentication Successful</p>"
            "<p>You can close this window.</p>"
            "<script>setTimeout(function(){window.close();},1500);</script>"
            "</div></body></html>"
        )

    @app.get("/api/health")
    async def health():
        return {"status": "healthy", "onboarding_required": app_state.needs_onboarding}

    return app


def run_app(config_dir=".", host="0.0.0.0", port=8008):
    """Run the NiceGUI application."""
    ui.run(
        title="pd_reloaded",
        host=host,
        port=port,
        reload=False,
        show=True,
        favicon="🎬",
        dark=True,
    )
