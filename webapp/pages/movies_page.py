"""Movie media page (/movies/{imdbid})."""

import asyncio
import datetime as dt
import logging
import os
import re

import requests
from nicegui import ui, Client

from webapp.components import empty_state
from webapp.pages.media_common import (
    element_matches_imdb_id,
    format_date_display,
    get_cached_meta_for_library_item,
    get_content_library_item,
    get_plex_file_paths,
    open_quick_scrape_popup,
    path_in_plex,
    render_action_icon,
    render_media_skeleton,
    render_settings_button,
    status_badges,
)
from webapp.theme import COLORS

logger = logging.getLogger(__name__)


def _get_library_movie_by_imdb(imdb_id):
    import content.services.plex as plex_svc

    library = plex_svc.library(silent=True)
    for item in library:
        if getattr(item, "type", "") != "movie":
            continue
        if element_matches_imdb_id(item, imdb_id):
            return item
    return None


def _get_movie_plex_file_path(movie):
    try:
        for media in getattr(movie, "Media", []) or []:
            for part in getattr(media, "Part", []) or []:
                full = getattr(part, "file", "")
                if full:
                    return full
    except Exception:
        pass
    return ""





def _scan_local_movie_file(app_state, imdb_id, title="", year=None):
    root = (app_state.db.get_setting("Global Media Folder", "") or "").rstrip("/")
    if not root or not os.path.isdir(root):
        return ""

    needle = f"imdb-{imdb_id}".lower()
    title_tokens = [
        token for token in re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).split()
        if len(token) >= 3
    ]
    year_text = str(year) if year else ""

    def _dir_matches(low_dirpath):
        if needle and needle in low_dirpath:
            return True
        if not title_tokens:
            return False
        token_hits = sum(1 for token in title_tokens if token in low_dirpath)
        min_hits = 1 if len(title_tokens) == 1 else 2
        if token_hits < min_hits:
            return False
        if year_text and year_text not in low_dirpath:
            return False
        return True

    try:
        for dirpath, _, filenames in os.walk(root):
            low_dirpath = dirpath.lower()
            if not _dir_matches(low_dirpath):
                continue
            for filename in filenames:
                low = filename.lower()
                if low.endswith((".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv")):
                    return os.path.join(dirpath, filename)
    except Exception:
        logger.debug("Local movie scan failed for %s", imdb_id)

    return ""


def _display_relative_path(app_state, full_path):
    if not full_path:
        return "N/A"
    root = (app_state.db.get_setting("Global Media Folder", "") or "").rstrip("/")
    if root and full_path.startswith(root):
        rel = os.path.relpath(full_path, root)
        return f"/{rel}"
    return full_path


def _split_display_path(display_path):
    raw = (display_path or "").strip()
    if not raw or raw == "N/A":
        return "N/A", "N/A"

    normalized = raw.replace("\\", "/")
    filename = os.path.basename(normalized) or "N/A"
    folder = os.path.dirname(normalized)
    if folder and not folder.endswith("/"):
        folder += "/"
    return folder or "N/A", filename





def _format_runtime_display(runtime_value):
    raw = (runtime_value or "").strip().lower()
    if not raw:
        return "N/A"

    minutes_match = re.search(r"(\d+)", raw)
    if not minutes_match:
        return "N/A"

    total_minutes = int(minutes_match.group(1))
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h"
    return f"{minutes}m"


def _format_release_year(value):
    raw = (value or "").strip()
    if not raw or raw == "N/A":
        return "N/A"
    if re.fullmatch(r"\d{4}", raw):
        return raw
    match = re.search(r"(\d{4})", raw)
    return match.group(1) if match else "N/A"


def _is_movie_downloading(app_state, imdb_id):
    try:
        for activity in app_state.get_activities():
            if activity.get("imdb_id") == imdb_id:
                return True
    except Exception:
        pass

    try:
        for row in app_state.db.get_all_content(status="downloading"):
            if row.get("imdb_id") == imdb_id and row.get("media_type") in ("movie", "anime_movie"):
                return True
    except Exception:
        pass

    return False


def _movie_release_category(app_state, imdb_id):
    try:
        logs = app_state.db.get_download_logs(limit=1000)
        for log in logs:
            if log.get("imdb_id") != imdb_id:
                continue
            debrid_service = log.get("debrid_service", "") or ""
            match = re.search(r"\(([^()]+)\)\s*$", debrid_service)
            if match:
                return match.group(1)
            return "N/A"
    except Exception:
        pass
    return "N/A"


def _auto_fetch_movie(app_state, imdb_id):
    from webapp.automation import get_automation_engine

    engine = get_automation_engine(app_state)
    engine._load_settings_into_modules()
    engine._setup_decypharr_download()

    import content.classes

    lib_services = content.classes.library()
    library = lib_services[0]() if lib_services else []

    movie = _get_library_movie_by_imdb(imdb_id)
    if movie is None:
        return False, "Movie not found in Plex library"

    if hasattr(movie, "_ensure_loaded"):
        movie._ensure_loaded()

    movie = content.classes.media(movie)
    movie.aliases("en")

    try:
        movie.download(library=library, parentReleases=[])
        return True, "Fetch started"
    except Exception as exc:
        logger.exception("Auto-fetch movie failed")
        return False, str(exc)


def _delete_movie(app_state, imdb_id, file_path, plex_movie, delete_from_plex=False):
    removed_local = False
    removed_plex = False

    try:
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)
            removed_local = True
            parent = os.path.dirname(file_path)
            try:
                if parent and os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
            except Exception:
                pass
    except Exception:
        logger.debug("Could not delete local movie file for %s", imdb_id)

    try:
        from database.models import ContentItem
        with app_state.db.session_scope() as session:
            session.query(ContentItem).filter(
                ContentItem.imdb_id == imdb_id,
                ContentItem.media_type.in_(["movie", "anime_movie"]),
            ).delete(synchronize_session=False)
    except Exception:
        logger.debug("Could not delete movie DB row for %s", imdb_id)

    if delete_from_plex and plex_movie is not None:
        try:
            import content.services.plex as plex_svc
            if plex_svc.users:
                token = plex_svc.users[0][1]
                server = plex_svc.library.url.rstrip("/")
                rating_key = getattr(plex_movie, "ratingKey", "")
                if server and token and rating_key:
                    response = requests.delete(
                        f"{server}/library/metadata/{rating_key}",
                        params={"X-Plex-Token": token},
                        timeout=10,
                    )
                    if response.ok:
                        removed_plex = True
        except Exception:
            logger.debug("Could not delete movie %s from Plex", imdb_id)

    if delete_from_plex:
        return removed_local or removed_plex, "Deleted locally + Plex where available"
    return removed_local, "Deleted locally + DB where available"


async def render(app_state, client: Client, imdb_id: str):
    library_item = get_content_library_item(app_state, imdb_id, "movie")
    if not library_item:
        with ui.column().classes("p-6 gap-4 w-full"):
            empty_state("movie", "Movie not found in content library.")
        return

    meta = await get_cached_meta_for_library_item(app_state, imdb_id=imdb_id, kind="movie")
    library_movie = await asyncio.get_event_loop().run_in_executor(None, _get_library_movie_by_imdb, imdb_id)
    movie_title = meta.get("name") or library_item.get("title") or "Unknown Movie"
    poster = meta.get("poster") or library_item.get("poster_url")
    release_date = meta.get("released") or meta.get("releaseInfo") or library_item.get("release_date") or "N/A"
    description = meta.get("description") or library_item.get("overview") or ""
    background = meta.get("background") or library_item.get("backdrop_url") or ""
    runtime_display = _format_runtime_display(meta.get("runtime") or "")
    release_year = _format_release_year(release_date)
    release_day = None
    if release_date and release_date != "N/A":
        candidate = release_date[:10]
        try:
            release_day = dt.datetime.strptime(candidate, "%Y-%m-%d").date()
            release_date = candidate
        except Exception:
            release_day = None

    release_runtime_line = f"Release date: {release_year} | Runtime: {runtime_display}"

    plex_path = _get_movie_plex_file_path(library_movie) if library_movie is not None else ""
    local_path = await asyncio.get_event_loop().run_in_executor(
        None,
        _scan_local_movie_file,
        app_state,
        imdb_id,
        movie_title,
        library_item.get("year"),
    )
    path_to_show = _display_relative_path(app_state, plex_path or local_path)
    folder_path, file_name = _split_display_path(path_to_show)

    plex_exact, plex_bases = await asyncio.get_event_loop().run_in_executor(
        None, get_plex_file_paths, app_state
    )

    in_plex = bool(plex_path) or path_in_plex(local_path, plex_exact, plex_bases)
    in_local_only = (not in_plex) and bool(local_path)
    downloading = _is_movie_downloading(app_state, imdb_id)
    category = _movie_release_category(app_state, imdb_id)

    def _render_content_info():
        ui.label("Content Info").classes("text-lg font-semibold mb-2").style(f"color:{COLORS['text']}")

        with ui.row().classes("w-full items-center no-wrap px-2 py-2").style(
            f"border-bottom:1px solid {COLORS['surface_light']};font-weight:600"
        ):
            ui.label("File name").style("width: 45%")
            ui.label("Release version category").style("width: 16%")
            ui.label("Status").style("width: 20%")
            ui.label("Plex").style("width: 10%; text-align: center")
            ui.label("Delete").style("width: 3%; text-align: right")
            ui.label("Search").style("width: 3%; text-align: right")
            ui.label("Fetch").style("width: 3%; text-align: right")

        with ui.row().classes("w-full items-center no-wrap px-2 py-2").style(
            f"border-bottom:1px solid {COLORS['surface_light']}"
        ):
            ui.label(file_name).style(
                f"width: 45%; color:{COLORS['text_muted']}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis"
            )
            ui.label(category).style("width: 16%")

            with ui.row().classes("items-center gap-1").style("width: 20%"):
                for text, color in status_badges(
                    in_plex=in_plex,
                    in_local_only=in_local_only,
                    downloading=downloading,
                ):
                    ui.badge(text, color=color)

            with ui.row().classes("items-center justify-center").style("width: 10%"):
                if in_plex:
                    ui.icon("check_circle").style(f"color:{COLORS['success']}")

            with ui.row().classes("justify-end").style("width: 3%"):
                released = True if release_day is None else (dt.date.today() >= release_day)

                async def _auto_fetch(_):
                    ui.notify("Fetching movie...", type="info")
                    ok, msg = await asyncio.get_event_loop().run_in_executor(
                        None, _auto_fetch_movie, app_state, imdb_id
                    )
                    ui.notify(msg, type=("positive" if ok else "negative"))

                async def _interactive(_):
                    await open_quick_scrape_popup(
                        app_state,
                        imdb_id=imdb_id,
                        media_type="movie",
                        title=movie_title,
                        poster_url=poster or "",
                    )

                async def _delete_local():
                    ok, msg = await asyncio.get_event_loop().run_in_executor(
                        None,
                        _delete_movie,
                        app_state,
                        imdb_id,
                        plex_path or local_path,
                        library_movie,
                        False,
                    )
                    ui.notify(msg, type=("positive" if ok else "warning"))
                    ui.navigate.to(f"/movies/{imdb_id}")

                async def _delete_both():
                    ok, msg = await asyncio.get_event_loop().run_in_executor(
                        None,
                        _delete_movie,
                        app_state,
                        imdb_id,
                        plex_path or local_path,
                        library_movie,
                        True,
                    )
                    ui.notify(msg, type=("positive" if ok else "warning"))
                    ui.navigate.to(f"/movies/{imdb_id}")

                render_settings_button(
                    on_delete_local=_delete_local,
                    on_delete_both=_delete_both,
                )

            with ui.row().classes("justify-end").style("width: 3%"):
                render_action_icon(
                    icon="manage_search",
                    color="blue",
                    on_click=_interactive,
                    enabled=True,
                )

            with ui.row().classes("justify-end").style("width: 3%"):
                render_action_icon(
                    icon="download",
                    color="amber",
                    on_click=_auto_fetch,
                    enabled=released,
                )

    render_media_skeleton(
        page_title="Movie",
        imdb_id=imdb_id,
        media_title=movie_title,
        poster_url=poster,
        release_date=release_runtime_line,
        media_path=folder_path,
        description=description,
        background_url=background,
        release_label="",
        content_renderer=_render_content_info,
    )
