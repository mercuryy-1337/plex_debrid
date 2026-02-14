"""Shared UI/helpers for media detail pages."""

import asyncio
import logging
import os
import re
import time
import urllib3

import requests
from nicegui import ui

from webapp.library_cache import LibraryMetaCache
from webapp.theme import COLORS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

# ── Cached Plex file-path index (shared across pages) ──────────────
_plex_paths_cache: dict = {"exact": set(), "basenames": set(), "ts": 0.0}
_PLEX_CACHE_TTL = 120  # seconds


def _norm_path(value):
    return (value or "").replace("\\", "/").strip().lower()


def get_plex_file_paths(app_state, *, force=False):
    """Return (exact_paths: set, basenames: set) of every file Plex knows about.

    Queries the Plex HTTP API directly using DB-stored credentials so it
    works even when the legacy ``plex_svc.library()`` helper fails (e.g.
    wrong 'Plex server address' setting).

    Results are cached in-memory for ``_PLEX_CACHE_TTL`` seconds.
    """
    now = time.monotonic()
    if not force and (now - _plex_paths_cache["ts"]) < _PLEX_CACHE_TTL:
        return _plex_paths_cache["exact"], _plex_paths_cache["basenames"]

    exact: set[str] = set()
    basenames: set[str] = set()

    users = app_state.db.get_plex_users()
    primary = next((u for u in users if u.get("is_primary")), users[0] if users else None)
    if not primary or not primary.get("token"):
        _plex_paths_cache.update(exact=exact, basenames=basenames, ts=now)
        return exact, basenames

    token = primary["token"]
    headers = {"X-Plex-Token": token, "Accept": "application/json"}

    # Build ordered list of server URLs to try
    urls_to_try: list[str] = []
    if primary.get("server_url"):
        urls_to_try.append(primary["server_url"])
    settings_url = app_state.db.get_setting("Plex server address", "")
    if settings_url and settings_url not in urls_to_try:
        urls_to_try.append(settings_url)

    def _extract_files(metadata_list):
        for item in metadata_list:
            for media in item.get("Media", []):
                for part in media.get("Part", []):
                    fp = _norm_path(part.get("file", ""))
                    if fp:
                        exact.add(fp)
                        base = os.path.basename(fp)
                        if base:
                            basenames.add(base)

    for base_url in urls_to_try:
        try:
            resp = requests.get(
                f"{base_url}/library/sections",
                headers=headers, timeout=5, verify=False,
            )
            if not resp.ok:
                continue

            sections = resp.json().get("MediaContainer", {}).get("Directory", [])
            for section in sections:
                key = section.get("key")
                stype = section.get("type", "")
                # type=1 → movies, type=4 → episodes
                plex_type = "1" if stype == "movie" else "4" if stype == "show" else None
                if not plex_type:
                    continue
                r = requests.get(
                    f"{base_url}/library/sections/{key}/all",
                    headers=headers,
                    params={"type": plex_type},
                    timeout=30, verify=False,
                )
                if r.ok:
                    _extract_files(r.json().get("MediaContainer", {}).get("Metadata", []))

            # Success — stop trying other URLs
            break
        except Exception:
            logger.debug("Plex path index: %s unreachable", base_url)
            continue

    _plex_paths_cache.update(exact=exact, basenames=basenames, ts=now)
    return exact, basenames


def path_in_plex(path_value, exact_paths, basenames):
    """Check if a local file path exists in Plex by exact path or basename."""
    norm = _norm_path(path_value)
    if not norm:
        return False
    if norm in exact_paths:
        return True
    return os.path.basename(norm) in basenames


def extract_imdb_ids(element):
    ids = []
    for attr in ("EID", "parentEID", "grandparentEID"):
        values = getattr(element, attr, []) or []
        for value in values:
            if isinstance(value, str) and value.startswith("imdb://"):
                ids.append(value.replace("imdb://", ""))
    return ids


def normalize_imdb_id(value):
    raw = (value or "").strip().lower()
    if not raw:
        return ""

    match = re.search(r"tt\d+", raw)
    if match:
        return match.group(0)

    if raw.startswith("imdb://"):
        raw = raw.split("://", 1)[1]
    if raw.startswith("tt"):
        core = re.match(r"tt\d+", raw)
        return core.group(0) if core else raw

    if raw.startswith("imdb:"):
        raw = raw.split(":", 1)[1]

    digits = re.sub(r"\D", "", raw)
    if digits:
        return f"tt{digits}"
    return raw


def element_matches_imdb_id(element, imdb_id):
    target = normalize_imdb_id(imdb_id)
    if not target:
        return False

    extracted = [normalize_imdb_id(x) for x in extract_imdb_ids(element)]
    if target in extracted:
        return True

    guid_values = []
    for attr in ("Guid", "guid", "guids"):
        value = getattr(element, attr, None)
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                guid_values.append(getattr(item, "id", "") or str(item))
        else:
            guid_values.append(getattr(value, "id", "") or str(value))

    normalized_guid_values = [normalize_imdb_id(g) for g in guid_values if "imdb" in str(g).lower() or "tt" in str(g).lower()]
    return target in normalized_guid_values


def fetch_meta(kind, imdb_id):
    try:
        url = f"https://cinemeta-live.strem.io/meta/{kind}/{imdb_id}.json"
        response = requests.get(url, timeout=8)
        if response.ok:
            return response.json().get("meta", {})
    except Exception:
        logger.debug("Could not load cinemeta for %s", imdb_id)
    return {}


def get_content_library_item(app_state, imdb_id, media_scope):
    rows = app_state.db.get_all_content()
    for row in rows:
        if row.get("imdb_id") != imdb_id:
            continue
        media_type = row.get("media_type")
        if media_scope == "series" and media_type in ("show", "anime_show"):
            return row
        if media_scope == "movie" and media_type in ("movie", "anime_movie"):
            return row
    return None


async def get_cached_meta_for_library_item(app_state, *, imdb_id, kind):
    cache = LibraryMetaCache(app_state.config_dir)
    cached = await asyncio.get_event_loop().run_in_executor(None, cache.get, imdb_id, kind)
    if cached:
        return cached

    fresh = fetch_meta(kind, imdb_id)
    if fresh:
        await asyncio.get_event_loop().run_in_executor(None, cache.set, imdb_id, kind, fresh)
    return fresh


def status_badges(*, in_plex, in_local_only, downloading, not_yet_aired=False):
    if in_plex or in_local_only:
        return [("On Disk", "green")]
    if not_yet_aired:
        return [("Not yet aired", "grey")]
    return [("Missing", "red")]


def render_media_skeleton(
    *,
    page_title,
    imdb_id,
    media_title,
    poster_url,
    release_date,
    media_path="N/A",
    description,
    background_url,
    release_label="Release date",
    path_label="Path",
    content_renderer,
):
    with ui.column().classes("w-full gap-0").style("padding: 16px 24px 16px 24px"):

        with ui.card().classes("w-full p-0 overflow-hidden").style("position: relative; border-radius: 0"):
            if background_url:
                ui.html(
                    f'<div style="position:absolute;inset:0;background-image:url(\'{background_url}\');'
                    'background-size:cover;background-position:center;opacity:0.35"></div>'
                )
            ui.html(
                f'<div style="position:absolute;inset:0;background:linear-gradient(90deg, rgba(10,10,10,0.95) 0%, rgba(10,10,10,0.70) 45%, rgba(10,10,10,0.85) 100%);"></div>'
            )

            with ui.row().classes("w-full items-start no-wrap").style("gap: 16px; padding: 12px 16px"):
                with ui.element("div").style("width: 210px; min-width: 210px"):
                    if poster_url:
                        ui.image(poster_url).style("width: 210px; height: 315px; object-fit: cover; border-radius: 8px")
                    else:
                        with ui.element("div").style(
                            f"width:210px;height:315px;border-radius:8px;background:{COLORS['surface_light']};"
                            "display:flex;align-items:center;justify-content:center"
                        ):
                            ui.icon("movie").classes("text-5xl").style(f"color:{COLORS['text_muted']}")

                with ui.column().classes("gap-2").style("z-index:2; padding: 4px 4px 4px 0"):
                    ui.label(media_title).classes("text-3xl font-bold").style(f"color:{COLORS['text']}")
                    release_text = (
                        f"{release_label}: {release_date}" if release_label else str(release_date)
                    )
                    ui.label(release_text).classes("text-sm").style(f"color:{COLORS['text_muted']}")
                    ui.label(f"{path_label}: {media_path or 'N/A'}").classes("text-sm").style(
                        f"color:{COLORS['text_muted']}; max-width: 960px;"
                    )
                    if description:
                        ui.label(description).classes("text-sm").style(
                            f"color:{COLORS['text']}; max-width: 960px;"
                        )
                    ui.label(f"IMDB: {imdb_id}").classes("text-xs").style(f"color:{COLORS['text_muted']}")

        with ui.card().classes("w-full p-4").style("border-radius: 0; margin-top: 0"):
            content_renderer()


def format_date_display(value):
    from datetime import datetime

    raw = (value or "").strip()
    if not raw or raw == "N/A":
        return "N/A"

    for candidate in (raw[:10], raw):
        try:
            parsed = datetime.strptime(candidate, "%Y-%m-%d")
            return parsed.strftime("%d %B %Y")
        except Exception:
            continue
    return raw


async def open_quick_scrape_popup(
    app_state,
    *,
    imdb_id,
    media_type,
    title="",
    poster_url="",
    season=None,
    episode=None,
):
    from webapp.pages.scraper_page import _render_scrape_results, _run_scrape

    normalized_imdb = normalize_imdb_id(imdb_id) or imdb_id
    is_series = media_type in ("series", "show", "anime_show")

    if is_series and season is not None and episode is not None:
        scrape_query = f"{normalized_imdb}:{int(season)}:{int(episode)}"
        scrape_altquery = f"{normalized_imdb} S{int(season):02d}E{int(episode):02d}"
        meta_type = "show"
    else:
        scrape_query = normalized_imdb
        scrape_altquery = None
        meta_type = "movie"

    search_state = {
        "query": scrape_query,
        "imdb_id": normalized_imdb,
        "media_type": meta_type,
        "cinemeta_name": title,
        "poster_url": poster_url,
        "meta_year": "",
        "meta_genres": [],
        "meta_type": meta_type,
        "tmdb_id": None,
    }

    with ui.dialog() as dlg, ui.card().classes("w-full p-0").style(
        f"background: {COLORS['background']}; max-width: 960px; width: 92vw; max-height: 85vh; overflow: hidden"
    ):
        with ui.row().classes("items-center justify-between w-full px-4 py-3").style(
            f"border-bottom: 1px solid {COLORS['surface_light']}"
        ):
            with ui.row().classes("items-center gap-3"):
                if poster_url:
                    ui.image(poster_url).style(
                        "width: 34px; height: 50px; object-fit: cover; border-radius: 4px"
                    )
                with ui.column().classes("gap-0"):
                    ui.label(title or normalized_imdb).classes("text-sm font-semibold").style(
                        f"color: {COLORS['text']}"
                    )
                    ui.label(scrape_query).classes("text-xs").style(f"color: {COLORS['text_muted']}")
            ui.button(icon="close", on_click=dlg.close).props("flat round color=grey")

        with ui.column().classes("w-full p-4 gap-3 overflow-auto").style("max-height: calc(85vh - 64px)"):
            spinner = ui.spinner("dots", size="md", color="amber")
            results_container = ui.column().classes("w-full gap-3")

            async def _load_results():
                spinner.visible = True
                results_container.clear()
                try:
                    scraped = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: _run_scrape(
                            app_state,
                            scrape_query,
                            search_state,
                            scrape_altquery=scrape_altquery,
                        ),
                    )
                    search_state["results"] = scraped
                    results_container.clear()
                    with results_container:
                        if scraped:
                            _render_scrape_results(app_state, scraped, search_state, results_container)
                        else:
                            ui.label("No releases found.").classes("text-sm").style(
                                f"color: {COLORS['text_muted']}"
                            )
                except Exception as exc:
                    logger.exception("Quick scrape popup failed")
                    results_container.clear()
                    with results_container:
                        ui.label(f"Error: {exc}").classes("text-sm").style(
                            f"color: {COLORS['error']}"
                        )
                finally:
                    spinner.visible = False

            await _load_results()

    dlg.open()


def render_settings_button(*, on_delete_local, on_delete_both):
    with ui.dialog() as dlg, ui.card().classes("p-4").style(
        f"background: {COLORS['surface']}; min-width: 320px"
    ):
        ui.label("Episode/Movie actions").classes("text-base font-semibold")
        ui.label("Delete options").classes("text-sm").style(f"color:{COLORS['text_muted']}")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat color=grey")

            async def _delete_local(_):
                dlg.close()
                await on_delete_local()

            async def _delete_both(_):
                dlg.close()
                await on_delete_both()

            ui.button("Delete local + DB", on_click=_delete_local).props("color=red")
            ui.button("Delete local + Plex", on_click=_delete_both).props("color=red")

    ui.button(icon="delete").props("flat dense color=red size=sm").on("click", lambda _: dlg.open())


def render_action_icon(*, icon, color, on_click, enabled=True):
    btn = ui.button(icon=icon).props(f"flat dense color={color} size=sm")
    btn.on("click", on_click)
    if not enabled:
        btn.disable()
