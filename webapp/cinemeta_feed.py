"""
Cinemeta feed cache for pd_reloaded.

Fetches https://cinemeta-catalogs.strem.io/feed.json and caches it
in memory with a 12-hour TTL.  Used by the search bar to provide
instant suggestions as the user types.

Search is powered by a Python port of Stremio's local-search algorithm
(https://github.com/Stremio/local-search, MIT licence) which provides
TF-IDF scoring, Levenshtein fuzzy matching and prefix boosting so
minor typos like "jujutsu kisen" still return the correct results.
"""

import logging
import time
import requests
from threading import Lock
from typing import Optional

from webapp.local_search import LocalSearch

logger = logging.getLogger(__name__)

FEED_URL = "https://cinemeta-catalogs.strem.io/feed.json"

# Module-level cache
_feed_items: list = []       # raw dicts from the feed
_search_index: Optional[LocalSearch] = None
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
    """Fetch the cinemeta feed JSON, store items, and rebuild the search index."""
    global _feed_items, _search_index, _last_update

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

        # Build the intelligent search index
        # Boost by popularity so well-known titles float to the top
        max_pop = max((item.get("popularity") or 0 for item in items), default=1) or 1

        def _boost(item):
            pop = item.get("popularity") or 0
            rating = 0
            try:
                rating = float(item.get("imdbRating") or 0)
            except (ValueError, TypeError):
                pass
            # Normalised popularity + small IMDB nudge
            return math.exp((pop / max_pop) * 0.8 + (rating / 10) * 0.2)

        import math
        _search_index = LocalSearch(
            items,
            text_fn=lambda item: item.get("name") or "",
            boost_fn=_boost,
            max_edit_distance=1,
            max_edit_distance_boost=2.0,
            max_prefix_boost=1.5,
            score_threshold=0.40,
        )

        _last_update = time.time()
        logger.info("Cinemeta feed loaded: %d items, search index built", len(items))
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


def search(query: str, limit: int = 5) -> list:
    """Search the cached feed using Stremio-style intelligent local search.

    Handles typos, partial matches and prefix queries via TF-IDF +
    Levenshtein fuzzy matching + prefix boosting.

    Returns up to *limit* items sorted by relevance score.
    """
    if not query or not query.strip():
        return []

    with _lock:
        _ensure_fresh()
        idx = _search_index

    if idx is None:
        return []

    results = idx.search(query.strip(), max_results=limit)
    return [item for item, _score in results]


def search_grouped(query: str, limit_per_type: int = 20) -> dict:
    """Search Cinemeta catalogs directly via their search API.

    Queries both the movie and series catalogs in parallel and returns
    ``{"movie": [...], "series": [...]}``.
    """
    if not query or not query.strip():
        return {"movie": [], "series": []}

    from urllib.parse import quote
    from concurrent.futures import ThreadPoolExecutor

    q = quote(query.strip())
    movie_url = f"https://v3-cinemeta.strem.io/catalog/movie/top/search={q}.json"
    series_url = f"https://v3-cinemeta.strem.io/catalog/series/top/search={q}.json"

    def _fetch(url):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("metas", [])
        except Exception as e:
            logger.debug("Cinemeta search failed for %s: %s", url, e)
            return []

    with ThreadPoolExecutor(max_workers=2) as pool:
        movie_future = pool.submit(_fetch, movie_url)
        series_future = pool.submit(_fetch, series_url)
        movies = movie_future.result()
        series = series_future.result()

    return {
        "movie": movies[:limit_per_type],
        "series": series[:limit_per_type],
    }
