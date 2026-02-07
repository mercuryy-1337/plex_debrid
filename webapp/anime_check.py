"""
Anime detection using the Anime-Lists animetitles XML.

Uses https://github.com/Anime-Lists/anime-lists animetitles.xml to determine
if a title belongs to anime content via case-insensitive name matching.
The list is cached for 1 hour.
"""

import logging
import time
import requests
import xml.etree.ElementTree as ET
from threading import Lock

logger = logging.getLogger(__name__)

ANIMETITLES_URL = "https://raw.githubusercontent.com/Anime-Lists/anime-lists/master/animetitles.xml"

# Module-level cache
_anime_titles: set = set()
_last_update: float = 0
_lock = Lock()
_TTL = 43200  # 12 hours


def preload_cache():
    """Eagerly fetch and cache the animetitles list.

    Call once at app startup so the first anime check is instant.
    """
    with _lock:
        _refresh_cache()


def _refresh_cache():
    """Fetch the animetitles XML and build a set of lowercase title strings."""
    global _anime_titles, _last_update

    try:
        resp = requests.get(ANIMETITLES_URL, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        new_titles = set()
        for anime in root.findall("anime"):
            for title_elem in anime.findall("title"):
                title_type = title_elem.get("type", "")
                if title_type in ("main", "official", "short"):
                    text = title_elem.text
                    if text:
                        new_titles.add(text.strip().lower())

        _anime_titles = new_titles
        _last_update = time.time()
        logger.info(f"Animetitles loaded: {len(new_titles)} titles")
    except Exception as e:
        logger.warning(f"Failed to fetch animetitles: {e}")


def is_anime(title: str) -> bool:
    """
    Check if a given title belongs to anime content based on the
    animetitles XML (case-insensitive name matching).

    Returns False if the list can't be fetched or the title is not found.
    """
    if not title:
        return False

    with _lock:
        if time.time() - _last_update > _TTL:
            _refresh_cache()

    return title.strip().lower() in _anime_titles
