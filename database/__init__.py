"""
Database module for plex_debrid.
Uses SQLite via SQLAlchemy for lightweight persistent storage.
"""

from database.models import (
    init_db, get_session, get_engine,
    Setting, ContentItem, DownloadLog, IgnoredItem, PlexUser, TraktUser,
    DebridService, ScraperSource, ReleaseVersion, ReleaseRule, TrackerRule
)
from database.manager import DatabaseManager

db = DatabaseManager()
