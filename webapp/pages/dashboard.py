"""
Dashboard page for pd_reloaded.
Shows overview stats, automation controls, and recent activity.
"""

import asyncio
import logging
import requests
from nicegui import ui, Client

from webapp.components import stat_card, content_card, page_header, empty_state
from webapp.theme import COLORS
from webapp.automation import AutomationEngine

logger = logging.getLogger(__name__)


def _enrich_content_items(db, items):
    """Enrich content items that lack poster/year by fetching from cinemeta."""
    for item in items:
        if item.get("poster_url") or not item.get("imdb_id"):
            continue
        imdb_id = item["imdb_id"]
        media_type = item.get("media_type", "movie")
        try:
            if media_type in ("show", "anime_show"):
                url = f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json"
            else:
                url = f"https://cinemeta-live.strem.io/meta/movie/{imdb_id}.json"
            resp = requests.get(url, timeout=5)
            if resp.ok:
                meta = resp.json().get("meta", {})
                if meta:
                    poster = meta.get("poster")
                    genres = meta.get("genres", [])
                    release_info = meta.get("releaseInfo", "")
                    year = None
                    try:
                        year = int(release_info[:4]) if release_info else None
                    except (ValueError, TypeError):
                        pass
                    # Update in-memory item and persist to DB
                    if poster:
                        item["poster_url"] = poster
                    if year and not item.get("year"):
                        item["year"] = year
                    if genres and not item.get("genres"):
                        item["genres"] = genres
                    # Update title from cinemeta if the current title looks like an ID
                    name = meta.get("name")
                    if name:
                        current_title = item.get("title", "")
                        if not current_title or current_title.startswith("tt") or current_title == "Unknown":
                            item["title"] = name
                    # Check anime via animetitles XML (title matching)
                    from webapp.anime_check import is_anime as check_anime
                    check_name = name or item.get("title", "")
                    if check_name and check_anime(check_name):
                        item["is_anime"] = True
                    db.upsert_content_item(
                        imdb_id=imdb_id,
                        title=name if name else None,
                        poster_url=poster,
                        year=year if year else item.get("year"),
                        genres=genres if genres else [],
                    )
        except Exception:
            logger.debug(f"Failed to enrich content item {imdb_id}")


async def render(app_state, client: Client):
    engine = AutomationEngine(app_state)

    with ui.column().classes("p-6 gap-6 w-full"):
        page_header("Dashboard", "Overview of your pd_reloaded instance")

        # ─── Automation Controls ─────────────────────────────
        with ui.card().classes("w-full p-4"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-3"):
                    status_icon = ui.icon("circle")
                    status_text = ui.label()
                    if app_state.automation_running:
                        status_icon.props("color=green")
                        status_text.text = "Automation is running"
                        status_text.classes("text-sm font-medium").style(f"color: {COLORS['success']}")
                    else:
                        status_icon.props("color=red")
                        status_text.text = "Automation is stopped"
                        status_text.classes("text-sm font-medium").style(f"color: {COLORS['error']}")

                with ui.row().classes("gap-2"):
                    async def start_automation():
                        engine.start()
                        status_icon.props("color=green")
                        status_text.text = "Automation is running"
                        status_text.style(f"color: {COLORS['success']}")
                        ui.notify("Automation started", type="positive")
                        refresh_stats()

                    async def stop_automation():
                        engine.stop()
                        status_icon.props("color=red")
                        status_text.text = "Automation is stopped"
                        status_text.style(f"color: {COLORS['error']}")
                        ui.notify("Automation stopped", type="warning")
                        refresh_stats()

                    ui.button("Start", on_click=start_automation, icon="play_arrow").props("color=green push").classes("px-4")
                    ui.button("Stop", on_click=stop_automation, icon="stop").props("color=red push").classes("px-4")

        # ─── Stats Cards ─────────────────────────────────────
        stats = app_state.get_stats()
        stats_grid = ui.element("div").classes("w-full").style(
            "display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px;"
        )

        def refresh_stats():
            nonlocal stats
            stats = app_state.get_stats()
            stats_grid.clear()
            with stats_grid:
                stat_card("movie", "Movies", stats.get("movies", 0), COLORS["info"])
                stat_card("tv", "Shows", stats.get("shows", 0), COLORS["success"])
                stat_card("animation", "Anime", stats.get("anime", 0), "#7C3AED")
                stat_card("download", "Collected", stats.get("collected", 0), COLORS["primary"])
                stat_card("hourglass_top", "Watchlisted", stats.get("watchlisted", 0), COLORS["warning"])
                stat_card("block", "Ignored", stats.get("ignored_count", 0), COLORS["error"])

        with stats_grid:
            stat_card("movie", "Movies", stats.get("movies", 0), COLORS["info"])
            stat_card("tv", "Shows", stats.get("shows", 0), COLORS["success"])
            stat_card("animation", "Anime", stats.get("anime", 0), "#7C3AED")
            stat_card("download", "Collected", stats.get("collected", 0), COLORS["primary"])
            stat_card("hourglass_top", "Watchlisted", stats.get("watchlisted", 0), COLORS["warning"])
            stat_card("block", "Ignored", stats.get("ignored_count", 0), COLORS["error"])

        # ─── Recent Downloads ────────────────────────────────
        with ui.card().classes("w-full p-4"):
            with ui.row().classes("items-center justify-between mb-3"):
                ui.label("Recent Downloads").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
                ui.button("View All", on_click=lambda: ui.navigate.to("/logs"), icon="arrow_forward").props(
                    "flat color=amber")

            logs = app_state.db.get_download_logs(limit=10)
            if logs:
                columns = [
                    {"name": "title", "label": "Title", "field": "title", "align": "left", "sortable": True},
                    {"name": "media_type", "label": "Type", "field": "media_type", "align": "left"},
                    {"name": "imdb_id", "label": "IMDB", "field": "imdb_id", "align": "left"},
                    {"name": "tmdb_id", "label": "TMDB", "field": "tmdb_id", "align": "left"},
                    {"name": "debrid_service", "label": "Service", "field": "debrid_service", "align": "left"},
                    {"name": "resolution", "label": "Quality", "field": "resolution", "align": "left"},
                    {"name": "size_gb", "label": "Size (GB)", "field": "size_gb", "align": "right"},
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                    {"name": "created_at", "label": "Date", "field": "created_at", "align": "left"},
                ]
                ui.table(
                    columns=columns,
                    rows=logs,
                    row_key="id",
                ).classes("w-full").props("dark flat dense")
            else:
                empty_state("download", "No downloads yet. Start the automation or use the scraper.")

        # ─── Recent Content ──────────────────────────────────
        with ui.card().classes("w-full p-4"):
            with ui.row().classes("items-center justify-between mb-3"):
                ui.label("Monitored Content").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
                ui.button("View All", on_click=lambda: ui.navigate.to("/content"), icon="arrow_forward").props(
                    "flat color=amber")

            content_items = app_state.db.get_all_content()

            # Enrich items missing poster/year in the background (first load)
            needs_enrich = [i for i in content_items if not i.get("poster_url") and i.get("imdb_id")]
            if needs_enrich:
                content_container = ui.column().classes("w-full")
                with content_container:
                    with ui.row().classes("p-4 items-center gap-2"):
                        ui.spinner("dots", size="md", color="amber")
                        ui.label("Loading metadata...").classes("text-sm").style(f"color: {COLORS['text_muted']}")

                async def enrich_and_render():
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: _enrich_content_items(app_state.db, content_items)
                    )
                    content_container.clear()
                    with content_container:
                        if content_items:
                            with ui.row().classes("gap-4 flex-wrap overflow-x-auto"):
                                for item in content_items[:12]:
                                    content_card(item)
                        else:
                            empty_state("movie", "No content items yet.")

                asyncio.ensure_future(enrich_and_render())
            elif content_items:
                with ui.row().classes("gap-4 flex-wrap overflow-x-auto"):
                    for item in content_items[:12]:
                        content_card(item)
            else:
                empty_state("movie", "No content items yet. Add media to your watchlists to get started.")

        # ─── Live Log ────────────────────────────────────────
        with ui.card().classes("w-full p-4"):
            with ui.row().classes("items-center justify-between mb-3"):
                ui.label("Live Log").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
                ui.button("Clear", on_click=lambda: (app_state.clear_logs(), log_area.clear()), icon="clear_all").props(
                    "flat color=grey size=sm")

            log_area = ui.column().classes("p-3 gap-0 max-h-64 overflow-y-auto w-full").style(
                f"background: {COLORS['background']}; border-radius: 8px; font-family: 'JetBrains Mono', monospace"
            )
            with log_area:
                for entry in app_state.automation_logs[-20:]:
                    ui.label(entry).classes("log-entry text-xs").style(f"color: {COLORS['text_muted']}")
                if not app_state.automation_logs:
                    ui.label("No log entries yet. Start the automation to see activity.").classes("text-xs").style(
                        f"color: {COLORS['text_muted']}")
