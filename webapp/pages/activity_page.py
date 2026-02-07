"""
Activity page for pd_reloaded.

Shows a live table of incoming torrents:
sent → downloading → downloaded → processing → completed.
Items are removed from the queue once fully processed.
"""

import logging
from nicegui import ui, Client

from webapp.components import page_header, empty_state
from webapp.theme import COLORS

logger = logging.getLogger(__name__)

# Human-friendly labels per status
_STATUS_LABELS = {
    "sent":        "Sent",
    "downloading": "Downloading",
    "downloaded":  "Downloaded",
    "processing":  "Processing",
    "completed":   "Completed",
}

# Table column definitions
_COLUMNS = [
    {"name": "title", "label": "Title", "field": "title", "align": "left", "sortable": True},
    {"name": "release", "label": "Torrent", "field": "release", "align": "left", "sortable": True},
    {"name": "imdb", "label": "IMDB", "field": "imdb", "align": "left"},
    {"name": "category", "label": "Category", "field": "category", "align": "left"},
    {"name": "status", "label": "Status", "field": "status", "align": "center", "sortable": True},
    {"name": "progress", "label": "Progress", "field": "progress", "align": "center", "sortable": True},
    {"name": "submitted", "label": "Submitted", "field": "submitted", "align": "right", "sortable": True},
]


def _build_rows(app_state) -> list[dict]:
    """Convert activity items to table row dicts."""
    items = app_state.get_activities()
    rows = []
    for item in items:
        progress = min(max(item.get("progress", 0.0), 0), 1)
        rows.append({
            "title": item.get("title", ""),
            "release": item.get("release_title", ""),
            "imdb": item.get("imdb_id", ""),
            "category": item.get("category", ""),
            "status": _STATUS_LABELS.get(item["status"], item["status"]),
            "progress": f"{progress * 100:.0f}%",
            "submitted": item["submitted_at"].strftime("%H:%M:%S"),
        })
    return rows


async def render(app_state, client: Client):
    """Render the Activity page as a simple table."""

    with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-4"):
        page_header("Activity", "Live download queue — torrents are removed once completed")

        initial_rows = _build_rows(app_state)

        if not initial_rows:
            placeholder = ui.column().classes("w-full")
            with placeholder:
                empty_state("hourglass_empty", "No active downloads")

        table = ui.table(
            columns=_COLUMNS,
            rows=initial_rows,
            row_key="release",
        ).classes("w-full").props(
            'flat bordered dense separator=cell '
            'hide-pagination rows-per-page-options="[0]" '
            'no-data-label="No active downloads"'
        ).style(
            f"background: {COLORS['surface']}; "
            f"color: {COLORS['text']}"
        )

        # Style the table header
        table.add_slot('header', f'''
            <q-tr :props="props">
                <q-th v-for="col in props.cols" :key="col.name" :props="props"
                    style="background: {COLORS['surface_light']}; color: {COLORS['text_muted']};
                           font-weight: 600; font-size: 0.7rem; text-transform: uppercase;
                           letter-spacing: 0.05em; border-bottom: 1px solid rgba(255,255,255,0.08)">
                    {{{{ col.label }}}}
                </q-th>
            </q-tr>
        ''')

        # Style body rows with status-aware colouring
        table.add_slot('body', f'''
            <q-tr :props="props">
                <q-td key="title" :props="props" style="color: {COLORS['text']}; font-weight: 600; font-size: 0.85rem; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                    {{{{ props.row.title }}}}
                </q-td>
                <q-td key="release" :props="props" style="color: {COLORS['text_muted']}; font-size: 0.8rem; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                    {{{{ props.row.release }}}}
                </q-td>
                <q-td key="imdb" :props="props" style="color: {COLORS['primary']}; font-size: 0.8rem">
                    {{{{ props.row.imdb }}}}
                </q-td>
                <q-td key="category" :props="props" style="color: {COLORS['text_muted']}; font-size: 0.8rem">
                    {{{{ props.row.category }}}}
                </q-td>
                <q-td key="status" :props="props" style="font-size: 0.8rem; font-weight: 600"
                    :style="props.row.status === 'Downloading' ? 'color: {COLORS['warning']}' :
                            props.row.status === 'Downloaded' ? 'color: {COLORS['success']}' :
                            props.row.status === 'Processing' ? 'color: {COLORS['primary']}' :
                            props.row.status === 'Completed' ? 'color: {COLORS['success']}' :
                            'color: {COLORS['info']}'">
                    {{{{ props.row.status }}}}
                </q-td>
                <q-td key="progress" :props="props" style="color: {COLORS['text_muted']}; font-size: 0.8rem; font-weight: 500">
                    {{{{ props.row.progress }}}}
                </q-td>
                <q-td key="submitted" :props="props" style="color: {COLORS['text_muted']}; font-size: 0.8rem">
                    {{{{ props.row.submitted }}}}
                </q-td>
            </q-tr>
        ''')

        def _refresh():
            rows = _build_rows(app_state)
            table.rows = rows
            table.update()
            # Toggle placeholder visibility
            if hasattr(render, '_placeholder'):
                pass

        ui.timer(2.0, _refresh)
