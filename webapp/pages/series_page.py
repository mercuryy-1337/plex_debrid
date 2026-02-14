"""Series media page (/series/{imdbid})."""

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
from webapp.parse import TorrentParser
from webapp.theme import COLORS

logger = logging.getLogger(__name__)


def _get_library_show_by_imdb(imdb_id):
    import content.services.plex as plex_svc

    library = plex_svc.library(silent=True)
    for item in library:
        if getattr(item, "type", "") != "show":
            continue
        if element_matches_imdb_id(item, imdb_id):
            return item
    return None


def _get_library_show_by_title_year(title, year=None):
    import content.services.plex as plex_svc

    target_title = (title or "").strip().lower()
    target_year = int(year) if year else None
    if not target_title:
        return None

    library = plex_svc.library(silent=True)
    for item in library:
        if getattr(item, "type", "") != "show":
            continue
        item_title = (getattr(item, "title", "") or "").strip().lower()
        if item_title != target_title:
            continue
        if target_year is not None:
            item_year = getattr(item, "year", None)
            try:
                if int(item_year) != target_year:
                    continue
            except Exception:
                continue
        return item
    return None


def _build_cinemeta_season_rows(meta):
    season_map = {}
    videos = meta.get("videos") or []

    for video in videos:
        season = video.get("season")
        episode = video.get("episode")
        if season is None or episode is None:
            continue
        try:
            season_number = int(season)
            episode_number = int(episode)
        except Exception:
            continue
        if season_number <= 0 or episode_number <= 0:
            continue

        raw_date = (
            video.get("released")
            or video.get("releaseDate")
            or video.get("firstAired")
            or ""
        )
        air_date = str(raw_date)[:10] if raw_date else "N/A"
        season_map.setdefault(season_number, {})[episode_number] = air_date

    rows = []
    for season_number in sorted(season_map.keys()):
        episode_rows = [
            {"episode_number": episode_number, "air_date": season_map[season_number][episode_number]}
            for episode_number in sorted(season_map[season_number].keys())
        ]
        rows.append({"season_number": season_number, "episodes": episode_rows})
    return rows


def _get_episode_plex_file_full_path(episode):
    try:
        for media in getattr(episode, "Media", []) or []:
            for part in getattr(media, "Part", []) or []:
                full = getattr(part, "file", "")
                if full:
                    return full
    except Exception:
        pass
    return ""





def _scan_local_episode_files(app_state, imdb_id, is_anime=False, title="", year=None):
    root = (app_state.db.get_setting("Global Media Folder", "") or "").rstrip("/")
    mapping = {}
    if not root or not os.path.isdir(root):
        return mapping

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
            low = dirpath.lower()
            if not _dir_matches(low):
                continue
            for filename in filenames:
                se = TorrentParser.extract_season_episode(filename, is_anime=is_anime)
                if not se:
                    continue
                mapping.setdefault(se, os.path.join(dirpath, filename))
    except Exception:
        logger.debug("Local file scan failed for %s", imdb_id)

    return mapping


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


def _collect_downloading_episode_keys(app_state, imdb_id):
    keys = set()

    try:
        for activity in app_state.get_activities():
            if activity.get("imdb_id") != imdb_id:
                continue
            raw = f"{activity.get('release_title', '')} {activity.get('title', '')}"
            se = TorrentParser.extract_season_episode(raw, is_anime=True)
            if se:
                keys.add(se)
    except Exception:
        pass

    try:
        for row in app_state.db.get_all_content(status="downloading"):
            if row.get("imdb_id") != imdb_id:
                continue
            title = row.get("title", "")
            m = re.search(r"S(\d{2})E(\d{2,3})", title, re.I)
            if m:
                keys.add(f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}")
    except Exception:
        pass

    return keys


def _auto_fetch_episode(app_state, imdb_id, season_index, episode_index):
    from webapp.automation import get_automation_engine

    engine = get_automation_engine(app_state)
    engine._load_settings_into_modules()
    engine._setup_decypharr_download()

    import content.classes

    lib_services = content.classes.library()
    library = lib_services[0]() if lib_services else []

    show = _get_library_show_by_imdb(imdb_id)
    if show is None:
        row = get_content_library_item(app_state, imdb_id, "series")
        show = _get_library_show_by_title_year(
            (row or {}).get("title"),
            (row or {}).get("year"),
        )
    if show is None:
        return False, "Show not found in Plex library"

    if hasattr(show, "_ensure_loaded"):
        show._ensure_loaded()

    show = content.classes.media(show)

    show.isanime()
    show.aliases("en")

    target = None
    for season in getattr(show, "Seasons", []) or []:
        if int(getattr(season, "index", 0)) != int(season_index):
            continue
        for episode in getattr(season, "Episodes", []) or []:
            if int(getattr(episode, "index", 0)) == int(episode_index):
                target = episode
                break
        if target:
            break

    if target is None:
        return False, "Episode not found"

    try:
        target.download(library=library, parentReleases=[])
        return True, "Fetch started"
    except Exception as exc:
        logger.exception("Auto-fetch failed")
        return False, str(exc)


def _delete_episode(app_state, imdb_id, se_key, file_path, plex_episode, delete_from_plex=False):
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
        logger.debug("Could not delete local file for %s", se_key)

    try:
        from database.models import ContentItem
        with app_state.db.session_scope() as session:
            session.query(ContentItem).filter(
                ContentItem.imdb_id == imdb_id,
                ContentItem.media_type == "episode",
                ContentItem.title.ilike(f"%{se_key}%"),
            ).delete(synchronize_session=False)
    except Exception:
        logger.debug("Could not delete episode DB row for %s", se_key)

    if delete_from_plex and plex_episode is not None:
        try:
            import content.services.plex as plex_svc
            if plex_svc.users:
                token = plex_svc.users[0][1]
                server = plex_svc.library.url.rstrip("/")
                rating_key = getattr(plex_episode, "ratingKey", "")
                if server and token and rating_key:
                    response = requests.delete(
                        f"{server}/library/metadata/{rating_key}",
                        params={"X-Plex-Token": token},
                        timeout=10,
                    )
                    if response.ok:
                        removed_plex = True
        except Exception:
            logger.debug("Could not delete episode %s from Plex", se_key)

    if delete_from_plex:
        return removed_local or removed_plex, "Deleted locally + Plex where available"
    return removed_local, "Deleted locally + DB where available"


async def render(app_state, client: Client, imdb_id: str):
    library_item = get_content_library_item(app_state, imdb_id, "series")
    if not library_item:
        with ui.column().classes("p-6 gap-4 w-full"):
            empty_state("live_tv", "Series not found in content library.")
        return

    meta = await get_cached_meta_for_library_item(app_state, imdb_id=imdb_id, kind="series")
    library_show = await asyncio.get_event_loop().run_in_executor(None, _get_library_show_by_imdb, imdb_id)

    if library_show is None:
        library_show = await asyncio.get_event_loop().run_in_executor(
            None,
            _get_library_show_by_title_year,
            library_item.get("title"),
            library_item.get("year"),
        )

    show_title = meta.get("name") or library_item.get("title") or getattr(library_show, "title", "Unknown Series")
    poster = meta.get("poster") or library_item.get("poster_url") or getattr(library_show, "thumb", None)
    description = meta.get("description") or library_item.get("overview") or ""
    background = meta.get("background") or library_item.get("backdrop_url") or ""

    cinemeta_seasons = _build_cinemeta_season_rows(meta)

    dates = []
    for season_row in cinemeta_seasons:
        for episode_row in season_row["episodes"]:
            air_date = episode_row.get("air_date")
            if air_date and air_date != "N/A":
                dates.append(air_date)

    if not dates and library_show is not None:
        for season in getattr(library_show, "Seasons", []) or []:
            for episode in getattr(season, "Episodes", []) or []:
                raw = getattr(episode, "originallyAvailableAt", "") or ""
                if raw:
                    dates.append(raw[:10])

    release_date_display = meta.get("releaseInfo") or "N/A"

    is_anime = (library_item.get("media_type") or "") == "anime_show"

    local_map = await asyncio.get_event_loop().run_in_executor(
        None,
        _scan_local_episode_files,
        app_state,
        imdb_id,
        is_anime,
        show_title,
        library_item.get("year"),
    )
    downloading_keys = _collect_downloading_episode_keys(app_state, imdb_id)

    plex_episode_map = {}
    if library_show is not None:
        for season in getattr(library_show, "Seasons", []) or []:
            for episode in getattr(season, "Episodes", []) or []:
                key = f"S{int(getattr(season, 'index', 0)):02d}E{int(getattr(episode, 'index', 0)):02d}"
                plex_episode_map[key] = episode

    plex_exact_paths, plex_basenames = await asyncio.get_event_loop().run_in_executor(
        None, get_plex_file_paths, app_state
    )

    series_folder_path = "N/A"
    for key in sorted(plex_episode_map.keys()):
        full_path = _get_episode_plex_file_full_path(plex_episode_map[key])
        if not full_path:
            continue
        display_path = _display_relative_path(app_state, full_path)
        folder, _ = _split_display_path(display_path)
        if folder != "N/A":
            series_folder_path = folder
            break

    if series_folder_path == "N/A":
        for key in sorted(local_map.keys()):
            full_path = local_map.get(key) or ""
            if not full_path:
                continue
            display_path = _display_relative_path(app_state, full_path)
            folder, _ = _split_display_path(display_path)
            if folder != "N/A":
                series_folder_path = folder
                break

    if cinemeta_seasons:
        seasons = cinemeta_seasons
    elif library_show is not None:
        seasons = []
        plex_seasons = sorted(
            [s for s in (getattr(library_show, "Seasons", []) or []) if int(getattr(s, "index", 0)) > 0],
            key=lambda x: int(getattr(x, "index", 0)),
        )
        for season in plex_seasons:
            season_number = int(getattr(season, "index", 0))
            episodes = []
            for episode in sorted(list(getattr(season, "Episodes", []) or []), key=lambda x: int(getattr(x, "index", 0))):
                ep_num = int(getattr(episode, "index", 0))
                raw = getattr(episode, "originallyAvailableAt", "") or ""
                episodes.append({"episode_number": ep_num, "air_date": (raw[:10] if raw else "N/A")})
            seasons.append({"season_number": season_number, "episodes": episodes})
    else:
        seasons = []

    today = dt.date.today()

    def _render_content_info():
        ui.label("Content Info").classes("text-lg font-semibold mb-2").style(f"color:{COLORS['text']}")

        if not seasons:
            empty_state("live_tv", "No seasons found")
            return

        for season in seasons:
            season_number = int(season["season_number"])
            episodes = season["episodes"]

            with ui.expansion(f"Season {season_number}", value=False).classes("w-full"):
                with ui.row().classes("w-full items-center no-wrap px-2 py-2").style(
                    f"border-bottom:1px solid {COLORS['surface_light']};font-weight:600"
                ):
                    ui.label("#").style("width: 10%")
                    ui.label("File name").style("width: 40%")
                    ui.label("Air date").style("width: 12%")
                    ui.label("Episode status").style("width: 19%")
                    ui.label("Plex").style("width: 10%; text-align: center")
                    ui.label("Delete").style("width: 3%; text-align: right")
                    ui.label("Search").style("width: 3%; text-align: right")
                    ui.label("Fetch").style("width: 3%; text-align: right")

                for episode in episodes:
                    ep_num = int(episode["episode_number"])
                    se_key = f"S{season_number:02d}E{ep_num:02d}"

                    in_plex = se_key in plex_episode_map
                    plex_file_path = _get_episode_plex_file_full_path(plex_episode_map[se_key]) if in_plex else ""
                    local_file_path = local_map.get(se_key, "")
                    if (not in_plex) and local_file_path:
                        in_plex = path_in_plex(local_file_path, plex_exact_paths, plex_basenames)
                    display_path = _display_relative_path(app_state, plex_file_path or local_file_path)
                    _, file_name = _split_display_path(display_path)

                    air_date = episode.get("air_date") or "N/A"
                    released = False
                    if air_date != "N/A":
                        try:
                            released = dt.datetime.strptime(air_date, "%Y-%m-%d").date() <= today
                        except Exception:
                            released = False

                    can_fetch = released

                    in_local_only = (not in_plex) and bool(local_file_path)
                    is_downloading = se_key in downloading_keys
                    is_missing = (not in_plex) and (not in_local_only) and (not is_downloading)
                    not_yet_aired = is_missing and (air_date != "N/A") and (not released)

                    with ui.row().classes("w-full items-center no-wrap px-2 py-2").style(
                        f"border-bottom:1px solid {COLORS['surface_light']}"
                    ):
                        ui.label(str(ep_num)).style("width: 10%")
                        ui.label(file_name).style(
                            f"width: 40%; color:{COLORS['text_muted']}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis"
                        )
                        ui.label(format_date_display(air_date)).style("width: 12%")

                        with ui.row().classes("items-center gap-1").style("width: 19%"):
                            for text, color in status_badges(
                                in_plex=in_plex,
                                in_local_only=in_local_only,
                                downloading=is_downloading,
                                not_yet_aired=not_yet_aired,
                            ):
                                ui.badge(text, color=color)

                        with ui.row().classes("items-center justify-center").style("width: 10%"):
                            if in_plex:
                                ui.icon("check_circle").style(f"color:{COLORS['success']}")

                        with ui.row().classes("justify-end").style("width: 3%"):
                            async def _auto_fetch(_, s=season_number, e=ep_num, can=can_fetch):
                                if not can:
                                    return
                                ui.notify(f"Fetching S{s:02d}E{e:02d}...", type="info")
                                ok, msg = await asyncio.get_event_loop().run_in_executor(
                                    None, _auto_fetch_episode, app_state, imdb_id, s, e
                                )
                                ui.notify(msg, type=("positive" if ok else "negative"))

                            async def _interactive(_, s=season_number, e=ep_num, can=released):
                                if not can:
                                    return
                                await open_quick_scrape_popup(
                                    app_state,
                                    imdb_id=imdb_id,
                                    media_type="series",
                                    title=f"{show_title} S{s:02d}E{e:02d}",
                                    poster_url=poster or "",
                                    season=s,
                                    episode=e,
                                )

                            async def _delete_local():
                                ok, msg = await asyncio.get_event_loop().run_in_executor(
                                    None,
                                    _delete_episode,
                                    app_state,
                                    imdb_id,
                                    se_key,
                                    plex_file_path or local_file_path,
                                    plex_episode_map.get(se_key),
                                    False,
                                )
                                ui.notify(msg, type=("positive" if ok else "warning"))
                                ui.navigate.to(f"/series/{imdb_id}")

                            async def _delete_both():
                                ok, msg = await asyncio.get_event_loop().run_in_executor(
                                    None,
                                    _delete_episode,
                                    app_state,
                                    imdb_id,
                                    se_key,
                                    plex_file_path or local_file_path,
                                    plex_episode_map.get(se_key),
                                    True,
                                )
                                ui.notify(msg, type=("positive" if ok else "warning"))
                                ui.navigate.to(f"/series/{imdb_id}")

                            render_settings_button(
                                on_delete_local=_delete_local,
                                on_delete_both=_delete_both,
                            )

                        with ui.row().classes("justify-end").style("width: 3%"):
                            render_action_icon(
                                icon="manage_search",
                                color="blue",
                                on_click=_interactive,
                                enabled=released,
                            )

                        with ui.row().classes("justify-end").style("width: 3%"):
                            render_action_icon(
                                icon="download",
                                color="amber",
                                on_click=_auto_fetch,
                                enabled=can_fetch,
                            )

    render_media_skeleton(
        page_title="Series",
        imdb_id=imdb_id,
        media_title=show_title,
        poster_url=poster,
        release_date=release_date_display,
        media_path=series_folder_path,
        description=description,
        background_url=background,
        release_label="",
        content_renderer=_render_content_info,
    )
