"""
Manual scraper page for pd_reloaded.
Allows searching, filtering, and downloading releases with type selection.
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


def _lookup_metadata(query, media_type="auto"):
    """Look up IMDB/TMDB IDs and metadata (poster, year, genres) using cinemeta.

    Returns a dict with keys: imdb_id, tmdb_id, poster_url, year, genres, meta_type.
    """
    result = {"imdb_id": None, "tmdb_id": None, "poster_url": None, "year": None, "genres": [], "meta_type": None, "name": None}

    def _extract(meta, kind):
        r = {}
        r["imdb_id"] = meta.get("imdb_id") or meta.get("id")
        tmdb = meta.get("moviedb_id") or meta.get("tmdb_id")
        r["tmdb_id"] = str(tmdb) if tmdb else None
        r["poster_url"] = meta.get("poster")
        r["genres"] = meta.get("genres", [])
        r["meta_type"] = kind  # "movie" or "series"
        r["name"] = meta.get("name")
        release_info = meta.get("releaseInfo", "")
        try:
            r["year"] = int(release_info[:4]) if release_info else None
        except (ValueError, TypeError):
            r["year"] = None
        return r

    # If the query is already an IMDB ID, fetch meta directly
    imdb_match = regex.search(r'(tt\d{7,})', query, regex.I)
    if imdb_match:
        imdb_id = imdb_match.group(1)
        # Series endpoint
        meta_endpoints = [
            ("series", f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json"),
            ("movie", f"https://cinemeta-live.strem.io/meta/movie/{imdb_id}.json"),
        ]
        for kind, url in meta_endpoints:
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

    # Search by title
    search_order = ["movie", "series"]
    if media_type in ("show", "anime_show"):
        search_order = ["series", "movie"]

    for kind in search_order:
        try:
            url = f"https://v3-cinemeta.strem.io/catalog/{kind}/top/search={requests.utils.quote(query)}.json"
            resp = requests.get(url, timeout=6)
            if resp.ok:
                data = resp.json()
                metas = data.get("metas", [])
                if metas:
                    best = metas[0]
                    best_id = best.get("imdb_id") or best.get("id")
                    # Fetch full meta for poster/genres/year
                    if best_id:
                        meta_url = (f"https://v3-cinemeta.strem.io/meta/series/{best_id}.json"
                                    if kind == "series"
                                    else f"https://cinemeta-live.strem.io/meta/movie/{best_id}.json")
                        try:
                            mresp = requests.get(meta_url, timeout=6)
                            if mresp.ok:
                                meta = mresp.json().get("meta", {})
                                if meta:
                                    return _extract(meta, kind)
                        except Exception:
                            pass
                    # Fallback: use catalog result directly
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
    """Check if a title belongs to anime via the animetitles XML (case-insensitive)."""
    from webapp.anime_check import is_anime
    return is_anime(title)


async def render(app_state, client: Client):

    with ui.column().classes("p-6 gap-6 w-full"):
        page_header("Manual Scraper", "Search for and download content manually")

        # ─── Search Controls ────────────────────────────────
        search_state = {
            "query": "",
            "media_type": "auto",
            "version": None,
            "results": [],
            "searching": False,
        }

        with ui.card().classes("w-full p-4"):
            with ui.row().classes("items-center gap-4 w-full flex-wrap"):
                query_input = ui.input(
                    "Enter search query (title or IMDB ID)...",
                    on_change=lambda e: search_state.update({"query": e.value}),
                ).classes("flex-1 min-w-64").props("outlined dark color=amber")

                # Media type selector
                type_select = ui.select(
                    {
                        "auto": "Auto Detect",
                        "movie": "Movie",
                        "show": "Show",
                        "anime_show": "Anime Show",
                    },
                    value="auto",
                    label="Content Type",
                    on_change=lambda e: search_state.update({"media_type": e.value}),
                ).classes("min-w-40").props("outlined dense dark color=amber")

            # Version selector
            with ui.row().classes("items-center gap-4 w-full mt-2"):
                versions = app_state.db.get_release_versions(enabled_only=True)
                version_options = {"none": "No version filter"}
                for v in versions:
                    version_options[str(v["id"])] = v["name"]

                version_select = ui.select(
                    version_options,
                    value="none",
                    label="Release Version",
                    on_change=lambda e: search_state.update({"version": e.value}),
                ).classes("min-w-48").props("outlined dense dark color=amber")

                search_btn = ui.button("Search", icon="search").props("color=amber push").classes("px-6")
                spinner = ui.spinner("dots", size="lg", color="amber").classes("ml-2")
                spinner.visible = False

        # ─── Results Container ──────────────────────────────
        results_container = ui.column().classes("w-full gap-4")

        async def do_search():
            query = search_state["query"].strip()
            if not query:
                ui.notify("Please enter a search query", type="warning")
                return

            spinner.visible = True
            search_btn.disable()
            search_state["searching"] = True
            results_container.clear()

            with results_container:
                ui.label("Searching...").classes("text-sm").style(f"color: {COLORS['text_muted']}")

            try:
                # Run scraping in background thread
                scraped = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _run_scrape(app_state, query, search_state)
                )

                search_state["results"] = scraped
                results_container.clear()

                with results_container:
                    if scraped:
                        _render_results(app_state, scraped, search_state, results_container)
                    else:
                        empty_state("search_off", "No releases found for your query.")

            except Exception as e:
                logger.exception("Scrape error")
                results_container.clear()
                with results_container:
                    ui.label(f"Error: {str(e)}").classes("text-sm").style(f"color: {COLORS['error']}")
            finally:
                spinner.visible = False
                search_btn.enable()
                search_state["searching"] = False

        search_btn.on("click", do_search)
        query_input.on("keydown.enter", do_search)


def _run_scrape(app_state, query, search_state):
    """Run the scraping process in a background thread."""
    try:
        # Load settings into modules
        from webapp.automation import AutomationEngine
        engine = AutomationEngine(app_state)
        engine._load_settings_into_modules()

        import scraper
        import releases

        # Determine search type based on media type selection
        media_type = search_state.get("media_type", "auto")

        # Modify query for anime searches
        if media_type in ("anime_show", "anime_movie"):
            # Use nyaa-style search if available
            pass

        scraped_releases = scraper.scrape(query)
        if not scraped_releases:
            return []

        # Resolve IMDB/TMDB IDs and metadata for the query
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
                logger.info(f"Resolved metadata for '{query}': IMDB={meta['imdb_id']}, TMDB={meta['tmdb_id']}")
        except Exception:
            logger.debug("Failed to resolve metadata")

        # Apply version sorting if selected
        version_id = search_state.get("version")
        if version_id and version_id != "none":
            versions_data = app_state.db.get_release_versions(enabled_only=True)
            for v in versions_data:
                if str(v["id"]) == version_id:
                    version_obj = releases.sort.version(
                        v["name"], v.get("triggers", []),
                        v.get("language", "en"), v.get("rules", [])
                    )
                    releases.sort(scraped_releases, version_obj)
                    break

        # Convert to serializable format
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
                "_release_obj": r,  # Keep reference for download
            })

        return result

    except Exception as e:
        logger.exception("Scrape execution error")
        return []


def _render_results(app_state, results, search_state, container):
    """Render scrape results as a table with download buttons."""
    with container:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between p-4"):
                ui.label(f"Results ({len(results)} releases)").classes("text-lg font-semibold").style(
                    f"color: {COLORS['text']}")
                
                # Auto-download best button
                async def auto_download():
                    if results:
                        await _download_release(app_state, results[0], search_state, stream=False)
                    else:
                        ui.notify("No releases available", type="warning")

                ui.button("Download Best", on_click=auto_download, icon="auto_awesome").props("color=amber push")

            # Results table  
            for i, release in enumerate(results):
                with ui.card().classes("w-full p-3").style(
                    f"background: {COLORS['surface_light']}; border-left: 3px solid {COLORS['primary']}"
                ):
                    with ui.row().classes("items-start justify-between w-full gap-4"):
                        with ui.column().classes("flex-1 gap-1"):
                            # Title (truncated)
                            title_text = release["title"]
                            if len(title_text) > 100:
                                title_text = title_text[:100] + "..."
                            ui.label(title_text).classes("text-sm font-medium").style(f"color: {COLORS['text']}")

                            with ui.row().classes("items-center gap-3 flex-wrap"):
                                # Source
                                ui.label(f"📡 {release['source']}").classes("text-xs").style(
                                    f"color: {COLORS['text_muted']}")
                                # Size
                                size_gb = release["size"] / 1024 if release["size"] > 100 else release["size"]
                                ui.label(f"💾 {size_gb:.1f} GB").classes("text-xs").style(
                                    f"color: {COLORS['text_muted']}")
                                # Resolution
                                if release.get("resolution"):
                                    ui.label(f"📺 {release['resolution']}p").classes("text-xs").style(
                                        f"color: {COLORS['text_muted']}")
                                # Seeders
                                if release.get("seeders"):
                                    ui.label(f"🌱 {release['seeders']}").classes("text-xs").style(
                                        f"color: {COLORS['text_muted']}")

                        with ui.column().classes("gap-1"):
                            async def dl_release(r=release):
                                await _download_release(app_state, r, search_state, stream=False)

                            ui.button(
                                "Download", on_click=dl_release, icon="download"
                            ).props("color=amber push dense size=sm").classes("text-xs")


async def _download_release(app_state, release_data, search_state, stream=True):
    """Download a selected release via Decypharr — always requires version selection."""
    try:
        release = release_data.get("_release_obj")
        if not release:
            ui.notify("Release object not available", type="negative")
            return

        # Get available versions
        versions = app_state.db.get_release_versions()
        enabled_versions = [v for v in versions if v.get("enabled")]

        if not enabled_versions:
            ui.notify("No enabled versions found. Configure versions in Settings first.", type="negative")
            return

        # Always prompt user to select which version to use
        with ui.dialog() as ver_dlg, ui.card().classes("p-4").style(
            f"background: {COLORS['surface']}; min-width: 400px"
        ):
            ui.label("Select Version").classes("text-base font-bold mb-1").style(f"color: {COLORS['primary']}")
            ui.label("Choose which version profile to use for this download:").classes("text-xs mb-3").style(
                f"color: {COLORS['text_muted']}")

            for v in enabled_versions:
                ver_id = v.get("id")
                ver_name = v.get("name", "Unknown")
                cat = v.get("category", "default")

                async def pick_version(version=v):
                    ver_dlg.close()
                    ui.notify(f"Sending via {version['name']} (category: {version.get('category', 'default')})",
                              type="info")
                    await _proceed_download(app_state, release, release_data, search_state, version, stream)

                with ui.row().classes("items-center gap-3 w-full cursor-pointer p-3 rounded").style(
                    f"background: {COLORS['surface_light']}; margin-bottom: 4px"
                ).on("click", pick_version):
                    ui.icon("label").style(f"color: {COLORS['primary']}")
                    with ui.column().classes("gap-0"):
                        ui.label(ver_name).classes("text-sm font-medium").style(f"color: {COLORS['text']}")
                        ui.label(f"Category: {cat}").classes("text-xs").style(f"color: {COLORS['text_muted']}")

            ui.button("Cancel", on_click=ver_dlg.close).props("flat color=grey").classes("mt-2")
        ver_dlg.open()

    except Exception as e:
        logger.exception("Download error")
        ui.notify(f"Download error: {str(e)}", type="negative")


async def _proceed_download(app_state, release, release_data, search_state, version, stream):
    """Execute the download via Decypharr after version is selected."""
    try:
        query = search_state.get("query", "")
        media_type = search_state.get("media_type", "auto")
        category = version.get("category", "default")
        ver_name = version.get("name", "unknown")

        # Determine type based on selection or auto-detect
        if media_type == "auto":
            if regex.search(r'(S[0-9]+|SEASON|E[0-9]+|EPISODE|[0-9]+-[0-9]+)', release.title, regex.I):
                detected_type = "show"
            else:
                detected_type = "movie"
        elif media_type in ("anime_show", "show"):
            detected_type = "show"
        else:
            detected_type = "movie"

        # Check if it's anime based on animetitles XML (title matching)
        cinemeta_name = search_state.get("cinemeta_name") or query
        if detected_type == "show" and cinemeta_name and _is_anime_by_xml(cinemeta_name):
            detected_type = "anime_show"
        elif media_type == "anime_show":
            detected_type = "anime_show"

        release.type = detected_type
        release.Releases = [release]

        # Resolve the display title before sending to executor
        cinemeta_name = search_state.get("cinemeta_name") or query or release.title
        imdb_id = search_state.get("imdb_id") or ""

        # Run download via Decypharr in background (pass full version dict)
        dl_result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _execute_download(app_state, release, stream, cinemeta_name, media_type, version, imdb_id=imdb_id)
        )

        dl_success = dl_result.get("success", False) if isinstance(dl_result, dict) else bool(dl_result)
        dl_error = dl_result.get("error") if isinstance(dl_result, dict) else None

        if dl_success:
            final_type = media_type if media_type != "auto" else detected_type
            tmdb_id = search_state.get("tmdb_id")

            # If we still don't have IDs, try resolving now
            poster_url = None
            meta_year = None
            meta_genres = []
            if not imdb_id:
                try:
                    meta = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: _lookup_metadata(query, final_type)
                    )
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

            # Log to database with IDs
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

            # Also upsert a content item so it appears on dashboard/content page
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
            # Log the failure with reason
            app_state.db.add_download_log(
                title=cinemeta_name,
                release_title=release.title,
                media_type=media_type if media_type != "auto" else detected_type,
                imdb_id=search_state.get("imdb_id"),
                tmdb_id=search_state.get("tmdb_id"),
                debrid_service=f"decypharr/{ver_name} ({category})",
                scraper_source=getattr(release, 'source', 'unknown'),
                status=f"failed: {reason}",
            )

    except Exception as e:
        logger.exception("Download error")
        ui.notify(f"Download error: {str(e)}", type="negative")


def _execute_download(app_state, release, stream, cinemeta_name, media_type, version, imdb_id=""):
    """Execute the download via Decypharr using global API key.

    Tracks the download lifecycle on app_state's activity queue:
    sent → downloading → downloaded → processing → (removed).

    Args:
        cinemeta_name: resolved movie/show title from cinemeta.
        version: dict with keys: category, name, etc.
        imdb_id: optional IMDB ID for display on the activity page.

    Returns a dict: {"success": bool, "error": str or None, "poll_completed": bool}
    """
    activity_id = None
    try:
        from webapp.decypharr import DecypharrClient, TorrentState

        decypharr_url = app_state.db.get_setting("Decypharr Base URL", "")
        decypharr_username = app_state.db.get_setting("Decypharr Username", "")
        api_key = app_state.db.get_setting("Decypharr API Key", "")

        if not decypharr_url:
            msg = "Decypharr URL not configured — go to Settings → Decypharr"
            logger.error(msg)
            return {"success": False, "error": msg}

        if not api_key:
            msg = "No Decypharr API Key configured — go to Settings → Decypharr"
            logger.error(msg)
            return {"success": False, "error": msg}

        category = version.get("category", "default")

        # Auth: username = Arr host, password = global API key
        client = DecypharrClient(decypharr_url, username=decypharr_username, password=api_key)

        # Get magnet/hash from the release
        download_attr = getattr(release, 'download', None) or getattr(release, 'magnet', None)
        if isinstance(download_attr, list):
            magnet = download_attr[0] if download_attr else None
        else:
            magnet = download_attr
        info_hash = getattr(release, 'hash', None) or getattr(release, 'infoHash', None)

        # Try to extract info_hash from magnet link for polling later
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
            msg = "No magnet link or info hash found on this release"
            logger.error(msg)
            return {"success": False, "error": msg}

        if not result:
            msg = f"Decypharr rejected the torrent (version={version.get('name', '?')}, category={category})"
            logger.warning(msg)
            return {"success": False, "error": msg}

        logger.info("Decypharr download sent: %s → version=%s category=%s",
                     release.title[:60], version.get("name", "?"), category)

        # ── Activity: mark as sent ──────────────────────────────────
        activity_id = app_state.add_activity(
            title=cinemeta_name,
            release_title=release.title,
            info_hash=info_hash or "",
            category=category,
            imdb_id=imdb_id,
        )

        out = {"success": True, "error": None, "sent_title": release.title, "poll_completed": False}

        # Poll for completion and update activity along the way
        if info_hash:
            def _on_progress(state, progress):
                if TorrentState.is_downloading(state):
                    app_state.update_activity(activity_id, status="downloading", progress=progress)
                elif TorrentState.is_completed(state):
                    app_state.update_activity(activity_id, status="downloaded", progress=1.0)

            try:
                poll_result = client.wait_for_completion(
                    info_hash, timeout=300, poll_interval=5,
                    remove_on_complete=True,
                    progress_callback=_on_progress,
                )
                if poll_result["success"]:
                    logger.info("Torrent completed and removed from Decypharr: %s", info_hash[:16])
                    out["poll_completed"] = True
                    # Mark processing briefly, then completed
                    app_state.update_activity(activity_id, status="processing", progress=1.0)
                    import time as _time; _time.sleep(2)
                    app_state.update_activity(activity_id, status="completed", progress=1.0)
                    # Remove from activity queue after a short visible delay
                    _time.sleep(5)
                    app_state.remove_activity(activity_id)
                    activity_id = None  # prevent double-remove in finally
                else:
                    logger.warning("Torrent poll ended: %s (state: %s)",
                                   info_hash[:16], poll_result["state"])
            except Exception as poll_err:
                logger.debug("Torrent polling error (non-fatal): %s", poll_err)
        else:
            logger.info("No info_hash available — skipping completion polling")
            # No hash to poll — mark completed directly
            app_state.update_activity(activity_id, status="completed", progress=1.0)
            import time as _time; _time.sleep(5)
            app_state.remove_activity(activity_id)
            activity_id = None

        return out

    except Exception as e:
        logger.exception("Download execution error")
        return {"success": False, "error": str(e)}
    finally:
        # Ensure activity item is cleaned up on any failure
        if activity_id:
            try:
                app_state.remove_activity(activity_id)
            except Exception:
                pass
