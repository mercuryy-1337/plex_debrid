"""
Search results page for pd_reloaded.

Displays cinemeta search results grouped by Movies and Series.
Allows the user to select a title and proceed to manual scraping / download.
"""

import asyncio
import logging
import copy
import requests
import regex
from threading import Thread
from nicegui import ui, Client

from webapp.components import page_header, empty_state
from webapp.theme import COLORS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Metadata helpers (kept from the original scraper page)
# ═══════════════════════════════════════════════════════════════════

def _lookup_metadata(query, media_type="auto"):
    """Look up IMDB/TMDB IDs and metadata (poster, year, genres) using cinemeta."""
    result = {
        "imdb_id": None, "tmdb_id": None, "poster_url": None,
        "year": None, "genres": [], "meta_type": None, "name": None,
    }

    def _extract(meta, kind):
        r = {}
        r["imdb_id"] = meta.get("imdb_id") or meta.get("id")
        tmdb = meta.get("moviedb_id") or meta.get("tmdb_id")
        r["tmdb_id"] = str(tmdb) if tmdb else None
        r["poster_url"] = meta.get("poster")
        r["genres"] = meta.get("genres", [])
        r["meta_type"] = kind
        r["name"] = meta.get("name")
        release_info = meta.get("releaseInfo", "")
        try:
            r["year"] = int(release_info[:4]) if release_info else None
        except (ValueError, TypeError):
            r["year"] = None
        return r

    imdb_match = regex.search(r'(tt\d{7,})', query, regex.I)
    if imdb_match:
        imdb_id = imdb_match.group(1)
        for kind, url in [
            ("series", f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json"),
            ("movie", f"https://cinemeta-live.strem.io/meta/movie/{imdb_id}.json"),
        ]:
            try:
                resp = requests.get(url, timeout=6)
                if resp.ok:
                    meta = resp.json().get("meta", {})
                    if meta:
                        return _extract(meta, kind)
            except Exception:
                pass
        result["imdb_id"] = imdb_id
        return result

    search_order = ["movie", "series"]
    if media_type in ("show", "anime_show"):
        search_order = ["series", "movie"]

    for kind in search_order:
        try:
            url = (f"https://v3-cinemeta.strem.io/catalog/{kind}/top/"
                   f"search={requests.utils.quote(query)}.json")
            resp = requests.get(url, timeout=6)
            if resp.ok:
                metas = resp.json().get("metas", [])
                if metas:
                    best = metas[0]
                    best_id = best.get("imdb_id") or best.get("id")
                    if best_id:
                        meta_url = (
                            f"https://v3-cinemeta.strem.io/meta/series/{best_id}.json"
                            if kind == "series"
                            else f"https://cinemeta-live.strem.io/meta/movie/{best_id}.json"
                        )
                        try:
                            mresp = requests.get(meta_url, timeout=6)
                            if mresp.ok:
                                meta = mresp.json().get("meta", {})
                                if meta:
                                    return _extract(meta, kind)
                        except Exception:
                            pass
                    partial = {"imdb_id": best_id, "tmdb_id": None, "meta_type": kind}
                    partial["poster_url"] = best.get("poster")
                    partial["genres"] = best.get("genres", [])
                    partial["year"] = None
                    try:
                        partial["year"] = int(best.get("releaseInfo", "")[:4])
                    except Exception:
                        pass
                    tmdb = best.get("moviedb_id") or best.get("tmdb_id")
                    partial["tmdb_id"] = str(tmdb) if tmdb else None
                    return partial
        except Exception:
            continue

    return result


def _is_anime_by_xml(title):
    """Check if a title belongs to anime via the animetitles XML."""
    from webapp.anime_check import is_anime
    return is_anime(title)


# ═══════════════════════════════════════════════════════════════════
# Page renderer
# ═══════════════════════════════════════════════════════════════════

async def render(app_state, client: Client, search_query: str = ""):
    """Render the search-results page.

    *search_query* is passed by the SPA router when the user submits a
    search from the header bar.  If empty the page shows a prompt.
    """
    from webapp.cinemeta_feed import search_grouped

    with ui.column().classes("p-6 gap-6 w-full"):
        if not search_query:
            page_header("Search Results", "Use the search bar above to find movies and shows")
            empty_state("search", "Type a title in the search bar and press Enter")
            return

        page_header("Search Results", f"Showing results for \"{search_query}\"")

        # Run feed search in executor so UI stays responsive
        grouped = await asyncio.get_event_loop().run_in_executor(
            None, lambda: search_grouped(search_query, limit_per_type=20)
        )
        movies = grouped.get("movie", [])
        series = grouped.get("series", [])

        if not movies and not series:
            empty_state("search_off", f"No results found for \"{search_query}\"")
            return

        # ── Movies section ───────────────────────────────────
        if movies:
            with ui.row().classes("items-center gap-2 mt-2"):
                ui.icon("movie").classes("text-xl").style(f"color: {COLORS['primary']}")
                ui.label(f"Movies ({len(movies)})").classes(
                    "text-lg font-bold").style(f"color: {COLORS['text']}")

            with ui.row().classes("w-full flex-wrap gap-4"):
                for item in movies:
                    _render_result_card(app_state, client, item, "movie")

        # ── Series section ───────────────────────────────────
        if series:
            with ui.row().classes("items-center gap-2 mt-6"):
                ui.icon("tv").classes("text-xl").style(f"color: {COLORS['primary']}")
                ui.label(f"Series ({len(series)})").classes(
                    "text-lg font-bold").style(f"color: {COLORS['text']}")

            with ui.row().classes("w-full flex-wrap gap-4"):
                for item in series:
                    _render_result_card(app_state, client, item, "series")


# ═══════════════════════════════════════════════════════════════════
# Result card
# ═══════════════════════════════════════════════════════════════════

def _render_result_card(app_state, client, item, media_type):
    """Render a single result card (poster + info + scrape button)."""
    name = item.get("name", "Unknown")
    year = item.get("releaseInfo", "")
    poster = item.get("poster", "")
    imdb_id = item.get("id", "")
    rating = item.get("imdbRating", "")
    type_label = "Movie" if media_type == "movie" else "Series"
    badge_class = "badge-movie" if media_type == "movie" else "badge-show"

    ic = "movie" if media_type == "movie" else "tv"
    # Placeholder sits behind the <img>; on error we just hide the img.
    placeholder_bg = (
        f'<div style="position:absolute;inset:0;display:flex;align-items:center;'
        f'justify-content:center;background:{COLORS["surface_light"]};">'
        f'<span class="material-icons" style="font-size:3rem;color:{COLORS["text_muted"]}">{ic}</span></div>'
    )

    with ui.card().style(
        f"width: 185px; height: 395px; overflow: hidden; padding: 0; margin: 0;"
        f" border-radius: 8px; background: {COLORS['surface']};"
        " display: flex; flex-direction: column; gap: 0;"
    ).classes("content-card"):
        # Poster — fixed height container with placeholder behind image
        if poster:
            ui.html(
                f'<div style="position:relative;width:185px;height:260px;overflow:hidden;">'
                f'{placeholder_bg}'
                f'<img src="{poster}" '
                f'onerror="this.style.display=\'none\'" '
                f'style="position:relative;width:185px;height:260px;object-fit:cover;display:block;" />'
                f'</div>'
            ).style("width: 185px; height: 260px; flex-shrink: 0; line-height: 0; padding: 0; overflow: hidden;")
        else:
            ui.html(
                f'<div style="position:relative;width:185px;height:260px;">'
                f'{placeholder_bg}</div>'
            ).style("width: 185px; height: 260px; flex-shrink: 0; line-height: 0; padding: 0;")

        # Info + button section
        with ui.element("div").style(
            "padding: 8px 10px; display: flex; flex-direction: column;"
            " justify-content: space-between; flex: 1; gap: 0;"
        ):
            # Top: metadata
            with ui.element("div").style("display: flex; flex-direction: column; gap: 3px;"):
                with ui.element("div").style("display: flex; align-items: center; gap: 6px;"):
                    ui.html(f'<span class="{badge_class}">{type_label}</span>')
                    if year:
                        ui.html(
                            f'<span style="color:{COLORS["text_muted"]};font-size:0.75rem;">{year}</span>'
                        )

                ui.html(
                    f'<div style="color:{COLORS["text"]};font-size:0.85rem;font-weight:600;'
                    f'line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
                    f'max-width:165px;">{name}</div>'
                )

                if imdb_id:
                    ui.html(
                        f'<span style="color:{COLORS["primary"]};font-size:0.7rem;'
                        f'background:{COLORS["primary"]}18;border-radius:3px;'
                        f'padding:1px 6px;">{imdb_id}</span>'
                    )

            # Bottom: scrape button — raw div, fixed width
            _card_spinner = ui.spinner("dots", size="sm", color="amber")
            _card_spinner.visible = False

            async def _scrape(
                _,
                _imdb=imdb_id, _name=name, _type=media_type,
                _poster=poster, _year=year, _rating=rating,
                _sp=_card_spinner,
            ):
                _sp.visible = True
                try:
                    await _open_scrape_dialog(
                        app_state, client,
                        imdb_id=_imdb, title=_name, media_type=_type,
                        poster_url=_poster, year=_year, rating=_rating,
                    )
                finally:
                    _sp.visible = False

            ui.button("Scrape", icon="search", on_click=_scrape).props(
                "color=amber push dense size=sm no-caps"
            ).style("width: 161px; font-size: 0.75rem;")


# ═══════════════════════════════════════════════════════════════════
# Scrape dialog (replaces old full-page scraper)
# ═══════════════════════════════════════════════════════════════════

async def _open_scrape_dialog(app_state, client, *, imdb_id, title, media_type,
                               poster_url="", year="", rating=""):
    """Open a full-screen dialog that scrapes for the selected title."""
    search_state = {
        "query": imdb_id or title,
        "imdb_id": imdb_id,
        "media_type": "show" if media_type == "series" else "movie",
        "cinemeta_name": title,
        "poster_url": poster_url,
        "meta_year": year,
        "meta_genres": [],
        "meta_type": media_type,
        "tmdb_id": None,
    }

    with ui.dialog().props("maximized") as dlg, ui.card().classes(
        "w-full h-full p-0"
    ).style(f"background: {COLORS['background']}; max-width: 1200px; margin: auto"):
        # ── Header ───────────────────────────────────────────
        with ui.row().classes("items-center justify-between w-full px-6 py-4").style(
            f"border-bottom: 1px solid {COLORS['surface_light']}"
        ):
            with ui.row().classes("items-center gap-4"):
                if poster_url:
                    ui.image(poster_url).style(
                        "width: 40px; height: 58px; object-fit: cover; border-radius: 0")
                with ui.column().classes("gap-0"):
                    ui.label(title).classes("text-lg font-bold").style(f"color: {COLORS['text']}")
                    with ui.row().classes("items-center gap-2"):
                        tl = "Movie" if media_type == "movie" else "Series"
                        ui.label(tl).classes("text-xs font-medium").style(
                            f"color: {COLORS['primary']}")
                        if year:
                            ui.label(f"• {year}").classes("text-xs").style(
                                f"color: {COLORS['text_muted']}")
                        if rating:
                            ui.html(f'<span style="color:#F59E0B;font-size:0.75rem">★ {rating}</span>')
                        if imdb_id:
                            ui.label(imdb_id).classes("text-xs").style(
                                f"color: {COLORS['text_muted']}")

            ui.button(icon="close", on_click=dlg.close).props("flat round color=grey")

        # ── Body ─────────────────────────────────────────────
        with ui.column().classes("w-full flex-1 overflow-auto p-6 gap-4"):
            with ui.card().classes("w-full p-4"):
                with ui.row().classes("items-center gap-4 w-full flex-wrap"):
                    versions = app_state.db.get_release_versions(enabled_only=True)
                    version_options = {"none": "No version filter"}
                    for v in versions:
                        version_options[str(v["id"])] = v["name"]

                    def _on_version_change(e):
                        search_state["version"] = e.value
                        if e.value and e.value != "none":
                            scrape_btn.enable()
                        else:
                            scrape_btn.disable()

                    ui.select(
                        version_options, value="none", label="Release Version",
                        on_change=_on_version_change,
                    ).classes("min-w-48").props("outlined dense dark color=amber")

                    scrape_btn = ui.button("Filter Results", icon="filter_list").props("color=amber push")
                    scrape_btn.disable()
                    spinner = ui.spinner("dots", size="lg", color="amber").classes("ml-2")
                    spinner.visible = False

            results_container = ui.column().classes("w-full gap-4")

            async def do_scrape():
                spinner.visible = True
                scrape_btn.disable()
                results_container.clear()
                with results_container:
                    ui.label("Scraping...").classes("text-sm").style(
                        f"color: {COLORS['text_muted']}")

                try:
                    scrape_query = imdb_id or title
                    scraped = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: _run_scrape(app_state, scrape_query, search_state))
                    search_state["results"] = scraped
                    results_container.clear()
                    with results_container:
                        if scraped:
                            _render_scrape_results(app_state, scraped, search_state, results_container)
                        else:
                            empty_state("search_off", "No releases found.")
                except Exception as e:
                    logger.exception("Scrape error")
                    results_container.clear()
                    with results_container:
                        ui.label(f"Error: {e}").classes("text-sm").style(
                            f"color: {COLORS['error']}")
                finally:
                    spinner.visible = False
                    # Only re-enable if a version is selected
                    if search_state.get("version") and search_state["version"] != "none":
                        scrape_btn.enable()

            scrape_btn.on("click", do_scrape)
            await do_scrape()  # auto-scrape on open

    dlg.open()


# ═══════════════════════════════════════════════════════════════════
# Scraping helpers
# ═══════════════════════════════════════════════════════════════════

def _run_scrape(app_state, query, search_state):
    """Run the scraping process in a background thread."""
    try:
        from webapp.automation import AutomationEngine
        engine = AutomationEngine(app_state)
        engine._load_settings_into_modules()

        import scraper
        import releases

        media_type = search_state.get("media_type", "auto")

        scraped_releases = scraper.scrape(query)
        if not scraped_releases:
            return []

        # Resolve IMDB/TMDB IDs and metadata
        try:
            meta = _lookup_metadata(query, media_type)
            search_state["imdb_id"] = meta.get("imdb_id")
            search_state["tmdb_id"] = meta.get("tmdb_id")
            search_state["poster_url"] = meta.get("poster_url")
            search_state["meta_year"] = meta.get("year")
            search_state["meta_genres"] = meta.get("genres", [])
            search_state["meta_type"] = meta.get("meta_type")
            search_state["cinemeta_name"] = meta.get("name")
            if meta.get("imdb_id"):
                logger.info("Resolved metadata for '%s': IMDB=%s TMDB=%s",
                            query, meta["imdb_id"], meta["tmdb_id"])
        except Exception:
            logger.debug("Failed to resolve metadata")

        # Apply version sorting
        version_id = search_state.get("version")
        if version_id and version_id != "none":
            versions_data = app_state.db.get_release_versions(enabled_only=True)
            for v in versions_data:
                if str(v["id"]) == version_id:
                    version_obj = releases.sort.version(
                        v["name"], v.get("triggers", []),
                        v.get("language", "en"), v.get("rules", []))
                    releases.sort(scraped_releases, version_obj)
                    break

        result = []
        for r in scraped_releases:
            result.append({
                "title": r.title,
                "source": r.source,
                "size": r.size,
                "seeders": getattr(r, "seeders", 0),
                "resolution": getattr(r, "resolution", ""),
                "hash": getattr(r, "hash", ""),
                "download": getattr(r, "download", []),
                "files": getattr(r, "files", []),
                "wanted": getattr(r, "wanted", 0),
                "unwanted": getattr(r, "unwanted", 0),
                "_release_obj": r,
            })
        return result

    except Exception as e:
        logger.exception("Scrape execution error")
        return []


def _is_season_pack(title: str) -> bool:
    """Detect if a release title looks like a season pack (not a single episode)."""
    t = title.upper()
    has_single_ep = bool(regex.search(r'S\d{1,2}[\s.]?E\d{1,3}(?!\s?-\s?E?\d)', t))
    # Also detect "Season 2 - 06" / "Season.2.-.12" style individual episodes
    if not has_single_ep:
        has_single_ep = bool(regex.search(r'SEASON[\s.]*\d+[\s.]*-[\s.]*\d+', t))
    # Episode ranges like S02E01-12 or S02E01-E12
    if regex.search(r'S\d{1,2}[\s.]?E\d{1,3}\s?-\s?E?\d{1,3}', t):
        return True
    # Has S01 but no episode number → season pack
    if regex.search(r'S\d{1,2}(?![\s.]?E\d)', t) and not has_single_ep:
        return True
    # Explicit markers — but only if there's no individual episode pattern
    if not has_single_ep and regex.search(r'(?:SEASON|COMPLETE|FULL\.SEASON)', t):
        return True
    return False


def _render_scrape_results(app_state, results, search_state, container):
    """Render scrape results as a list with download buttons."""
    with container:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between p-4"):
                ui.label(f"Results ({len(results)} releases)").classes(
                    "text-lg font-semibold").style(f"color: {COLORS['text']}")

                async def auto_download():
                    if results:
                        best = max(results, key=lambda r: r.get("seeders", 0) or 0)
                        await _download_release(app_state, best, search_state, stream=False)
                    else:
                        ui.notify("No releases available", type="warning")

                ui.button("Download Best", on_click=auto_download,
                          icon="auto_awesome").props("color=amber push")

            for release in results:
                media_type = search_state.get("media_type", "auto")
                is_pack = (media_type != "movie"
                           and _is_season_pack(release["title"]))
                border_color = "#3B82F6" if is_pack else COLORS['primary']
                with ui.card().classes("w-full p-3").style(
                    f"background: {COLORS['surface_light']}; "
                    f"border-left: 3px solid {border_color}"
                ):
                    with ui.row().classes("items-start justify-between w-full gap-4"):
                        with ui.column().classes("flex-1 gap-1"):
                            with ui.row().classes("items-center gap-2"):
                                title_text = release["title"]
                                if len(title_text) > 100:
                                    title_text = title_text[:100] + "..."
                                ui.label(title_text).classes("text-sm font-medium").style(
                                    f"color: {COLORS['text']}")
                                if is_pack:
                                    ui.html(
                                        '<span style="background: #3B82F6; color: #fff; '
                                        'font-size: 0.6rem; font-weight: 700; padding: 1px 6px; '
                                        'letter-spacing: 0.04em; white-space: nowrap">'
                                        'SEASON PACK</span>'
                                    )

                            with ui.row().classes("items-center gap-3 flex-wrap"):
                                ui.label(f"📡 {release['source']}").classes("text-xs").style(
                                    f"color: {COLORS['text_muted']}")
                                size_gb = (release["size"] / 1024
                                           if release["size"] > 100
                                           else release["size"])
                                ui.label(f"💾 {size_gb:.1f} GB").classes("text-xs").style(
                                    f"color: {COLORS['text_muted']}")
                                if release.get("resolution"):
                                    ui.label(f"📺 {release['resolution']}p").classes(
                                        "text-xs").style(f"color: {COLORS['text_muted']}")
                                if release.get("seeders"):
                                    ui.label(f"🌱 {release['seeders']}").classes(
                                        "text-xs").style(f"color: {COLORS['text_muted']}")

                        with ui.column().classes("gap-1"):
                            async def dl_release(r=release):
                                await _download_release(app_state, r, search_state, stream=False)

                            ui.button("Download", on_click=dl_release,
                                      icon="download").props(
                                "color=amber push dense size=sm").classes("text-xs")


# ═══════════════════════════════════════════════════════════════════
# Download flow (unchanged from original scraper page)
# ═══════════════════════════════════════════════════════════════════

async def _download_release(app_state, release_data, search_state, stream=True):
    """Download a selected release via Decypharr — always requires version selection."""
    try:
        release = release_data.get("_release_obj")
        if not release:
            ui.notify("Release object not available", type="negative")
            return

        versions = app_state.db.get_release_versions()
        enabled_versions = [v for v in versions if v.get("enabled")]

        if not enabled_versions:
            ui.notify("No enabled versions found. Configure versions in Settings first.",
                      type="negative")
            return

        with ui.dialog() as ver_dlg, ui.card().classes("p-4").style(
            f"background: {COLORS['surface']}; min-width: 400px"
        ):
            ui.label("Select Version").classes("text-base font-bold mb-1").style(
                f"color: {COLORS['primary']}")
            ui.label("Choose which version profile to use for this download:").classes(
                "text-xs mb-3").style(f"color: {COLORS['text_muted']}")

            for v in enabled_versions:
                ver_name = v.get("name", "Unknown")
                cat = v.get("category", "default")

                async def pick_version(version=v):
                    ver_dlg.close()
                    ui.notify(
                        f"Sending via {version['name']} "
                        f"(category: {version.get('category', 'default')})",
                        type="info")
                    await _proceed_download(
                        app_state, release, release_data, search_state, version, stream)

                with ui.row().classes(
                    "items-center gap-3 w-full cursor-pointer p-3 rounded"
                ).style(
                    f"background: {COLORS['surface_light']}; margin-bottom: 4px"
                ).on("click", pick_version):
                    ui.icon("label").style(f"color: {COLORS['primary']}")
                    with ui.column().classes("gap-0"):
                        ui.label(ver_name).classes("text-sm font-medium").style(
                            f"color: {COLORS['text']}")
                        ui.label(f"Category: {cat}").classes("text-xs").style(
                            f"color: {COLORS['text_muted']}")

            ui.button("Cancel", on_click=ver_dlg.close).props("flat color=grey").classes("mt-2")
        ver_dlg.open()

    except Exception as e:
        logger.exception("Download error")
        ui.notify(f"Download error: {e}", type="negative")


async def _proceed_download(app_state, release, release_data, search_state,
                             version, stream):
    """Execute the download via Decypharr after version is selected."""
    try:
        query = search_state.get("query", "")
        media_type = search_state.get("media_type", "auto")
        category = version.get("category", "default")
        ver_name = version.get("name", "unknown")

        if media_type == "auto":
            if regex.search(
                r'(S[0-9]+|SEASON|E[0-9]+|EPISODE|[0-9]+-[0-9]+)',
                release.title, regex.I,
            ):
                detected_type = "show"
            else:
                detected_type = "movie"
        elif media_type in ("anime_show", "show"):
            detected_type = "show"
        else:
            detected_type = "movie"

        cinemeta_name = search_state.get("cinemeta_name") or query
        if detected_type == "show" and cinemeta_name and _is_anime_by_xml(cinemeta_name):
            detected_type = "anime_show"
        elif media_type == "anime_show":
            detected_type = "anime_show"

        release.type = detected_type
        release.Releases = [release]

        cinemeta_name = search_state.get("cinemeta_name") or query or release.title
        imdb_id = search_state.get("imdb_id") or ""

        # ── Create activity immediately so it appears on the dashboard ──
        activity_id = app_state.add_activity(
            title=cinemeta_name,
            release_title=release.title,
            info_hash="",
            category=category,
            imdb_id=imdb_id,
        )

        dl_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _execute_download(
                app_state, release, stream, cinemeta_name,
                media_type, version, imdb_id=imdb_id,
                activity_id=activity_id),
        )

        dl_success = (dl_result.get("success", False)
                      if isinstance(dl_result, dict) else bool(dl_result))
        dl_error = (dl_result.get("error")
                    if isinstance(dl_result, dict) else None)

        if dl_success:
            final_type = media_type if media_type != "auto" else detected_type
            tmdb_id = search_state.get("tmdb_id")

            poster_url = None
            meta_year = None
            meta_genres = []
            if not imdb_id:
                try:
                    meta = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: _lookup_metadata(query, final_type))
                    imdb_id = meta.get("imdb_id")
                    tmdb_id = meta.get("tmdb_id")
                    poster_url = meta.get("poster_url")
                    meta_year = meta.get("year")
                    meta_genres = meta.get("genres", [])
                except Exception:
                    pass

            poster_url = search_state.get("poster_url") or poster_url
            meta_year = search_state.get("meta_year") or meta_year
            meta_genres = search_state.get("meta_genres") or meta_genres

            app_state.db.add_download_log(
                title=cinemeta_name,
                release_title=release.title,
                media_type=final_type,
                imdb_id=imdb_id,
                tmdb_id=tmdb_id,
                debrid_service=f"decypharr/{ver_name} ({category})",
                scraper_source=release.source,
                resolution=str(getattr(release, "resolution", "")),
                size_gb=release.size / 1024 if release.size > 100 else release.size,
                status="completed",
            )

            try:
                app_state.db.upsert_content_item(
                    imdb_id=imdb_id,
                    tmdb_id=tmdb_id,
                    title=cinemeta_name,
                    media_type=final_type,
                    year=meta_year,
                    genres=meta_genres if meta_genres else [],
                    poster_url=poster_url,
                    status="collected",
                    source="manual_scrape",
                )
            except Exception:
                logger.debug("Failed to upsert content item for manual download")
        else:
            reason = dl_error or "Unknown error"
            ui.notify(f"Download failed: {reason}", type="negative", close_button=True)
            app_state.db.add_download_log(
                title=cinemeta_name,
                release_title=release.title,
                media_type=media_type if media_type != "auto" else detected_type,
                imdb_id=search_state.get("imdb_id"),
                tmdb_id=search_state.get("tmdb_id"),
                debrid_service=f"decypharr/{ver_name} ({category})",
                scraper_source=getattr(release, "source", "unknown"),
                status=f"failed: {reason}",
            )

    except Exception as e:
        logger.exception("Download error")
        ui.notify(f"Download error: {e}", type="negative")


def _execute_download(app_state, release, stream, cinemeta_name,
                       media_type, version, imdb_id="", activity_id=None):
    """Execute the download via Decypharr using global API key.

    Tracks the download lifecycle on app_state's activity queue:
    sent → sent_to_decypharr → downloading → downloaded → processing → (removed).
    """
    try:
        from webapp.decypharr import DecypharrClient, TorrentState

        decypharr_url = app_state.db.get_setting("Decypharr Base URL", "")
        decypharr_username = app_state.db.get_setting("Decypharr Username", "")
        api_key = app_state.db.get_setting("Decypharr API Key", "")

        if not decypharr_url:
            return {"success": False, "error": "Decypharr URL not configured"}
        if not api_key:
            return {"success": False, "error": "No Decypharr API Key configured"}

        category = version.get("category", "default")
        client = DecypharrClient(
            decypharr_url, username=decypharr_username, password=api_key)

        download_attr = (getattr(release, "download", None)
                         or getattr(release, "magnet", None))
        if isinstance(download_attr, list):
            magnet = download_attr[0] if download_attr else None
        else:
            magnet = download_attr
        info_hash = (getattr(release, "hash", None)
                     or getattr(release, "infoHash", None))

        if not info_hash and magnet and magnet.startswith("magnet:"):
            import re as _re
            m = _re.search(r'btih:([a-fA-F0-9]{40})', magnet)
            if m:
                info_hash = m.group(1).lower()
            else:
                m = _re.search(r'btih:([a-fA-F0-9]{32})', magnet)
                if m:
                    info_hash = m.group(1).lower()

        logger.info("Download attempt: magnet=%s info_hash=%s category=%s",
                     bool(magnet), info_hash[:16] if info_hash else None, category)

        if magnet and magnet.startswith("magnet:"):
            result = client.download_magnet(magnet, category=category)
        elif info_hash:
            result = client.download_hash(info_hash, category=category)
        elif magnet:
            result = client.add_torrent_url(magnet, category=category)
        else:
            return {"success": False, "error": "No magnet link or info hash found"}

        if not result:
            return {
                "success": False,
                "error": (f"Decypharr rejected the torrent "
                          f"(version={version.get('name', '?')}, category={category})"),
            }

        logger.info("Decypharr download sent: %s → version=%s category=%s",
                     release.title[:60], version.get("name", "?"), category)

        # ── Activity tracking ────────────────────────────────
        # Update the pre-created activity now that Decypharr has accepted
        if activity_id:
            app_state.update_activity(
                activity_id, status="sent_to_decypharr",
                info_hash=info_hash or "")

        out = {
            "success": True, "error": None,
            "sent_title": release.title, "poll_completed": False,
        }

        if info_hash:
            def _on_progress(state, progress):
                if TorrentState.is_downloading(state):
                    app_state.update_activity(
                        activity_id, status="downloading", progress=progress)
                elif TorrentState.is_completed(state):
                    app_state.update_activity(
                        activity_id, status="downloaded", progress=1.0)

            try:
                poll_result = client.wait_for_completion(
                    info_hash, timeout=300, poll_interval=5,
                    remove_on_complete=True,
                    progress_callback=_on_progress,
                )
                if poll_result["success"]:
                    logger.info("Torrent completed and removed: %s", info_hash[:16])
                    out["poll_completed"] = True
                    app_state.update_activity(
                        activity_id, status="processing", progress=1.0)
                    import time as _time
                    _time.sleep(2)
                    app_state.update_activity(
                        activity_id, status="completed", progress=1.0)
                    _time.sleep(5)
                    app_state.remove_activity(activity_id)
                    activity_id = None
                else:
                    logger.warning("Torrent poll ended: %s (state: %s)",
                                   info_hash[:16], poll_result["state"])
            except Exception as poll_err:
                logger.debug("Torrent polling error (non-fatal): %s", poll_err)
        else:
            logger.info("No info_hash — skipping completion polling")
            app_state.update_activity(
                activity_id, status="completed", progress=1.0)
            import time as _time
            _time.sleep(5)
            app_state.remove_activity(activity_id)
            activity_id = None

        return out

    except Exception as e:
        logger.exception("Download execution error")
        return {"success": False, "error": str(e)}
    finally:
        if activity_id:
            try:
                app_state.remove_activity(activity_id)
            except Exception:
                pass
