"""
Cinemeta feed cache for pd_reloaded.

Fetches https://cinemeta-catalogs.strem.io/feed.json and caches it
in memory with a 12-hour TTL.  Used by the search bar to provide
instant suggestions as the user types.
"""

import logging
import time
import requests
from threading import Lock

logger = logging.getLogger(__name__)

FEED_URL = "https://cinemeta-catalogs.strem.io/feed.json"

# Module-level cache
_feed_items: list = []      # raw dicts from the feed
_last_update: float = 0
_lock = Lock()
_TTL = 43200  # 12 hours


def preload_feed():
    """Eagerly fetch and cache the cinemeta feed.

    Call once at app startup (in a daemon thread) so the first search
    is instant.
    """
    with _lock:
        _refresh_feed()


def _refresh_feed():
    """Fetch the cinemeta feed JSON and store the items list."""
    global _feed_items, _last_update

    try:
        resp = requests.get(FEED_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # The feed is a flat list of items (movies + series)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Some feeds wrap in {"metas": [...]}
            items = data.get("metas") or data.get("feed") or data.get("items") or []
        else:
            items = []

        _feed_items = items
        _last_update = time.time()
        logger.info("Cinemeta feed loaded: %d items", len(items))
    except Exception as e:
        logger.warning("Failed to fetch cinemeta feed: %s", e)


def _ensure_fresh():
    """Refresh the cache if stale (called under lock)."""
    if time.time() - _last_update > _TTL:
        _refresh_feed()


def get_all() -> list:
    """Return the full cached feed (refreshes if stale)."""
    with _lock:
        _ensure_fresh()
    return _feed_items


def search(query: str, limit: int = 20) -> list:
    """Search the cached feed by name (case-insensitive substring match).

    Returns up to *limit* items sorted by popularity (highest first).
    """
    if not query or not query.strip():
        return []

    with _lock:
        _ensure_fresh()
        items = _feed_items

    q = query.strip().lower()
    matches = [
        item for item in items
        if q in (item.get("name") or "").lower()
    ]

    # Sort by popularity descending, then by name
    matches.sort(key=lambda x: (-(x.get("popularity") or 0), (x.get("name") or "").lower()))
    return matches[:limit]


def search_grouped(query: str, limit_per_type: int = 20) -> dict:
    """Search and return results grouped by type.

    Returns ``{"movie": [...], "series": [...]}``.
    """
    if not query or not query.strip():
        return {"movie": [], "series": []}

    with _lock:
        _ensure_fresh()
        items = _feed_items

    q = query.strip().lower()
    movies = []
    series = []

    for item in items:
        name = (item.get("name") or "").lower()
        if q in name:
            t = (item.get("type") or "").lower()
            if t == "series":
                series.append(item)
            else:
                movies.append(item)

    movies.sort(key=lambda x: (-(x.get("popularity") or 0), (x.get("name") or "").lower()))
    series.sort(key=lambda x: (-(x.get("popularity") or 0), (x.get("name") or "").lower()))

    return {
        "movie": movies[:limit_per_type],
        "series": series[:limit_per_type],
    }
