"""
Automation engine for pd_reloaded.
Bridges the legacy download automation with the new web UI.
"""

import os
import re
import shutil
import time
import logging
from threading import Thread, Semaphore
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


# ─── Shared post-download helpers (used by automation + scraper page) ─────

def get_category_media_folders(db):
    """Build a category → media_folder mapping from release versions.

    The media folder for each category is derived from the global
    "Global Media Folder" base setting + the category name, mirroring
    how download folders are derived.
    """
    if not db:
        return {}
    global_media_base = (db.get_setting("Global Media Folder", "") or "").rstrip("/")
    versions_data = db.get_release_versions(enabled_only=True)
    mapping = {}
    for v in versions_data:
        cat = v.get("category", "default")
        if cat not in mapping:
            # Prefer global base + category; fall back to per-version value
            if global_media_base:
                mapping[cat] = f"{global_media_base}/{cat}"
            else:
                mf = v.get("media_folder", "")
                if mf:
                    mapping[cat] = mf
    return mapping


def move_to_media_folder(app_state, torrent, category, title, activity_id=None):
    """Move completed content from symlink dir to the media folder.

    Reads the ``content_path`` from the torrent dict returned by Decypharr
    and moves it to the media folder configured for the given *category*.

    Returns the destination path on success, ``None`` on failure.
    """
    content_path = torrent.get("content_path", "")
    if not content_path:
        logger.warning("No content_path in torrent data for %s", title)
        app_state.add_log(f"\u26a0 No content_path for: {title}")
        return None

    category_media_folders = get_category_media_folders(app_state.db)
    media_folder = category_media_folders.get(category)
    if not media_folder:
        app_state.add_log(
            f"\u26a0 No media folder configured for category '{category}' \u2014 "
            f"skipping move for: {title}"
        )
        logger.warning("No media_folder for category %s", category)
        return None

    folder_name = os.path.basename(content_path)
    dest_path = os.path.join(media_folder, folder_name)

    try:
        os.makedirs(media_folder, exist_ok=True)

        if os.path.exists(dest_path):
            app_state.add_log(
                f"\u26a0 Destination already exists, removing old: {dest_path}"
            )
            if os.path.isdir(dest_path):
                shutil.rmtree(dest_path)
            else:
                os.remove(dest_path)

        if activity_id:
            app_state.update_activity(activity_id, status="moving")
        shutil.move(content_path, dest_path)

        app_state.add_log(
            f"\u2714 Moved to media folder: {folder_name} \u2192 {media_folder}"
        )
        logger.info("Moved %s \u2192 %s", content_path, dest_path)
        return dest_path

    except Exception as e:
        app_state.add_log(
            f"\u2717 Failed to move content: {title} \u2014 {e}"
        )
        logger.exception("Failed to move %s \u2192 %s", content_path, dest_path)
        return None


def rename_media_folder(moved_path, title, year, media_type,
                        tmdb_id=None, imdb_id=None):
    """Rename moved media to a standard naming scheme.

    Movies:  ``Title (Year) - {tmdb-ID}/Title (Year) - {tmdb-ID}.ext``
    Shows/Anime: ``Title (Year) - {tmdb-ID}/``  (folder only, files untouched)

    Falls back to ``{imdb-ID}`` when *tmdb_id* is not available.
    Returns the (possibly updated) path so callers can pass it to the
    Plex refresh queue.
    """
    if not moved_path or not os.path.exists(moved_path):
        return moved_path

    # ── Build the identifier tag ─────────────────────────────
    if tmdb_id:
        id_tag = f"{{tmdb-{tmdb_id}}}"
    elif imdb_id:
        id_tag = f"{{imdb-{imdb_id}}}"
    else:
        logger.warning("No tmdb/imdb ID — skipping rename for %s", moved_path)
        return moved_path

    # ── Clean title ──────────────────────────────────────────
    name = title.strip()
    # Strip a trailing year if it duplicates the explicit *year* value
    if year:
        name = re.sub(
            rf'[\s._-]*\(?\s*{re.escape(str(year))}\s*\)?\s*$', '', name
        ).strip()
    # Strip trailing season/episode markers (e.g. "S01", "S01E03")
    name = re.sub(r'[\s._-]*S\d{2,}(?:E\d{2,})?[\s._-]*$', '', name, flags=re.I).strip()
    # Collapse dots / underscores → spaces, then title-case
    name = re.sub(r'[._]+', ' ', name).strip()
    name = name.title()

    year_part = f" ({year})" if year else ""
    base_name = f"{name}{year_part} - {id_tag}"

    parent_dir = os.path.dirname(moved_path)
    is_movie = media_type in ("movie", "anime_movie")

    try:
        if os.path.isdir(moved_path):
            # ── Movies: rename every file inside ─────────────
            if is_movie:
                for fname in os.listdir(moved_path):
                    fpath = os.path.join(moved_path, fname)
                    if os.path.isfile(fpath) or os.path.islink(fpath):
                        _, ext = os.path.splitext(fname)
                        new_fpath = os.path.join(
                            moved_path, f"{base_name}{ext}")
                        if fpath != new_fpath:
                            os.rename(fpath, new_fpath)

            # ── Check for an existing folder with the same ID tag ──
            new_folder = os.path.join(parent_dir, base_name)
            if moved_path.rstrip('/') != new_folder.rstrip('/'):
                existing = None
                for entry in os.listdir(parent_dir):
                    candidate = os.path.join(parent_dir, entry)
                    if (os.path.isdir(candidate)
                            and id_tag in entry
                            and candidate.rstrip('/') != moved_path.rstrip('/')):
                        existing = candidate
                        break

                if existing:
                    # Merge files into the existing folder
                    for fname in os.listdir(moved_path):
                        src = os.path.join(moved_path, fname)
                        dst = os.path.join(existing, fname)
                        shutil.move(src, dst)
                    # Remove the now-empty source folder
                    try:
                        os.rmdir(moved_path)
                    except OSError:
                        shutil.rmtree(moved_path, ignore_errors=True)
                    logger.info("Merged into existing → %s", existing)
                    return existing
                else:
                    os.rename(moved_path, new_folder)
                    logger.info("Renamed → %s", new_folder)
                    return new_folder
            return moved_path

        elif os.path.isfile(moved_path) or os.path.islink(moved_path):
            # Single file — place into existing or new folder
            _, ext = os.path.splitext(moved_path)
            # Check if a folder with the same ID tag already exists
            existing = None
            for entry in os.listdir(parent_dir):
                candidate = os.path.join(parent_dir, entry)
                if os.path.isdir(candidate) and id_tag in entry:
                    existing = candidate
                    break
            target_folder = existing if existing else os.path.join(parent_dir, base_name)
            os.makedirs(target_folder, exist_ok=True)
            if is_movie:
                new_file = os.path.join(target_folder, f"{base_name}{ext}")
            else:
                # Shows: keep original filename
                new_file = os.path.join(
                    target_folder, os.path.basename(moved_path))
            shutil.move(moved_path, new_file)
            logger.info("Renamed → %s", target_folder)
            return target_folder

    except Exception:
        logger.exception("rename_media_folder failed for %s", moved_path)

    return moved_path


# ── Plex refresh queue (serialises scans so they don't conflict) ─────
import queue as _queue
import threading as _threading

_refresh_q: _queue.Queue = _queue.Queue()
_worker_started = False
_worker_lock = _threading.Lock()


def _ensure_plex_worker():
    """Lazily start the single background worker thread."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        t = _threading.Thread(
            target=_plex_refresh_worker, daemon=True,
            name="plex-refresh-queue")
        t.start()
        _worker_started = True


def _plex_refresh_worker():
    """Process queued Plex scan requests one at a time."""
    while True:
        item = _refresh_q.get()
        try:
            _do_refresh_plex_library(
                item["app_state"], item["media_type"], item["moved_path"])
        except Exception as exc:
            logger.debug("Plex refresh worker error: %s", exc)
        finally:
            _refresh_q.task_done()


def refresh_plex_library(app_state, media_type, moved_path):
    """Enqueue a Plex library refresh (non-blocking, thread-safe).

    Scans are executed sequentially by a single background thread so
    that simultaneous requests for different libraries don't conflict.
    """
    _ensure_plex_worker()
    _refresh_q.put({
        "app_state": app_state,
        "media_type": media_type,
        "moved_path": moved_path,
    })
    depth = _refresh_q.qsize()
    logger.info("Plex refresh queued: type=%s path=%s (queue depth ~%d)",
                media_type, moved_path, depth)
    app_state.add_log(
        f"\U0001f4da Plex scan queued for {os.path.basename(moved_path)}"
        + (f" ({depth} pending)" if depth > 1 else "")
    )


def _do_refresh_plex_library(app_state, media_type, moved_path):
    """Actually trigger a Plex partial library scan for *moved_path*.

    *media_type* should be one of ``"movie"``, ``"show"``, ``"season"``,
    ``"anime_show"``, ``"anime_movie"`` etc.  It is mapped to the Plex
    section type (``"movie"`` or ``"show"``).

    Only the section whose ``<Location path>`` is a parent of *moved_path*
    is scanned — this avoids triggering scans on unrelated sections (e.g.
    Anime when the content landed in the TV Shows folder).
    """
    import requests as _requests

    try:
        import content.services.plex as plex_svc

        if not plex_svc.users:
            return
        token = plex_svc.users[0][1]
        server_url = getattr(plex_svc.library, 'url', '')
        if not server_url or not token:
            return

        section_type = ("show" if media_type in (
            "show", "season", "episode", "anime_show") else "movie")

        sections_url = f"{server_url}/library/sections/?X-Plex-Token={token}"
        resp = _requests.get(sections_url, timeout=10)
        if not resp.ok:
            logger.warning("Plex sections request failed: %s", resp.status_code)
            return

        from xml.etree import ElementTree
        root = ElementTree.fromstring(resp.content)

        refresh_sections = getattr(plex_svc.library.refresh, 'sections', [])
        partial = getattr(plex_svc.library.refresh, 'partial', 'true')
        delay = 10
        try:
            delay = float(getattr(plex_svc.library.refresh, 'delay', '10'))
        except (ValueError, TypeError):
            pass

        # Wait *before* scanning so the filesystem settles and we space
        # out consecutive scans when the queue has multiple items.
        time.sleep(delay)

        scanned = False
        for directory in root.findall('.//Directory'):
            key = directory.get('key', '')
            dtype = directory.get('type', '')
            dtitle = directory.get('title', '')

            if key not in refresh_sections or dtype != section_type:
                continue

            # Check this section's Location paths — only scan if our
            # moved_path actually falls under one of them.
            for location in directory.findall('Location'):
                loc_path = location.get('path', '').rstrip('/')
                if not loc_path:
                    continue
                if not (moved_path == loc_path
                        or moved_path.startswith(loc_path + '/')):
                    continue

                # Build the scan path from the Location root + folder name
                # so it exactly matches what Plex knows about.
                folder_name = os.path.basename(moved_path.rstrip('/'))
                scan_path = loc_path + '/' + folder_name
                encoded_path = _requests.utils.quote(scan_path)

                if partial == "true":
                    url = (f"{server_url}/library/sections/{key}"
                           f"/refresh?path={encoded_path}"
                           f"&X-Plex-Token={token}")
                else:
                    url = (f"{server_url}/library/sections/{key}"
                           f"/refresh?X-Plex-Token={token}")
                _requests.get(url, timeout=10)
                scanned = True
                app_state.add_log(
                    f"\U0001f4da Plex scan: \"{dtitle}\" at {scan_path}"
                )
                logger.info("Plex refresh: section %s (%s) path=%s",
                            key, dtitle, scan_path)
                break  # one location match per section is enough

        if not scanned:
            app_state.add_log(
                f"\u26a0 No Plex section found for path: {moved_path}"
            )
            logger.warning(
                "No Plex section Location matched moved_path=%s "
                "(sections=%s, type=%s)", moved_path, refresh_sections,
                section_type)

    except Exception as e:
        logger.debug("Plex library refresh error: %s", e)


class AutomationEngine:
    """Manages the background download automation loop."""

    # Concurrency limits
    MAX_CONCURRENT = 6          # total simultaneous downloads
    MAX_MOVIES = 4              # max concurrent movie downloads
    MAX_SERIES = 2              # max concurrent series downloads

    def __init__(self, app_state):
        self.app_state = app_state
        self._thread = None
        self._stop = False
        self._movie_sem = Semaphore(self.MAX_MOVIES)
        self._series_sem = Semaphore(self.MAX_SERIES)
        self._movie_dl_slots = Semaphore(self.MAX_MOVIES)
        self._series_dl_slots = Semaphore(self.MAX_SERIES)

    @property
    def running(self):
        return self.app_state.automation_running

    def start(self):
        """Start the automation loop."""
        if self._thread and self._thread.is_alive():
            self.app_state.add_log("Automation is already running")
            self.app_state.automation_running = True
            return

        self._stop = False
        self.app_state.automation_running = True
        self._thread = Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.app_state.add_log("Automation started")

    def stop(self):
        """Stop the automation loop."""
        if not self._thread or not self._thread.is_alive():
            self.app_state.automation_running = False
            self.app_state.add_log("Automation is not running")
            return

        self._stop = True
        self.app_state.add_log("Stop requested — finishing current in-flight work...")

    def _run_loop(self):
        """Main automation loop - mirrors the legacy threaded() function."""
        try:
            self._load_settings_into_modules()
            self._setup_decypharr_download()
            self.app_state.add_log("Loading content services...")

            import content
            import content.classes

            # Reset in-memory session state so a fresh start doesn't
            # remember downloads / retries from the previous run.
            content.classes.media.ignore_queue.clear()
            content.classes.media.downloaded_versions.clear()
            import content.services
            import content.services.plex
            import content.services.trakt
            import content.services.overseerr

            timeout = 5
            regular_check = 3600
            timeout_counter = 0

            self.app_state.add_log("Fetching library...")
            lib_services = content.classes.library()
            library = lib_services[0]() if lib_services else []

            self.app_state.add_log("Fetching Plex watchlist...")
            plex_watchlist = content.services.plex.watchlist()

            self.app_state.add_log("Fetching Trakt watchlist...")
            trakt_watchlist = content.services.trakt.watchlist()

            self.app_state.add_log("Fetching Overseerr requests...")
            overseerr_requests = content.services.overseerr.requests()

            # Combine all content
            watchlists = plex_watchlist + trakt_watchlist + overseerr_requests
            try:
                watchlists.data.sort(key=lambda s: s.watchlistedAt, reverse=True)
            except Exception:
                self.app_state.add_log("Couldn't sort by newest, using default order")

            unique_watchlists = self._dedupe_elements_by_identity(self._unique(watchlists))

            # Log all watchlist items to content library before processing
            try:
                removed = self.app_state.db.deduplicate_content_items()
                if removed:
                    self.app_state.add_log(f"Cleaned up {removed} duplicate content rows")
            except Exception as e:
                logger.debug("Content dedup cleanup failed: %s", e)

            self._log_all_watchlist_items(unique_watchlists)
            self._repair_existing_episode_ids()

            if len(library) > 0:
                self.app_state.add_log(f"Checking new content ({len(unique_watchlists)} items)...")
            else:
                self.app_state.add_log(f"Library empty — checking new content ({len(unique_watchlists)} items)...")

            self._process_elements(unique_watchlists, library)
            self.app_state.add_log("Initial check complete")

            # Main polling loop
            while not self._stop:
                # Call ALL update methods (avoid short-circuit so every
                # service is polled on each iteration).
                plex_updated = plex_watchlist.update()
                overseerr_updated = overseerr_requests.update()
                trakt_updated = trakt_watchlist.update()

                if plex_updated or overseerr_updated or trakt_updated:
                    watchlists = plex_watchlist + trakt_watchlist + overseerr_requests

                    # After download() mutates show objects via
                    # _ensure_loaded(), the equality check inside
                    # update()'s cleanup loop can fail, emptying
                    # self.data.  Re-fetch fresh watchlists when that
                    # happens so new items are never missed.
                    if len(watchlists) == 0:
                        logger.debug(
                            "Watchlists empty after update — refetching "
                            "(plex=%s overseerr=%s trakt=%s)",
                            plex_updated, overseerr_updated, trakt_updated,
                        )
                        plex_watchlist = content.services.plex.watchlist()
                        trakt_watchlist = content.services.trakt.watchlist()
                        overseerr_requests = content.services.overseerr.requests()
                        watchlists = plex_watchlist + trakt_watchlist + overseerr_requests

                    if len(watchlists) == 0:
                        # Still nothing — skip this cycle
                        time.sleep(timeout)
                        continue

                    try:
                        watchlists.data.sort(key=lambda s: s.watchlistedAt, reverse=True)
                    except Exception:
                        pass

                    lib_services = content.classes.library()
                    library = lib_services[0]() if lib_services else []

                    unique_watchlists = self._dedupe_elements_by_identity(self._unique(watchlists))
                    self._log_all_watchlist_items(unique_watchlists)
                    self._repair_existing_episode_ids()
                    self.app_state.add_log("Checking updated content...")
                    self._process_elements(unique_watchlists, library)
                    self.app_state.add_log("Check complete")

                elif timeout_counter >= regular_check:
                    self.app_state.add_log("Running regular full check...")
                    plex_watchlist = content.services.plex.watchlist()
                    trakt_watchlist = content.services.trakt.watchlist()
                    overseerr_requests = content.services.overseerr.requests()
                    watchlists = plex_watchlist + trakt_watchlist + overseerr_requests
                    try:
                        watchlists.data.sort(key=lambda s: s.watchlistedAt, reverse=True)
                    except Exception:
                        pass

                    lib_services = content.classes.library()
                    library = lib_services[0]() if lib_services else []
                    timeout_counter = 0

                    unique_watchlists = self._dedupe_elements_by_identity(self._unique(watchlists))
                    self._process_elements(unique_watchlists, library)
                    self.app_state.add_log("Regular check complete")
                else:
                    timeout_counter += timeout

                time.sleep(timeout)

        except Exception as e:
            self.app_state.add_log(f"Automation error: {str(e)}")
            logger.exception("Automation loop error")
        finally:
            self.app_state.automation_running = False
            self.app_state.add_log("Automation loop exited")

    def _load_settings_into_modules(self):
        """Load settings from DB into the legacy module-level variables."""
        if not self.app_state.db:
            return

        # Import settings FIRST to bootstrap the entire legacy import chain
        # in the correct order and avoid circular import errors.
        # The legacy code expects settings to be the entry-point import,
        # which resolves: content -> content.services -> plex -> classes ->
        # releases -> ui -> settings (partial) safely.
        import settings  # noqa: F401

        import content.services.plex
        import content.services.trakt
        import content.services.overseerr
        import content.services.jellyfin
        import content.classes
        import scraper.services
        import scraper.services.torrentio
        import scraper.services.jackett
        import scraper.services.prowlarr
        import scraper.services.orionoid
        import scraper.services.nyaa
        import releases

        db = self.app_state.db

        # Plex users
        plex_users = db.get_plex_users()
        if plex_users:
            content.services.plex.users = [[u["username"], u["token"]] for u in plex_users]
            primary = db.get_primary_plex_user()
            if primary:
                content.services.plex.library.url = primary.get("server_url", "")
                # Load client_id so Plex cloud API requests carry proper identification
                if primary.get("client_id"):
                    content.services.plex.client_id = primary["client_id"]
                    content.services.plex._update_headers()

        # Content services  
        active_content = db.get_setting("Content Services", [])
        if active_content:
            content.services.active = active_content

        # Scraper
        sources = db.get_scraper_sources(enabled_only=True)
        if sources:
            scraper.services.active = [s["name"] for s in sources]
            for s in sources:
                cfg = s.get("config", {})
                name = s["name"].lower()
                if name == "torrentio" and cfg.get("default_opts"):
                    scraper.services.torrentio.default_opts = cfg["default_opts"]
                elif name == "jackett":
                    if cfg.get("base_url"):
                        scraper.services.jackett.base_url = cfg["base_url"]
                    if cfg.get("api_key"):
                        scraper.services.jackett.api_key = cfg["api_key"]
                elif name == "prowlarr":
                    if cfg.get("base_url"):
                        scraper.services.prowlarr.base_url = cfg["base_url"]
                    if cfg.get("api_key"):
                        scraper.services.prowlarr.api_key = cfg["api_key"]
                elif name == "orionoid" and cfg.get("token"):
                    scraper.services.orionoid.token = cfg["token"]
                elif name == "nyaa":
                    if cfg.get("params"):
                        scraper.services.nyaa.params = cfg["params"]
                    if cfg.get("proxy"):
                        scraper.services.nyaa.proxy = cfg["proxy"]

        # Versions
        versions_data = db.get_release_versions(enabled_only=True)
        if versions_data:
            releases.sort.versions = []
            for v in versions_data:
                releases.sort.versions.append([
                    v["name"],
                    v.get("triggers", []),
                    v.get("language", "en"),
                    v.get("rules", []),
                ])

        # Library settings
        lib_service = db.get_setting("Library collection service")
        if lib_service:
            content.classes.library.active = lib_service
        
        lib_update = db.get_setting("Library update services")
        if lib_update:
            content.classes.refresh.active = lib_update

        lib_ignore = db.get_setting("Library ignore services")
        if lib_ignore:
            content.classes.ignore.active = lib_ignore

        # Plex library refresh settings
        plex_sections = db.get_setting("Plex library refresh")
        if plex_sections and isinstance(plex_sections, list):
            content.services.plex.library.refresh.sections = [
                s[0] if isinstance(s, list) else s for s in plex_sections
            ]
        partial_scan = db.get_setting("Plex library partial scan", "true")
        content.services.plex.library.refresh.partial = partial_scan
        scan_delay = db.get_setting("Plex library refresh delay", "2")
        content.services.plex.library.refresh.delay = scan_delay

        # Trakt settings
        trakt_users = db.get_trakt_users()
        if trakt_users:
            content.services.trakt.users = [u["access_token"] for u in trakt_users if u.get("access_token")]

        # Overseerr
        overseerr_url = db.get_setting("Overseerr Base URL")
        if overseerr_url:
            content.services.overseerr.base_url = overseerr_url
        overseerr_key = db.get_setting("Overseerr API Key")
        if overseerr_key:
            content.services.overseerr.api_key = overseerr_key

        # Decypharr
        decypharr_url = db.get_setting("Decypharr Base URL", "")
        decypharr_username = db.get_setting("Decypharr Username", "")
        decypharr_api_key = db.get_setting("Decypharr API Key", "")
        if decypharr_url:
            from webapp.decypharr import DecypharrClient
            client = DecypharrClient(decypharr_url, username=decypharr_username, password=decypharr_api_key)
            ok, info = client.test_connection()
            if ok:
                self.app_state.add_log(f"Decypharr connected at {decypharr_url} (v{info})")
                self.app_state.decypharr_client = client
            else:
                self.app_state.add_log(f"Warning: Decypharr at {decypharr_url} not reachable: {info}")
                self.app_state.decypharr_client = None
        else:
            self.app_state.decypharr_client = None

        # Watchlist auto-remove setting
        auto_remove = db.get_setting("Plex auto remove", "none")
        content.services.plex.watchlist.autoremove = auto_remove
        if hasattr(content.services, 'trakt') and hasattr(content.services.trakt, 'watchlist'):
            content.services.trakt.watchlist.autoremove = auto_remove

        self.app_state.add_log("Settings loaded into modules")

    def _setup_decypharr_download(self):
        """Replace legacy debrid.download with Decypharr-based download.

        The legacy flow scrapes and sorts releases perfectly, but the final
        ``debrid.download()`` call fails because no traditional debrid service
        (RealDebrid, AllDebrid, …) is configured — Decypharr is the download
        backend.  We monkey-patch ``debrid.download`` so the entire legacy
        scrape → sort → download pipeline works end-to-end via Decypharr.

        After Decypharr finishes, the content folder (symlinks) is moved from
        the download path to the final media folder, the item is marked as
        collected, and a Plex library partial scan is triggered.
        """
        import debrid

        decypharr_client = self.app_state.decypharr_client
        if not decypharr_client:
            self.app_state.add_log(
                "Warning: no Decypharr client — downloads will use legacy debrid"
            )
            return

        # Build version-name → category map from DB
        db = self.app_state.db
        versions_data = db.get_release_versions(enabled_only=True) if db else []
        version_categories = {}
        for v in versions_data:
            version_categories[v["name"]] = v.get("category", "default")

        app_state = self.app_state
        client = decypharr_client

        def _finalize_download(
            *,
            element,
            release,
            clean_title,
            media_type,
            imdb_id,
            tmdb_id,
            info_hash,
            category,
            ver_name,
            size_gb,
            activity_id,
            slot_sem,
        ):
            completed_torrent = None
            moved_path = None
            try:
                if info_hash:
                    from webapp.decypharr import TorrentState

                    def _on_progress(state, progress):
                        if TorrentState.is_downloading(state):
                            app_state.update_activity(
                                activity_id, status="downloading",
                                progress=progress,
                            )
                        elif TorrentState.is_completed(state):
                            app_state.update_activity(
                                activity_id, status="downloaded",
                                progress=1.0,
                            )

                    poll = client.wait_for_completion(
                        info_hash,
                        timeout=None,
                        poll_interval=5,
                        remove_on_complete=False,
                        progress_callback=_on_progress,
                    )
                    if poll["success"]:
                        completed_torrent = poll.get("torrent")
                        app_state.update_activity(
                            activity_id, status="processing", progress=1.0)
                    else:
                        app_state.add_log(
                            f"\u2717 Download failed: {clean_title} ({poll.get('state', 'unknown')})"
                        )
                        return

                if completed_torrent:
                    moved_path = move_to_media_folder(
                        app_state, completed_torrent, category,
                        clean_title, activity_id,
                    )

                if moved_path:
                    rename_title = clean_title
                    rename_year = getattr(element, 'year', None)
                    rename_imdb = imdb_id
                    rename_tmdb = tmdb_id

                    if media_type in ('season', 'anime_show'):
                        rename_title = getattr(
                            element, 'parentTitle',
                            getattr(element, 'title', clean_title)
                        ).replace('.', ' ').strip()
                        rename_year = getattr(
                            element, 'parentYear',
                            getattr(element, 'year', None))
                        rename_imdb = None
                        rename_tmdb = None
                        for eid in getattr(element, 'parentEID', []):
                            if 'imdb://' in eid:
                                rename_imdb = eid.replace('imdb://', '')
                            elif 'tmdb://' in eid:
                                rename_tmdb = eid.replace('tmdb://', '')
                    elif media_type == 'episode':
                        rename_title = getattr(
                            element, 'grandparentTitle',
                            getattr(element, 'parentTitle', clean_title)
                        ).replace('.', ' ').strip()
                        rename_year = getattr(
                            element, 'grandparentYear',
                            getattr(element, 'parentYear', None))
                        rename_imdb = None
                        rename_tmdb = None
                        for eid in getattr(element, 'grandparentEID', []):
                            if 'imdb://' in eid:
                                rename_imdb = eid.replace('imdb://', '')
                            elif 'tmdb://' in eid:
                                rename_tmdb = eid.replace('tmdb://', '')

                    moved_path = rename_media_folder(
                        moved_path, rename_title,
                        year=rename_year,
                        media_type=media_type,
                        tmdb_id=rename_tmdb, imdb_id=rename_imdb,
                    )

                if info_hash:
                    try:
                        client.remove_torrent(info_hash, delete_files=False)
                    except Exception:
                        logger.debug("Failed to remove torrent %s", info_hash[:16])

                try:
                    app_state.db.add_download_log(
                        title=clean_title,
                        release_title=release.title,
                        media_type=media_type,
                        imdb_id=imdb_id,
                        tmdb_id=tmdb_id,
                        info_hash=info_hash.lower() if info_hash else None,
                        debrid_service=f"decypharr/{ver_name} ({category})",
                        scraper_source=getattr(release, 'source', 'unknown'),
                        resolution=str(getattr(release, 'resolution', '')),
                        size_gb=size_gb,
                        status="completed",
                    )
                except Exception:
                    logger.debug("Failed to log download")

                try:
                    is_anime = (hasattr(element, 'isanime')
                                and element.isanime())
                    final_media_type = media_type
                    if is_anime:
                        if final_media_type == "movie":
                            final_media_type = "anime_movie"
                        elif final_media_type == "show":
                            final_media_type = "anime_show"

                    if final_media_type in ("episode", "season"):
                        app_state.db.add_content_item(
                            imdb_id=imdb_id,
                            tmdb_id=tmdb_id,
                            title=clean_title,
                            media_type=final_media_type,
                            year=getattr(element, 'year', None),
                            status="collected",
                            source="automation",
                        )
                    else:
                        app_state.db.upsert_content_item(
                            imdb_id=imdb_id,
                            tmdb_id=tmdb_id,
                            title=clean_title,
                            media_type=final_media_type,
                            year=getattr(element, 'year', None),
                            status="collected",
                            source="automation",
                        )
                except Exception:
                    logger.debug("Failed to update content item")

                if moved_path:
                    refresh_plex_library(app_state, media_type, moved_path)

                app_state.update_activity(
                    activity_id, status="completed", progress=1.0)
                time.sleep(3)
            except Exception as e:
                logger.exception("Decypharr finalize error for %s", release.title[:60])
                app_state.add_log(f"\u2717 Decypharr finalize error: {e}")
            finally:
                try:
                    app_state.remove_activity(activity_id)
                except Exception:
                    pass
                slot_sem.release()

        def _decypharr_download(element, stream=False, query='', force=False):
            """Route the download through Decypharr."""
            if not element.Releases:
                return False

            release = element.Releases[0]

            # ── Extract magnet / info-hash ────────────────────────
            download_attr = getattr(release, "download", None)
            if isinstance(download_attr, list):
                magnet = download_attr[0] if download_attr else None
            else:
                magnet = download_attr
            info_hash = getattr(release, "hash", None) or ""

            if not info_hash and magnet and isinstance(magnet, str) and magnet.startswith("magnet:"):
                import re as _re
                m = _re.search(r'btih:([a-fA-F0-9]{40})', magnet)
                if m:
                    info_hash = m.group(1).lower()
                else:
                    m = _re.search(r'btih:([a-fA-F0-9]{32})', magnet)
                    if m:
                        info_hash = m.group(1).lower()

            if not magnet and not info_hash:
                return False

            # ── Dedup: skip if this hash was already downloaded ───
            if info_hash and app_state.db.is_hash_downloaded(info_hash):
                app_state.add_log(
                    f"⏭ Skipping (already downloaded): {release.title[:80]}"
                )
                logger.info("Skipping duplicate hash %s: %s",
                            info_hash[:16], release.title[:60])
                # Important: return False so the legacy debrid_download() loop
                # can continue to the next candidate release for this item.
                # Returning True marks the item as downloaded and can cause
                # episodes/seasons to stop after the first matching torrent.
                return False

            # ── Determine category from the current version ───────
            category = "default"
            ver_name = "unknown"
            if hasattr(element, 'version') and hasattr(element.version, 'name'):
                ver_name = element.version.name
                category = version_categories.get(ver_name, "default")

            # ── Extract identifiers ──────────────────────────────
            title_str = (element.query() if hasattr(element, 'query')
                         and callable(element.query) else release.title)
            imdb_id = None
            tmdb_id = None
            media_type = getattr(element, 'type', 'movie')
            id_source = []
            if media_type == 'episode':
                id_source = getattr(element, 'grandparentEID', [])
            elif media_type in ('season', 'anime_show'):
                id_source = getattr(element, 'parentEID', [])
            elif hasattr(element, 'EID'):
                id_source = getattr(element, 'EID', [])
            for eid in id_source:
                if 'imdb://' in eid:
                    imdb_id = eid.replace('imdb://', '')
                elif 'tmdb://' in eid:
                    tmdb_id = eid.replace('tmdb://', '')

            # ── Create activity item ─────────────────────────────
            clean_title = title_str.replace('.', ' ').strip()
            activity_id = app_state.add_activity(
                title=clean_title,
                release_title=release.title,
                info_hash=info_hash or "",
                category=category,
                imdb_id=imdb_id or "",
            )
            size_gb = release.size / 1024 if release.size > 100 else release.size
            log_msg = (
                f"Sending to Decypharr: {clean_title} | "
                f"Release: {release.title[:90]} | "
                f"{size_gb:.1f}GB | {ver_name}/{category}"
            )
            app_state.add_log(f"\u2b07 {log_msg}")
            logger.info(log_msg)

            is_series_media = media_type in ('show', 'season', 'episode', 'anime_show')
            slot_sem = self._series_dl_slots if is_series_media else self._movie_dl_slots
            slot_sem.acquire()

            # ── Send to Decypharr ────────────────────────────────
            try:
                if magnet and isinstance(magnet, str) and magnet.startswith("magnet:"):
                    result = client.download_magnet(magnet, category=category)
                elif info_hash:
                    result = client.download_hash(info_hash, category=category)
                elif magnet:
                    result = client.add_torrent_url(magnet, category=category)
                else:
                    app_state.remove_activity(activity_id)
                    return False

                if not result:
                    app_state.add_log(
                        f"\u2717 Decypharr rejected: {release.title[:80]} [{ver_name}/{category}]"
                    )
                    app_state.remove_activity(activity_id)
                    slot_sem.release()
                    return False

                app_state.update_activity(
                    activity_id, status="sent_to_decypharr")
                Thread(
                    target=_finalize_download,
                    kwargs={
                        "element": element,
                        "release": release,
                        "clean_title": clean_title,
                        "media_type": media_type,
                        "imdb_id": imdb_id,
                        "tmdb_id": tmdb_id,
                        "info_hash": info_hash,
                        "category": category,
                        "ver_name": ver_name,
                        "size_gb": size_gb,
                        "activity_id": activity_id,
                        "slot_sem": slot_sem,
                    },
                    daemon=True,
                ).start()
                return True

            except Exception as e:
                logger.exception("Decypharr download error for %s",
                                 release.title[:60])
                app_state.add_log(f"\u2717 Decypharr error: {e}")
                try:
                    slot_sem.release()
                except Exception:
                    pass
                try:
                    app_state.remove_activity(activity_id)
                except Exception:
                    pass
                return False

        # Patch the global debrid.download function
        debrid.download = _decypharr_download
        self.app_state.add_log("Download routing: Decypharr")

    def _log_all_watchlist_items(self, watchlists):
        """Batch-insert all watchlist items to content library before processing."""
        for element in watchlists:
            try:
                imdb_id = None
                tmdb_id = None
                media_type = getattr(element, 'type', 'unknown')
                title = getattr(element, 'title', 'Unknown')

                if hasattr(element, 'EID'):
                    for eid in element.EID:
                        if 'imdb://' in eid:
                            imdb_id = eid.replace('imdb://', '')
                        elif 'tmdb://' in eid:
                            tmdb_id = eid.replace('tmdb://', '')

                # Determine source from the watchlist module
                source = 'plex'
                if hasattr(element, 'watchlist'):
                    mod = getattr(element.watchlist, '__module__', '')
                    if 'trakt' in mod:
                        source = 'trakt'
                    elif 'overseerr' in mod:
                        source = 'overseerr'

                self.app_state.db.upsert_content_item(
                    imdb_id=imdb_id,
                    tmdb_id=tmdb_id,
                    title=title,
                    media_type=media_type,
                    year=getattr(element, 'year', None),
                    genres=[],
                    status="watchlisted",
                    source=source,
                )
            except Exception as e:
                logger.debug(f"Failed to log watchlist item: {e}")
        self.app_state.add_log(f"Added/updated {len(watchlists)} watchlist items in content library")

    def _dedupe_elements_by_identity(self, elements):
        """Dedupe elements by stable identity (IDs/title/year/episode numbers)."""
        unique = []
        seen = set()

        def _norm(value):
            return re.sub(r'\s+', ' ', (value or '').strip().lower())

        for element in elements:
            media_type = getattr(element, 'type', 'unknown')

            ids = []
            for attr in ('EID', 'parentEID', 'grandparentEID'):
                if hasattr(element, attr):
                    try:
                        ids.extend(getattr(element, attr) or [])
                    except Exception:
                        pass

            imdb_id = next((eid.replace('imdb://', '') for eid in ids if isinstance(eid, str) and eid.startswith('imdb://')), '')
            tmdb_id = next((eid.replace('tmdb://', '') for eid in ids if isinstance(eid, str) and eid.startswith('tmdb://')), '')

            title = getattr(element, 'title', '')
            if media_type == 'season':
                title = getattr(element, 'parentTitle', title)
            elif media_type == 'episode':
                title = getattr(element, 'grandparentTitle', getattr(element, 'parentTitle', title))

            key = (
                media_type,
                imdb_id,
                tmdb_id,
                _norm(title),
                getattr(element, 'year', None) or getattr(element, 'parentYear', None) or getattr(element, 'grandparentYear', None),
                getattr(element, 'parentIndex', None),
                getattr(element, 'index', None),
            )

            if key in seen:
                continue
            seen.add(key)
            unique.append(element)

        return unique

    def _log_content_item(self, element):
        """Log a content item with its IDs to the database."""
        try:
            title = element.query() if hasattr(element, 'query') else str(element)
            imdb_id = None
            tmdb_id = None
            media_type = getattr(element, 'type', 'unknown')
            genres = []

            if hasattr(element, 'EID'):
                for eid in element.EID:
                    if 'imdb://' in eid:
                        imdb_id = eid.replace('imdb://', '')
                    elif 'tmdb://' in eid:
                        tmdb_id = eid.replace('tmdb://', '')

            if hasattr(element, 'genre'):
                try:
                    genres = element.genre().split(', ') if callable(element.genre) else []
                except Exception:
                    pass

            # Determine source from the watchlist module
            source = 'plex'
            if hasattr(element, 'watchlist'):
                mod = getattr(element.watchlist, '__module__', '')
                if 'trakt' in mod:
                    source = 'trakt'
                elif 'overseerr' in mod:
                    source = 'overseerr'

            is_anime = hasattr(element, 'isanime') and element.isanime()
            if is_anime:
                if media_type == "movie":
                    media_type = "anime_movie"
                elif media_type == "show":
                    media_type = "anime_show"

            self.app_state.db.upsert_content_item(
                imdb_id=imdb_id,
                tmdb_id=tmdb_id,
                title=title.replace('.', ' ').strip(),
                media_type=media_type,
                year=getattr(element, 'year', None),
                genres=genres,
                status="downloading",
                source=source,
            )

            self.app_state.add_log(
                f"Processing: {title} | IMDB: {imdb_id or 'N/A'} | TMDB: {tmdb_id or 'N/A'} | Type: {media_type}"
            )

            # Debug: log season info for shows / anime
            
        except Exception as e:
            logger.debug(f"Failed to log content item: {e}")

    def _repair_existing_episode_ids(self):
        """Ensure episode rows use their parent show's IMDB/TMDB IDs."""
        db = self.app_state.db
        if not db:
            return
        try:
            from database.models import ContentItem

            def _norm(name):
                return re.sub(r'\s+', ' ', (name or '').strip().lower())

            updated = 0
            with db.session_scope() as session:
                shows = session.query(ContentItem).filter(
                    ContentItem.media_type.in_(["show", "anime_show"])
                ).all()
                shows_by_title = {}
                for show in shows:
                    key = _norm(show.title)
                    shows_by_title.setdefault(key, []).append(show)

                episodes = session.query(ContentItem).filter_by(media_type="episode").all()
                for episode in episodes:
                    m = re.search(r'\sS\d{1,2}E\d{1,2}\b', episode.title or '', re.I)
                    if not m:
                        continue
                    base_title = _norm((episode.title or '')[:m.start()])
                    candidates = shows_by_title.get(base_title, [])
                    if not candidates:
                        continue

                    show = None
                    if episode.year:
                        show = next((s for s in candidates if s.year == episode.year), None)
                    if show is None:
                        show = candidates[0]

                    changed = False
                    if show.imdb_id and episode.imdb_id != show.imdb_id:
                        episode.imdb_id = show.imdb_id
                        changed = True
                    if show.tmdb_id and episode.tmdb_id != show.tmdb_id:
                        episode.tmdb_id = show.tmdb_id
                        changed = True
                    if changed:
                        updated += 1

            if updated:
                self.app_state.add_log(f"Fixed episode IDs for {updated} items")
                logger.info("Episode ID repair updated %d items", updated)
        except Exception as e:
            logger.debug("Episode ID repair failed: %s", e)

    def _reconcile_with_plex(self, elements, library):
        """Remove stale in-memory blocks for items not present in Plex.

        The legacy system tracks ``ignore_queue`` (retry backoff) and
        ``downloaded_versions`` (already-grabbed-this-session) in class-level
        lists.  If an item was downloaded but later disappears from Plex
        (manual delete, failed import, etc.) these stale entries prevent
        reprocessing.  This method cross-checks against the live Plex
        library and clears the blockers so the item is retried.
        """
        import content.classes

        cleared = 0
        try:
            for element in elements:
                if not hasattr(element, 'collected'):
                    continue
                # Lazily load full metadata so collected() can compare
                if hasattr(element, '_ensure_loaded'):
                    element._ensure_loaded()

                media_type = getattr(element, 'type', 'movie')

                if media_type == 'movie':
                    if element.collected(library):
                        continue
                    # Movie not in Plex — clear blockers
                    q = element.query() if hasattr(element, 'query') else ''
                    # Remove from ignore queue
                    for queued in content.classes.media.ignore_queue[:]:
                        if queued == element:
                            content.classes.media.ignore_queue.remove(queued)
                            cleared += 1
                    # Remove downloaded_versions entries
                    content.classes.media.downloaded_versions[:] = [
                        v for v in content.classes.media.downloaded_versions
                        if not v.startswith(q + ' [')
                    ]
                    # Clear DB hash logs so the same torrent can be re-sent
                    self._clear_hash_logs_for(element)
                    # Reset DB status so dashboard shows it needs reprocessing
                    self._reset_content_status(element, 'downloading')

                elif media_type == 'show':
                    # Check every episode individually against Plex
                    any_missing = False
                    for season in getattr(element, 'Seasons', []):
                        season_missing = False
                        for episode in getattr(season, 'Episodes', []):
                            if episode.collected(library):
                                continue
                            any_missing = True
                            season_missing = True
                            eq = episode.query() if hasattr(episode, 'query') else ''
                            for queued in content.classes.media.ignore_queue[:]:
                                if queued == episode:
                                    content.classes.media.ignore_queue.remove(queued)
                                    cleared += 1
                            content.classes.media.downloaded_versions[:] = [
                                v for v in content.classes.media.downloaded_versions
                                if not v.startswith(eq + ' [')
                            ]
                            self._clear_hash_logs_for(episode)
                        # Clear season-level blocks if any episode is missing
                        if season_missing:
                            sq = season.query() if hasattr(season, 'query') else ''
                            for queued in content.classes.media.ignore_queue[:]:
                                if queued == season:
                                    content.classes.media.ignore_queue.remove(queued)
                                    cleared += 1
                            content.classes.media.downloaded_versions[:] = [
                                v for v in content.classes.media.downloaded_versions
                                if not v.startswith(sq + ' [')
                            ]
                    # Clear show-level blocks if any episode is missing
                    if any_missing:
                        for queued in content.classes.media.ignore_queue[:]:
                            if queued == element:
                                content.classes.media.ignore_queue.remove(queued)
                                cleared += 1
                        self._clear_hash_logs_for(element)
                        self._reset_content_status(element, 'downloading')

            if cleared:
                logger.info("Plex reconcile: cleared %d stale blocks", cleared)
        except Exception as e:
            logger.debug("Plex reconciliation error: %s", e)

    def _clear_hash_logs_for(self, element):
        """Remove 'completed' download-log entries for an element so its
        hashes are no longer blocked by the dedup check."""
        db = self.app_state.db
        if not db:
            return
        try:
            imdb_id = None
            eids = getattr(element, 'EID', [])
            if getattr(element, 'type', '') == 'episode':
                eids = getattr(element, 'grandparentEID', eids)
            for eid in eids:
                if 'imdb://' in eid:
                    imdb_id = eid.replace('imdb://', '')
                    break
            if not imdb_id:
                return
            from database.models import DownloadLog
            with db.session_scope() as session:
                deleted = session.query(DownloadLog).filter_by(
                    imdb_id=imdb_id, status="completed"
                ).delete()
                if deleted:
                    logger.info("Cleared %d hash log(s) for %s (not in Plex)",
                                deleted, imdb_id)
        except Exception as e:
            logger.debug("Failed to clear hash logs: %s", e)

    def _reset_content_status(self, element, status):
        """Reset DB content status for an item missing from Plex."""
        db = self.app_state.db
        if not db:
            return
        try:
            imdb_id = None
            eids = getattr(element, 'EID', [])
            for eid in eids:
                if 'imdb://' in eid:
                    imdb_id = eid.replace('imdb://', '')
                    break
            if imdb_id:
                from database.models import ContentItem
                with db.session_scope() as session:
                    item = session.query(ContentItem).filter_by(
                        imdb_id=imdb_id, status="collected"
                    ).first()
                    if item:
                        item.status = status
                        logger.info("Reset %s status → %s (not in Plex)",
                                    imdb_id, status)
        except Exception as e:
            logger.debug("Failed to reset content status: %s", e)

    def _process_elements(self, elements, library):
        """Process downloadable elements concurrently (max 4 movies, 1 series)."""
        # Clear stale in-memory blocks for items missing from Plex
        self._reconcile_with_plex(elements, library)

        def _do_download(element):
            if self._stop:
                return
            media_type = getattr(element, 'type', 'movie')
            is_series = media_type in ('show', 'season')
            sem = self._series_sem if is_series else self._movie_sem
            acquired = False
            while not self._stop and not acquired:
                acquired = sem.acquire(timeout=0.5)
            if not acquired:
                return
            try:
                if self._stop:
                    return
                self._log_content_item(element)
                element.download(library=library)
            finally:
                sem.release()

        downloadable = [e for e in elements if hasattr(e, 'download')]
        if not downloadable:
            return

        with ThreadPoolExecutor(max_workers=self.MAX_CONCURRENT) as pool:
            futures = []
            for element in downloadable:
                if self._stop:
                    break
                futures.append(pool.submit(_do_download, element))
            # Wait for all to finish (or stop flag)
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.debug(f"Concurrent download error: {e}")

    @staticmethod
    def _unique(lst):
        """Remove duplicates while preserving order."""
        unique_objects = []
        for obj in lst:
            is_unique = True
            for unique_obj in unique_objects:
                if unique_obj == obj:
                    is_unique = False
                    break
            if is_unique:
                unique_objects.append(obj)
        return unique_objects


def get_automation_engine(app_state):
    """Return one shared AutomationEngine instance per app_state."""
    engine = getattr(app_state, "automation_engine", None)
    if engine is None:
        engine = AutomationEngine(app_state)
        app_state.automation_engine = engine
    return engine
