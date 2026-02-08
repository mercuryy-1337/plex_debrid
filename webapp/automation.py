"""
Automation engine for pd_reloaded.
Bridges the legacy download automation with the new web UI.
"""

import time
import logging
import itertools
from threading import Thread, Semaphore
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class AutomationEngine:
    """Manages the background download automation loop."""

    # Concurrency limits
    MAX_CONCURRENT = 5          # total simultaneous downloads
    MAX_MOVIES = 4              # max concurrent movie downloads
    MAX_SERIES = 1              # max concurrent series downloads

    def __init__(self, app_state):
        self.app_state = app_state
        self._thread = None
        self._stop = False
        self._movie_sem = Semaphore(self.MAX_MOVIES)
        self._series_sem = Semaphore(self.MAX_SERIES)

    @property
    def running(self):
        return self.app_state.automation_running

    def start(self):
        """Start the automation loop."""
        if self.app_state.automation_running:
            self.app_state.add_log("Automation is already running")
            return

        self._stop = False
        self.app_state.automation_running = True
        self._thread = Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.app_state.add_log("Automation started")

    def stop(self):
        """Stop the automation loop."""
        if not self.app_state.automation_running:
            self.app_state.add_log("Automation is not running")
            return

        self._stop = True
        self.app_state.automation_running = False
        self.app_state.add_log("Stopping automation after current item finishes...")

    def _run_loop(self):
        """Main automation loop - mirrors the legacy threaded() function."""
        try:
            self._load_settings_into_modules()
            self._setup_decypharr_download()
            self.app_state.add_log("Loading content services...")

            import content
            import content.classes
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

            # Log all watchlist items to content library before processing
            self._log_all_watchlist_items(watchlists)

            if len(library) > 0:
                self.app_state.add_log(f"Checking new content ({len(watchlists)} items)...")
            else:
                self.app_state.add_log(f"Library empty — checking new content ({len(watchlists)} items)...")

            self._process_elements(self._unique(watchlists), library)
            self.app_state.add_log("Initial check complete")

            # Main polling loop
            while not self._stop:
                if plex_watchlist.update() or overseerr_requests.update() or trakt_watchlist.update():
                    lib_services = content.classes.library()
                    library = lib_services[0]() if lib_services else []

                    watchlists = plex_watchlist + trakt_watchlist + overseerr_requests
                    try:
                        watchlists.data.sort(key=lambda s: s.watchlistedAt, reverse=True)
                    except Exception:
                        pass

                    self.app_state.add_log("Checking updated content...")
                    # Filter to newly added items only
                    new_elements = []
                    for element in self._unique(watchlists):
                        if self._stop:
                            break
                        if hasattr(element, 'download'):
                            newly_added = True
                            if element.type == "show":
                                for season in element.Seasons:
                                    if season in content.classes.media.ignore_queue or not newly_added:
                                        newly_added = False
                                        break
                                    for episode in season.Episodes:
                                        if episode in content.classes.media.ignore_queue:
                                            newly_added = False
                                            break
                            if newly_added:
                                new_elements.append(element)
                    self._process_elements(new_elements, library)
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

                    self._process_elements(self._unique(watchlists), library)
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
            if hasattr(element, 'EID'):
                for eid in element.EID:
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
            app_state.add_log(f"⬇ {log_msg}")
            logger.info(log_msg)

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
                        f"✗ Decypharr rejected: {release.title[:80]} [{ver_name}/{category}]"
                    )
                    app_state.remove_activity(activity_id)
                    return False

                app_state.update_activity(
                    activity_id, status="sent_to_decypharr")

                # ── Poll for completion (up to 5 min) ────────────
                if info_hash:
                    try:
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
                            timeout=300,
                            poll_interval=5,
                            remove_on_complete=True,
                            progress_callback=_on_progress,
                        )
                        if poll["success"]:
                            app_state.update_activity(
                                activity_id, status="completed", progress=1.0)
                            time.sleep(5)
                            app_state.remove_activity(activity_id)
                            activity_id = None
                    except Exception as poll_err:
                        logger.debug("Torrent polling error (non-fatal): %s",
                                     poll_err)
                else:
                    app_state.update_activity(
                        activity_id, status="completed", progress=1.0)
                    time.sleep(5)
                    app_state.remove_activity(activity_id)
                    activity_id = None

                # ── Log download + update content library ─────────
                try:
                    app_state.db.add_download_log(
                        title=clean_title,
                        release_title=release.title,
                        media_type=media_type,
                        imdb_id=imdb_id,
                        tmdb_id=tmdb_id,
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
                    if is_anime:
                        if media_type == "movie":
                            media_type = "anime_movie"
                        elif media_type == "show":
                            media_type = "anime_show"

                    app_state.db.upsert_content_item(
                        imdb_id=imdb_id,
                        tmdb_id=tmdb_id,
                        title=clean_title,
                        media_type=media_type,
                        year=getattr(element, 'year', None),
                        status="collected",
                        source="automation",
                    )
                except Exception:
                    logger.debug("Failed to update content item")

                return True

            except Exception as e:
                logger.exception("Decypharr download error for %s",
                                 release.title[:60])
                app_state.add_log(f"✗ Decypharr error: {e}")
                return False
            finally:
                if activity_id:
                    try:
                        app_state.remove_activity(activity_id)
                    except Exception:
                        pass

        # Patch the global debrid.download function
        debrid.download = _decypharr_download
        self.app_state.add_log("Download routing: Decypharr")

    def _log_all_watchlist_items(self, watchlists):
        """Batch-insert all watchlist items to content library before processing."""
        for element in self._unique(watchlists):
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
        self.app_state.add_log(f"Added {len(watchlists)} watchlist items to content library")

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
        except Exception as e:
            logger.debug(f"Failed to log content item: {e}")

    def _process_elements(self, elements, library):
        """Process downloadable elements concurrently (max 4 movies, 1 series)."""
        def _do_download(element):
            if self._stop:
                return
            media_type = getattr(element, 'type', 'movie')
            is_series = media_type in ('show', 'season')
            sem = self._series_sem if is_series else self._movie_sem
            sem.acquire()
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
