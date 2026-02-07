"""
SQLAlchemy models for pd_reloaded.
"""

import os
import json
import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean, Float,
    DateTime, ForeignKey, JSON, Enum as SAEnum
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool

Base = declarative_base()
_engine = None
_session_factory = None


def get_db_path(config_dir="."):
    return os.path.join(config_dir, "pd_reloaded.db")


def get_engine(config_dir="."):
    global _engine
    if _engine is None:
        db_path = get_db_path(config_dir)
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
    return _engine


def get_session(config_dir="."):
    global _session_factory
    if _session_factory is None:
        engine = get_engine(config_dir)
        _session_factory = scoped_session(sessionmaker(bind=engine))
    return _session_factory()


def init_db(config_dir="."):
    engine = get_engine(config_dir)
    Base.metadata.create_all(engine)


# ─── Settings ──────────────────────────────────────────────────────────
class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)  # JSON-encoded
    category = Column(String(100), nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def get_value(self):
        if self.value is None:
            return None
        try:
            return json.loads(self.value)
        except (json.JSONDecodeError, TypeError):
            return self.value

    def set_value(self, val):
        self.value = json.dumps(val)


# ─── Content Items (fetched/approved media) ────────────────────────────
class ContentItem(Base):
    __tablename__ = "content_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    media_type = Column(String(20), nullable=False)  # movie, show, anime_movie, anime_show
    year = Column(Integer, nullable=True)
    imdb_id = Column(String(20), nullable=True, index=True)
    tmdb_id = Column(String(20), nullable=True, index=True)
    tvdb_id = Column(String(20), nullable=True)
    genres = Column(Text, nullable=True)  # JSON list
    status = Column(String(20), default="watchlisted")  # watchlisted, downloading, collected, ignored
    source = Column(String(50), nullable=True)  # plex, trakt, overseerr
    requested_by = Column(String(100), nullable=True)
    season_count = Column(Integer, nullable=True)
    episode_count = Column(Integer, nullable=True)
    poster_url = Column(String(500), nullable=True)
    backdrop_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    downloads = relationship("DownloadLog", back_populates="content_item", cascade="all, delete-orphan")

    def get_genres(self):
        if self.genres:
            try:
                return json.loads(self.genres)
            except:
                return []
        return []

    @property
    def is_anime(self):
        from webapp.anime_check import is_anime as check_anime
        if self.title:
            return check_anime(self.title)
        return self.media_type == "anime_show"


# ─── Download Logs ────────────────────────────────────────────────────
class DownloadLog(Base):
    __tablename__ = "download_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content_item_id = Column(Integer, ForeignKey("content_items.id"), nullable=True)
    title = Column(String(500), nullable=False)
    release_title = Column(String(500), nullable=True)
    media_type = Column(String(20), nullable=True)
    imdb_id = Column(String(20), nullable=True)
    tmdb_id = Column(String(20), nullable=True)
    debrid_service = Column(String(50), nullable=True)
    scraper_source = Column(String(50), nullable=True)
    resolution = Column(String(20), nullable=True)
    size_gb = Column(Float, nullable=True)
    cached = Column(Boolean, default=True)
    status = Column(String(20), default="completed")  # completed, failed, pending
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    content_item = relationship("ContentItem", back_populates="downloads")


# ─── Ignored Items ────────────────────────────────────────────────────
class IgnoredItem(Base):
    __tablename__ = "ignored_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    imdb_id = Column(String(20), nullable=True, index=True)
    tmdb_id = Column(String(20), nullable=True)
    media_type = Column(String(20), nullable=True)
    reason = Column(String(200), nullable=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=48)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ─── Plex Users ───────────────────────────────────────────────────────
class PlexUser(Base):
    __tablename__ = "plex_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(200), nullable=False)
    token = Column(String(500), nullable=False)
    client_id = Column(String(200), nullable=True)
    server_name = Column(String(200), nullable=True)
    server_url = Column(String(500), nullable=True)
    server_machine_id = Column(String(200), nullable=True)
    library_sections = Column(Text, nullable=True)  # JSON
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ─── Trakt Users ──────────────────────────────────────────────────────
class TraktUser(Base):
    __tablename__ = "trakt_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(200), nullable=False)
    access_token = Column(String(500), nullable=True)
    refresh_token = Column(String(500), nullable=True)
    client_id = Column(String(200), nullable=True)
    lists = Column(Text, nullable=True)  # JSON list
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ─── Debrid Services ─────────────────────────────────────────────────
class DebridService(Base):
    __tablename__ = "debrid_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    short_name = Column(String(10), nullable=False)
    api_key = Column(String(500), nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ─── Scraper Sources ─────────────────────────────────────────────────
class ScraperSource(Base):
    __tablename__ = "scraper_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    enabled = Column(Boolean, default=True)
    config = Column(Text, nullable=True)  # JSON config (base_url, api_key, etc.)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ─── Release Versions ────────────────────────────────────────────────
class ReleaseVersion(Base):
    __tablename__ = "release_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    enabled = Column(Boolean, default=True)
    category = Column(String(100), nullable=False, default="default")
    category_api_key = Column(String(64), nullable=True)
    triggers = Column(Text, nullable=True)  # JSON
    language = Column(String(10), default="en")
    rules = Column(Text, nullable=True)  # JSON
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ─── Release Rules (renaming etc.) ───────────────────────────────────
class ReleaseRule(Base):
    __tablename__ = "release_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_type = Column(String(50), nullable=False)  # rename, filter, etc.
    pattern = Column(String(500), nullable=False)
    replacement = Column(String(500), default="")
    enabled = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)


# ─── Tracker Rules ───────────────────────────────────────────────────
class TrackerRule(Base):
    __tablename__ = "tracker_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracker_pattern = Column(String(500), nullable=False)
    debrid_short = Column(String(10), nullable=False)
    enabled = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
