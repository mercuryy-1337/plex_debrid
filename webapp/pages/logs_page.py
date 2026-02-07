"""
Download/activity logs page for pd_reloaded.
"""

import logging
from nicegui import ui, Client

from webapp.components import page_header, empty_state
from webapp.theme import COLORS

logger = logging.getLogger(__name__)


async def render(app_state, client: Client):

    with ui.column().classes("p-6 gap-6 w-full"):
        page_header("Download Logs", "View all download activity and content tracking")

        # ─── Filter Controls ────────────────────────────────
        filter_state = {"status": "all", "search": ""}

        with ui.card().classes("w-full p-4"):
            with ui.row().classes("items-center gap-4 w-full flex-wrap"):
                search_input = ui.input(
                    "Search logs...",
                    on_change=lambda e: update_filter("search", e.value),
                ).classes("flex-1 min-w-64").props("outlined dense dark color=amber clearable")

                status_select = ui.select(
                    {"all": "All Status", "completed": "Completed", "failed": "Failed", "pending": "Pending"},
                    value="all",
                    label="Status",
                    on_change=lambda e: update_filter("status", e.value),
                ).classes("min-w-36").props("outlined dense dark color=amber")

        # ─── Logs Table ─────────────────────────────────────
        logs_container = ui.column().classes("w-full")

        def load_logs():
            logs_container.clear()

            logs = app_state.db.get_download_logs(limit=200)

            # Apply filters
            if filter_state["search"]:
                query = filter_state["search"].lower()
                logs = [l for l in logs if query in l["title"].lower() or
                        query in (l.get("imdb_id") or "").lower() or
                        query in (l.get("tmdb_id") or "").lower() or
                        query in (l.get("release_title") or "").lower()]

            if filter_state["status"] != "all":
                logs = [l for l in logs if l["status"] == filter_state["status"]]

            with logs_container:
                if not logs:
                    empty_state("article", "No download logs found.")
                    return

                # Summary stats
                with ui.row().classes("gap-4 mb-4"):
                    completed = len([l for l in logs if l["status"] == "completed"])
                    failed = len([l for l in logs if l["status"] == "failed"])
                    
                    with ui.element("div").classes("px-4 py-2").style(
                        f"background: {COLORS['surface_light']}; border-radius: 8px"
                    ):
                        ui.label(f"Total: {len(logs)}").classes("text-sm font-semibold").style(
                            f"color: {COLORS['text']}")
                    
                    with ui.element("div").classes("px-4 py-2").style(
                        f"background: {COLORS['surface_light']}; border-radius: 8px"
                    ):
                        ui.label(f"Completed: {completed}").classes("text-sm font-semibold").style(
                            f"color: {COLORS['success']}")
                    
                    with ui.element("div").classes("px-4 py-2").style(
                        f"background: {COLORS['surface_light']}; border-radius: 8px"
                    ):
                        ui.label(f"Failed: {failed}").classes("text-sm font-semibold").style(
                            f"color: {COLORS['error']}")

                # Main table
                with ui.card().classes("w-full"):
                    columns = [
                        {"name": "title", "label": "Title", "field": "title", "align": "left", "sortable": True,
                         "style": "max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"},
                        {"name": "release_title", "label": "Release", "field": "release_title", "align": "left",
                         "style": "max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"},
                        {"name": "media_type", "label": "Type", "field": "media_type", "align": "left"},
                        {"name": "imdb_id", "label": "IMDB", "field": "imdb_id", "align": "left"},
                        {"name": "tmdb_id", "label": "TMDB", "field": "tmdb_id", "align": "left"},
                        {"name": "debrid_service", "label": "Service", "field": "debrid_service", "align": "left"},
                        {"name": "scraper_source", "label": "Source", "field": "scraper_source", "align": "left"},
                        {"name": "resolution", "label": "Quality", "field": "resolution", "align": "left"},
                        {"name": "size_gb", "label": "Size (GB)", "field": "size_gb", "align": "right"},
                        {"name": "cached", "label": "Cached", "field": "cached", "align": "center"},
                        {"name": "status", "label": "Status", "field": "status", "align": "left"},
                        {"name": "created_at", "label": "Date", "field": "created_at", "align": "left", "sortable": True},
                    ]

                    table = ui.table(
                        columns=columns,
                        rows=logs,
                        row_key="id",
                        pagination={"rowsPerPage": 50},
                    ).classes("w-full").props("dark flat dense")

                    # Custom rendering for status and cached cells
                    table.add_slot("body-cell-status", """
                        <q-td :props="props">
                            <q-badge :color="props.value === 'completed' ? 'green' : props.value === 'failed' ? 'red' : 'amber'">
                                {{ props.value }}
                            </q-badge>
                        </q-td>
                    """)

                    table.add_slot("body-cell-cached", """
                        <q-td :props="props">
                            <q-icon :name="props.value ? 'check_circle' : 'cancel'" :color="props.value ? 'green' : 'red'" />
                        </q-td>
                    """)

                    table.add_slot("body-cell-media_type", """
                        <q-td :props="props">
                            <q-badge :color="props.value === 'movie' || props.value === 'anime_movie' ? 'blue' : props.value === 'show' || props.value === 'anime_show' ? 'green' : 'grey'">
                                {{ props.value }}
                            </q-badge>
                        </q-td>
                    """)

        def update_filter(key, value):
            filter_state[key] = value
            load_logs()

        load_logs()

        # ─── Live Automation Log ────────────────────────────
        with ui.card().classes("w-full mt-4"):
            with ui.row().classes("items-center justify-between p-4"):
                ui.label("Live Automation Log").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
                ui.button("Clear", on_click=lambda: (app_state.clear_logs(), refresh_live_log()), icon="clear_all").props(
                    "flat color=grey size=sm")

            live_log_container = ui.column().classes("p-4 gap-0 max-h-80 overflow-y-auto w-full").style(
                f"background: {COLORS['background']}; border-radius: 8px; font-family: 'JetBrains Mono', monospace"
            )

            def refresh_live_log():
                live_log_container.clear()
                with live_log_container:
                    for entry in app_state.automation_logs[-50:]:
                        ui.label(entry).classes("log-entry text-xs").style(f"color: {COLORS['text_muted']}")
                    if not app_state.automation_logs:
                        ui.label("No log entries yet.").classes("text-xs").style(f"color: {COLORS['text_muted']}")

            refresh_live_log()

            # Auto-refresh timer
            ui.timer(5.0, refresh_live_log)
