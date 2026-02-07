"""
Application state management for pd_reloaded.
"""

import asyncio
import logging
from threading import Thread
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class AppState:
    """Centralized application state."""

    def __init__(self):
        self.config_dir = "."
        self.db = None
        self.needs_onboarding = True

        # Automation state
        self.automation_running = False
        self.automation_thread: Optional[Thread] = None
        self.automation_stop = False
        self.automation_logs: List[str] = []
        self.max_log_lines = 500

        # Scraper state
        self.scrape_results: List[Dict] = []
        self.scrape_in_progress = False

        # Decypharr client (set by automation engine)
        self.decypharr_client = None

        # Connected clients for live updates
        self._update_callbacks = []

    def add_log(self, message: str):
        """Add a log message to the automation log buffer."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self.automation_logs.append(entry)
        if len(self.automation_logs) > self.max_log_lines:
            self.automation_logs = self.automation_logs[-self.max_log_lines:]
        # Notify UI
        self._notify_updates("log", entry)

    def clear_logs(self):
        self.automation_logs.clear()

    def register_update_callback(self, callback):
        self._update_callbacks.append(callback)

    def unregister_update_callback(self, callback):
        if callback in self._update_callbacks:
            self._update_callbacks.remove(callback)

    def _notify_updates(self, event_type: str, data: Any = None):
        for callback in self._update_callbacks[:]:
            try:
                callback(event_type, data)
            except Exception:
                pass

    def get_stats(self) -> Dict[str, Any]:
        """Get dashboard statistics."""
        if not self.db:
            return {}
        
        all_content = self.db.get_all_content()
        movies = [c for c in all_content if c["media_type"] == "movie"]
        # Shows that are NOT anime
        shows = [c for c in all_content if c["media_type"] in ("show", "anime_show") and not c.get("is_anime")]
        # Anime = shows with anime/animation genre
        anime = [c for c in all_content if c.get("is_anime")]
        
        collected = [c for c in all_content if c["status"] == "collected"]
        downloading = [c for c in all_content if c["status"] == "downloading"]
        watchlisted = [c for c in all_content if c["status"] == "watchlisted"]
        
        logs = self.db.get_download_logs(limit=50)
        ignored = self.db.get_ignored_items()
        
        return {
            "total_content": len(all_content),
            "movies": len(movies),
            "shows": len(shows),
            "anime": len(anime),
            "collected": len(collected),
            "downloading": len(downloading),
            "watchlisted": len(watchlisted),
            "recent_downloads": len(logs),
            "ignored_count": len(ignored),
            "automation_running": self.automation_running,
        }
