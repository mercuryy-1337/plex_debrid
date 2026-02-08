"""
Application state management for pd_reloaded.
"""

import asyncio
import datetime
import logging
import uuid
from threading import Thread, Lock
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# ─── Activity item status flow ──────────────────────────────────────
# sent → sent_to_decypharr → downloading → downloaded → processing → completed
ACTIVITY_STATUSES = ("sent", "sent_to_decypharr", "downloading", "downloaded", "processing", "completed")


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

        # ── Activity queue ──────────────────────────────────────────
        self._activity_items: Dict[str, Dict[str, Any]] = {}  # keyed by id
        self._activity_lock = Lock()

    # ── Activity helpers ────────────────────────────────────────────

    def add_activity(self, *, title: str, release_title: str,
                     info_hash: str = "", category: str = "",
                     imdb_id: str = "") -> str:
        """Register a new activity item (status=sent). Returns item id."""
        item_id = uuid.uuid4().hex[:12]
        now = datetime.datetime.now()
        with self._activity_lock:
            self._activity_items[item_id] = {
                "id": item_id,
                "title": title,             # movie / series name
                "release_title": release_title,  # torrent name
                "info_hash": info_hash,
                "imdb_id": imdb_id,
                "category": category,
                "status": "sent",
                "progress": 0.0,
                "submitted_at": now,
                "updated_at": now,
            }
        self._notify_updates("activity", {"action": "add", "id": item_id})
        return item_id

    def update_activity(self, item_id: str, **kwargs):
        """Update fields on an activity item (status, progress, etc.)."""
        with self._activity_lock:
            item = self._activity_items.get(item_id)
            if not item:
                return
            for k, v in kwargs.items():
                item[k] = v
            item["updated_at"] = datetime.datetime.now()
        self._notify_updates("activity", {"action": "update", "id": item_id})

    def remove_activity(self, item_id: str):
        """Remove an activity item from the queue."""
        with self._activity_lock:
            self._activity_items.pop(item_id, None)
        self._notify_updates("activity", {"action": "remove", "id": item_id})

    def get_activities(self) -> List[Dict[str, Any]]:
        """Return all activity items sorted newest-first."""
        with self._activity_lock:
            items = list(self._activity_items.values())
        items.sort(key=lambda x: x["submitted_at"], reverse=True)
        return items

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
