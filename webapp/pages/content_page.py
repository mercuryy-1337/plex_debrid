"""
Content page for pd_reloaded.
View all content categorized into shows, movies, and anime.
"""

import logging
import requests
from nicegui import ui, Client

from webapp.components import content_card, page_header, empty_state
from webapp.theme import COLORS
from webapp.pages.dashboard import _enrich_content_items

logger = logging.getLogger(__name__)


async def render(app_state, client: Client):

    with ui.column().classes("p-6 gap-6 w-full"):
        page_header("Content Library", "Browse all monitored and collected content")

        # ─── Filter Controls ────────────────────────────────
        filter_state = {"type": "all", "status": "all", "search": ""}

        with ui.card().classes("w-full p-4"):
            with ui.row().classes("items-center gap-4 w-full flex-wrap"):
                search_input = ui.input(
                    "Search content...",
                    on_change=lambda e: update_filter("search", e.value),
                ).classes("flex-1 min-w-64").props("outlined dense dark color=amber clearable")

                type_select = ui.select(
                    {"all": "All Types", "movie": "Movies", "show": "Shows", "anime": "Anime"},
                    value="all",
                    label="Type",
                    on_change=lambda e: update_filter("type", e.value),
                ).classes("min-w-36").props("outlined dense dark color=amber")

                status_select = ui.select(
                    {"all": "All Status", "watchlisted": "Watchlisted", "downloading": "Downloading",
                     "collected": "Collected", "ignored": "Ignored"},
                    value="all",
                    label="Status",
                    on_change=lambda e: update_filter("status", e.value),
                ).classes("min-w-36").props("outlined dense dark color=amber")

        # ─── Content Tabs ──────────────────────────────────
        content_container = ui.column().classes("w-full gap-6")

        def load_content():
            content_container.clear()
            items = app_state.db.get_all_content(
                media_type=filter_state["type"] if filter_state["type"] != "all" else None,
                status=filter_state["status"] if filter_state["status"] != "all" else None,
            )

            # Apply search filter
            if filter_state["search"]:
                query = filter_state["search"].lower()
                items = [i for i in items if query in i["title"].lower() or
                         query in (i.get("imdb_id") or "").lower() or
                         query in (i.get("tmdb_id") or "").lower()]

            # Enrich items missing posters
            needs_enrich = [i for i in items if not i.get("poster_url") and i.get("imdb_id")]
            if needs_enrich:
                _enrich_content_items(app_state.db, items)

            with content_container:
                if not items:
                    empty_state("search_off", "No content found matching your filters.")
                    return

                # Group by type
                movies = [i for i in items if i["media_type"] == "movie"]
                shows = [i for i in items if i["media_type"] in ("show", "anime_show") and not i.get("is_anime")]
                anime_items = [i for i in items if i["media_type"] in ("show", "anime_show") and i.get("is_anime")]

                if filter_state["type"] == "all" or filter_state["type"] == "movie":
                    if movies:
                        _render_section("Movies", "movie", movies, len(movies))

                if filter_state["type"] == "all" or filter_state["type"] == "show":
                    if shows:
                        _render_section("Shows", "tv", shows, len(shows))

                if filter_state["type"] == "all" or filter_state["type"] == "anime":
                    if anime_items:
                        _render_section("Anime", "animation", anime_items, len(anime_items))

                # Summary table
                with ui.card().classes("w-full mt-4"):
                    with ui.row().classes("items-center justify-between p-4"):
                        ui.label(f"All Content ({len(items)} items)").classes("text-lg font-semibold").style(
                            f"color: {COLORS['text']}")

                    columns = [
                        {"name": "title", "label": "Title", "field": "title", "align": "left", "sortable": True},
                        {"name": "media_type", "label": "Type", "field": "media_type", "align": "left", "sortable": True},
                        {"name": "year", "label": "Year", "field": "year", "align": "left", "sortable": True},
                        {"name": "imdb_id", "label": "IMDB ID", "field": "imdb_id", "align": "left"},
                        {"name": "tmdb_id", "label": "TMDB ID", "field": "tmdb_id", "align": "left"},
                        {"name": "status", "label": "Status", "field": "status", "align": "left", "sortable": True},
                        {"name": "source", "label": "Source", "field": "source", "align": "left"},
                        {"name": "created_at", "label": "Added", "field": "created_at", "align": "left", "sortable": True},
                    ]

                    table = ui.table(
                        columns=columns,
                        rows=items,
                        row_key="id",
                        pagination={"rowsPerPage": 25},
                    ).classes("w-full").props("dark flat dense")

                    # Add row action slots
                    table.add_slot("body-cell-status", """
                        <q-td :props="props">
                            <q-badge :color="props.value === 'collected' ? 'green' : props.value === 'downloading' ? 'amber' : props.value === 'watchlisted' ? 'blue' : 'red'">
                                {{ props.value }}
                            </q-badge>
                        </q-td>
                    """)

                    table.add_slot("body-cell-media_type", """
                        <q-td :props="props">
                            <q-badge :color="props.value === 'movie' ? 'blue' : props.value === 'show' ? 'green' : 'purple'">
                                {{ props.value }}
                            </q-badge>
                        </q-td>
                    """)

        def update_filter(key, value):
            filter_state[key] = value
            load_content()

        load_content()


def _render_section(title, icon, items, count):
    """Render a content section with cards."""
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2 p-4"):
            ui.icon(icon).classes("text-xl").style(f"color: {COLORS['primary']}")
            ui.label(f"{title} ({count})").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")

        with ui.row().classes("p-4 pt-0 gap-4 flex-wrap overflow-x-auto"):
            for item in items[:24]:
                content_card(item)
            if len(items) > 24:
                with ui.column().classes("items-center justify-center p-4"):
                    ui.label(f"+{len(items) - 24} more").classes("text-sm").style(f"color: {COLORS['text_muted']}")
