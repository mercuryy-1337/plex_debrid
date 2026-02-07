"""
Activity page for pd_reloaded.

Shows a live queue of torrents: sent → downloading → downloaded → processing → completed.
Items are removed from the queue once fully processed.
"""

import logging
from nicegui import ui, Client

from webapp.components import page_header, empty_state
from webapp.theme import COLORS

logger = logging.getLogger(__name__)

# Human-friendly labels & colours per status
_STATUS_META = {
    "sent":        {"label": "Sent",        "color": COLORS["info"],    "icon": "cloud_upload"},
    "downloading": {"label": "Downloading", "color": COLORS["warning"], "icon": "downloading"},
    "downloaded":  {"label": "Downloaded",  "color": COLORS["success"], "icon": "cloud_done"},
    "processing":  {"label": "Processing",  "color": COLORS["primary"], "icon": "hourglass_top"},
    "completed":   {"label": "Completed",   "color": COLORS["success"], "icon": "check_circle"},
}


async def render(app_state, client: Client):
    """Render the Activity page."""

    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4"):
        page_header("Activity", "Live download queue — torrents are removed once completed")

        # ── Column headings (always visible) ──
        with ui.row().classes("w-full items-center px-4 py-2").style(
            f"border-bottom: 1px solid {COLORS['surface_light']}"
        ):
            ui.label("Title / Torrent").classes("text-xs font-semibold uppercase").style(
                f"color: {COLORS['text_muted']}; flex: 1; min-width: 0")
            ui.label("Status").classes("text-xs font-semibold uppercase text-center").style(
                f"color: {COLORS['text_muted']}; width: 120px")
            ui.label("Progress").classes("text-xs font-semibold uppercase text-center").style(
                f"color: {COLORS['text_muted']}; width: 160px")
            ui.label("Submitted").classes("text-xs font-semibold uppercase text-right").style(
                f"color: {COLORS['text_muted']}; width: 80px")

        activity_container = ui.column().classes("w-full gap-2")

        def _build_rows():
            """Rebuild the activity rows inside the container."""
            activity_container.clear()
            items = app_state.get_activities()

            if not items:
                with activity_container:
                    empty_state("hourglass_empty", "No active downloads")
                return

            with activity_container:
                for item in items:
                    meta = _STATUS_META.get(item["status"], _STATUS_META["sent"])
                    progress = min(max(item.get("progress", 0.0), 0), 1)
                    submitted = item["submitted_at"].strftime("%H:%M:%S")
                    imdb_id = item.get("imdb_id", "")

                    with ui.card().classes("w-full p-0").style(
                        f"background: {COLORS['surface']}; border-left: 3px solid {meta['color']}"
                    ):
                        with ui.row().classes("w-full items-center px-4 py-3 gap-4"):
                            # ── Title / Torrent column ──
                            with ui.column().classes("gap-0 min-w-0").style("flex: 1"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(item["title"]).classes(
                                        "text-sm font-bold truncate"
                                    ).style(f"color: {COLORS['text']}")
                                    if imdb_id:
                                        ui.label(imdb_id).classes("text-xs").style(
                                            f"color: {COLORS['primary']}; "
                                            f"background: {COLORS['primary']}18; "
                                            "border-radius: 4px; padding: 1px 6px")
                                    if item.get("category"):
                                        ui.label(item["category"]).classes("text-xs").style(
                                            f"color: {COLORS['text_muted']}; "
                                            f"background: {COLORS['surface_light']}; "
                                            "border-radius: 4px; padding: 1px 6px")
                                ui.label(item["release_title"]).classes(
                                    "text-xs truncate"
                                ).style(f"color: {COLORS['text_muted']}; max-width: 100%")

                            # ── Status column ──
                            with ui.column().classes("items-center").style("width: 120px"):
                                with ui.row().classes("items-center gap-1").style(
                                    f"background: {meta['color']}22; "
                                    f"border: 1px solid {meta['color']}44; "
                                    "border-radius: 12px; padding: 2px 10px"
                                ):
                                    ui.icon(meta["icon"]).style(
                                        f"color: {meta['color']}; font-size: 0.85rem")
                                    ui.label(meta["label"]).classes("text-xs font-semibold").style(
                                        f"color: {meta['color']}")

                            # ── Progress column ──
                            with ui.row().classes("items-center gap-2").style("width: 160px"):
                                ui.linear_progress(value=progress).props(
                                    f"color={'amber' if progress < 1 else 'positive'} rounded size=6px"
                                ).classes("flex-1")
                                ui.label(f"{progress * 100:.0f}%").classes(
                                    "text-xs font-medium"
                                ).style(f"color: {COLORS['text_muted']}; width: 36px; text-align: right")

                            # ── Submitted column ──
                            ui.label(submitted).classes("text-xs text-right").style(
                                f"color: {COLORS['text_muted']}; width: 80px")

        # Initial render
        _build_rows()

        # Auto-refresh every 2s
        ui.timer(2.0, _build_rows)
