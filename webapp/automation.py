"""
Automation engine for pd_reloaded.
Bridges the legacy download automation with the new web UI.
"""

import time
import logging
import itertools
from threading import Thread

logger = logging.getLogger(__name__)


class AutomationEngine:
    """Manages the background download automation loop."""

    def __init__(self, app_state):
        self.app_state = app_state
        self._thread = None
        self._stop = False

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
            t0 = time.time()
            for element in self._unique(watchlists):
                if self._stop:
                    break
                if hasattr(element, 'download'):
                    self._log_content_item(element)
                    element.download(library=library)
                    t1 = time.time()
                    if t1 - t0 >= 5:
                        if plex_watchlist.update() or overseerr_requests.update() or trakt_watchlist.update():
                            lib_services = content.classes.library()
                            library = lib_services[0]() if lib_services else []
                            new_wl = plex_watchlist + trakt_watchlist + overseerr_requests
                            try:
                                new_wl.data.sort(key=lambda s: s.watchlistedAt, reverse=True)
                            except Exception:
                                pass
                            new_wl = self._unique(new_wl)
                            for el in new_wl[:]:
                                if el in watchlists:
                                    new_wl.remove(el)
                            self.app_state.add_log("Found new content while processing...")
                            for el in new_wl:
                                if self._stop:
                                    break
                                if hasattr(el, 'download'):
                                    self._log_content_item(el)
                                    el.download(library=library)
                        t0 = time.time()
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
                                self._log_content_item(element)
                                element.download(library=library)
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

                    for element in self._unique(watchlists):
                        if self._stop:
                            break
                        if hasattr(element, 'download'):
                            self._log_content_item(element)
                            element.download(library=library)
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

        self.app_state.add_log("Settings loaded into modules")

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
