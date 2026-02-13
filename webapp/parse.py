"""
Torrent / filename parser for TV shows and anime.

Centralises season-pack detection and season + episode extraction so that
every part of the application uses the same logic.
"""

from __future__ import annotations

import re
from enum import Enum, auto


# Pack-type categories

class PackType(Enum):
    """Categories of season-pack indicators found in release titles."""

    STANDARD_EP_RANGE = auto()
    SEASON_ONLY       = auto()
    EXPLICIT_KEYWORD  = auto()
    ANIME_BATCH_TAG   = auto()
    ANIME_TILDE_RANGE = auto()
    NOT_A_PACK        = auto()


# TorrentParser

class TorrentParser:
    """Parse torrent and file names for TV shows and anime."""

    # Primary episode matcher for shows/anime.
    _EPISODE_RE = re.compile(
        r'(.*?)'
        r'(S\d{2}.? ?E\d{2,3}(?:\-E\d{2})?'
        r'|\b\d{1,2}x\d{2}\b'
        r'|S\d{2}E\d{2}-?(?:E\d{2})'
        r'|S\d{2,3} ?E\d{2}(?:\+E\d{2})?)',
        re.IGNORECASE,
    )

    # Anime-specific patterns (priority order).
    _ANIME_SEASON_DASH_EP = re.compile(
        r'S(\d{1,2})[\s.]*-[\s.]*(\d{1,4})(?:v\d+)?',
        re.IGNORECASE,
    )

    _ANIME_ORDINAL_SEASON = re.compile(
        r'(\d{1,2})(?:st|nd|rd|th)?[\s.]*Season[\s.]*-[\s.]*(\d{1,4})(?:v\d+)?',
        re.IGNORECASE,
    )

    _ANIME_BARE_SEASON = re.compile(
        r'(?:^|[\s.\]])(\d{1,2})[\s.]*-[\s.]*(\d{1,4})(?:v\d+)?'
        r'(?:[\s.]*(?:END|FINAL))?'
        r'(?=[\s.\[\(]|$)',
        re.IGNORECASE,
    )

    _ANIME_NO_SEASON = re.compile(
        r'[\s.]-[\s.](\d{1,4})(?:v\d+)?'
        r'(?:[\s.]*(?:END|FINAL))?'
        r'(?=[\s.\[\(]|$)',
        re.IGNORECASE,
    )

    # Prevent false pack positives.
    _SINGLE_EP_STANDARD = re.compile(
        r'S\d{1,2}[\s.]?E\d{1,3}(?!\s?-\s?E?\d)', re.IGNORECASE,
    )
    _SINGLE_EP_SEASON_DASH = re.compile(
        r'SEASON[\s.]*\d+[\s.]*-[\s.]*\d+(?![\s.]*~)', re.IGNORECASE,
    )
    _SINGLE_EP_ANIME_DASH = re.compile(
        r'S\d{1,2}[\s.]*-[\s.]*\d{1,3}(?![\s.]*~)', re.IGNORECASE,
    )

    _PACK_EP_RANGE = re.compile(
        r'S\d{1,2}[\s.]?E\d{1,3}\s?-\s?E?\d{1,3}', re.IGNORECASE,
    )
    _PACK_SEASON_ONLY = re.compile(
        r'S\d{1,2}(?![\s.]?E\d)', re.IGNORECASE,
    )
    _PACK_KEYWORD = re.compile(
        r'(?:SEASON|COMPLETE|FULL\.SEASON)', re.IGNORECASE,
    )
    _PACK_BATCH_TAG = re.compile(r'\[BATCH\]', re.IGNORECASE)
    _PACK_TILDE_RANGE = re.compile(r'\d{1,3}[\s.]*~[\s.]*\d{1,3}')

    # (PackType, regex, requires_no_single_episode)
    _PACK_RULES: list[tuple[PackType, re.Pattern, bool]] = [
        (PackType.STANDARD_EP_RANGE,  _PACK_EP_RANGE,    False),
        (PackType.ANIME_BATCH_TAG,    _PACK_BATCH_TAG,   False),
        (PackType.ANIME_TILDE_RANGE,  _PACK_TILDE_RANGE, False),
        (PackType.SEASON_ONLY,        _PACK_SEASON_ONLY, True),
        (PackType.EXPLICIT_KEYWORD,   _PACK_KEYWORD,     True),
    ]

    # Formatting helper

    @staticmethod
    def _format_se(season: int, episode: int) -> str:
        """Format season + episode as ``SxxExx`` (variable episode width)."""
        if episode >= 1000:
            return f"S{season:02d}E{episode:04d}"
        elif episode >= 100:
            return f"S{season:02d}E{episode:03d}"
        else:
            return f"S{season:02d}E{episode:02d}"

    # Season-pack detection

    @classmethod
    def classify_pack(cls, title: str) -> PackType:
        """Classify a title into a season-pack category."""
        has_single = cls._has_single_episode(title)

        for pack_type, pattern, needs_no_single in cls._PACK_RULES:
            if pattern.search(title) and (not needs_no_single or not has_single):
                return pack_type

        return PackType.NOT_A_PACK

    @classmethod
    def is_season_pack(cls, title: str) -> bool:
        """Return ``True`` when *title* looks like a season pack."""
        match cls.classify_pack(title):
            case PackType.NOT_A_PACK:
                return False
            case _:
                return True

    # Season / episode extraction

    @classmethod
    def extract_season_episode(
        cls, filename: str, *, is_anime: bool = False,
    ) -> str | None:
        """Extract season + episode from a filename as ``SxxExx``."""
        result = cls._extract_show_episode(filename)
        if result:
            return result

        if is_anime:
            return cls._extract_anime_episode(filename)

        return None

    # Internals

    @classmethod
    def _has_single_episode(cls, title: str) -> bool:
        """Detect single-episode markers that should prevent pack classification."""
        if cls._SINGLE_EP_STANDARD.search(title):
            return True
        if cls._SINGLE_EP_SEASON_DASH.search(title):
            return True
        if cls._SINGLE_EP_ANIME_DASH.search(title):
            return True
        return False

    @classmethod
    def _extract_show_episode(cls, filename: str) -> str | None:
        """Extract first season/episode pair with standard TV patterns."""
        m = cls._EPISODE_RE.search(filename)
        if not m:
            return None
        raw = m.group(2).strip()

        xm = re.match(r'(\d{1,2})x(\d{2,3})', raw, re.IGNORECASE)
        if xm:
            return cls._format_se(int(xm.group(1)), int(xm.group(2)))

        norm = re.sub(r'(?<=\d)[.\s]+(?=[Ee])', '', raw)
        se = re.match(r'S(\d{2,3})E(\d{2,4})', norm, re.IGNORECASE)
        if se:
            return cls._format_se(int(se.group(1)), int(se.group(2)))

        return None

    @classmethod
    def _extract_anime_episode(cls, filename: str) -> str | None:
        """Anime fallback extraction when standard matching fails."""
        match True:
            case _ if (m := cls._ANIME_SEASON_DASH_EP.search(filename)):
                return cls._format_se(int(m.group(1)), int(m.group(2)))

            case _ if (m := cls._ANIME_ORDINAL_SEASON.search(filename)):
                return cls._format_se(int(m.group(1)), int(m.group(2)))

            case _ if (m := cls._ANIME_BARE_SEASON.search(filename)):
                season = int(m.group(1))
                episode = int(m.group(2))
                if 1 <= season <= 20:
                    return cls._format_se(season, episode)
                m2 = cls._ANIME_NO_SEASON.search(filename)
                if m2:
                    return cls._format_se(1, int(m2.group(1)))
                return None

            case _ if (m := cls._ANIME_NO_SEASON.search(filename)):
                return cls._format_se(1, int(m.group(1)))

            case _:
                return None
