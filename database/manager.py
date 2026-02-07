"""
Database manager for pd_reloaded.
Provides high-level CRUD operations and settings migration from legacy JSON.
"""

import os
import json
import datetime
import logging
from contextlib import contextmanager

from database.models import (
    init_db, get_session, get_engine, Base,
    Setting, ContentItem, DownloadLog, IgnoredItem, PlexUser, TraktUser,
    DebridService, ScraperSource, ReleaseVersion, ReleaseRule, TrackerRule
)

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, config_dir="."):
        self.config_dir = config_dir
        self._initialized = False

    def initialize(self, config_dir=None):
        if config_dir:
            self.config_dir = config_dir
        init_db(self.config_dir)
        self._initialized = True
        logger.info(f"Database initialized at {self.config_dir}")

    @contextmanager
    def session_scope(self):
        session = get_session(self.config_dir)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def is_setup_complete(self):
        """Check if the app has been set up (DB exists and has required settings)."""
        db_path = os.path.join(self.config_dir, "pd_reloaded.db")
        if not os.path.exists(db_path):
            return False
        try:
            with self.session_scope() as session:
                setup_done = session.query(Setting).filter_by(key="setup_complete").first()
                return setup_done is not None and setup_done.get_value() is True
        except Exception:
            return False

    def has_legacy_settings(self):
        """Check if legacy settings.json exists."""
        legacy_path = os.path.join(self.config_dir, "settings.json")
        return os.path.exists(legacy_path) and os.path.getsize(legacy_path) > 0

    # ─── Settings CRUD ────────────────────────────────────────────────
    def get_setting(self, key, default=None):
        with self.session_scope() as session:
            setting = session.query(Setting).filter_by(key=key).first()
            if setting:
                return setting.get_value()
            return default

    def set_setting(self, key, value, category=None):
        with self.session_scope() as session:
            setting = session.query(Setting).filter_by(key=key).first()
            if setting:
                setting.set_value(value)
                if category:
                    setting.category = category
            else:
                setting = Setting(key=key, category=category)
                setting.set_value(value)
                session.add(setting)

    def get_all_settings(self, category=None):
        with self.session_scope() as session:
            query = session.query(Setting)
            if category:
                query = query.filter_by(category=category)
            return {s.key: s.get_value() for s in query.all()}

    def delete_setting(self, key):
        with self.session_scope() as session:
            session.query(Setting).filter_by(key=key).delete()

    # ─── Content Items CRUD ──────────────────────────────────────────
    def add_content_item(self, **kwargs):
        with self.session_scope() as session:
            if "genres" in kwargs and isinstance(kwargs["genres"], list):
                kwargs["genres"] = json.dumps(kwargs["genres"])
            item = ContentItem(**kwargs)
            session.add(item)
            session.flush()
            return item.id

    def get_content_item(self, item_id=None, imdb_id=None, tmdb_id=None):
        with self.session_scope() as session:
            if item_id:
                return session.query(ContentItem).get(item_id)
            if imdb_id:
                return session.query(ContentItem).filter_by(imdb_id=imdb_id).first()
            if tmdb_id:
                return session.query(ContentItem).filter_by(tmdb_id=tmdb_id).first()
            return None

    def get_all_content(self, media_type=None, status=None):
        with self.session_scope() as session:
            query = session.query(ContentItem)
            if media_type:
                if media_type == "anime":
                    query = query.filter(ContentItem.media_type.in_(["anime_movie", "anime_show"]))
                else:
                    query = query.filter_by(media_type=media_type)
            if status:
                query = query.filter_by(status=status)
            items = query.order_by(ContentItem.updated_at.desc()).all()
            # Detach from session
            result = []
            for item in items:
                result.append({
                    "id": item.id,
                    "title": item.title,
                    "media_type": item.media_type,
                    "year": item.year,
                    "imdb_id": item.imdb_id,
                    "tmdb_id": item.tmdb_id,
                    "tvdb_id": item.tvdb_id,
                    "genres": item.get_genres(),
                    "status": item.status,
                    "source": item.source,
                    "requested_by": item.requested_by,
                    "poster_url": item.poster_url,
                    "backdrop_url": item.backdrop_url,
                    "is_anime": item.is_anime,
                    "created_at": str(item.created_at) if item.created_at else None,
                    "updated_at": str(item.updated_at) if item.updated_at else None,
                })
            return result

    def update_content_status(self, item_id, status):
        with self.session_scope() as session:
            item = session.query(ContentItem).get(item_id)
            if item:
                item.status = status

    def upsert_content_item(self, imdb_id=None, tmdb_id=None, **kwargs):
        """Insert or update a content item by IMDB or TMDB ID."""
        with self.session_scope() as session:
            item = None
            if imdb_id:
                item = session.query(ContentItem).filter_by(imdb_id=imdb_id).first()
            elif tmdb_id:
                item = session.query(ContentItem).filter_by(tmdb_id=tmdb_id).first()
            if "genres" in kwargs and isinstance(kwargs["genres"], list):
                kwargs["genres"] = json.dumps(kwargs["genres"])
            if item:
                for k, v in kwargs.items():
                    setattr(item, k, v)
                return item.id
            else:
                if imdb_id:
                    kwargs["imdb_id"] = imdb_id
                if tmdb_id:
                    kwargs["tmdb_id"] = tmdb_id
                item = ContentItem(**kwargs)
                session.add(item)
                session.flush()
                return item.id

    # ─── Download Logs CRUD ──────────────────────────────────────────
    def add_download_log(self, **kwargs):
        with self.session_scope() as session:
            log = DownloadLog(**kwargs)
            session.add(log)
            session.flush()
            return log.id

    def get_download_logs(self, content_item_id=None, limit=100):
        with self.session_scope() as session:
            query = session.query(DownloadLog)
            if content_item_id:
                query = query.filter_by(content_item_id=content_item_id)
            logs = query.order_by(DownloadLog.created_at.desc()).limit(limit).all()
            result = []
            for log in logs:
                result.append({
                    "id": log.id,
                    "content_item_id": log.content_item_id,
                    "title": log.title,
                    "release_title": log.release_title,
                    "media_type": log.media_type,
                    "imdb_id": log.imdb_id,
                    "tmdb_id": log.tmdb_id,
                    "debrid_service": log.debrid_service,
                    "scraper_source": log.scraper_source,
                    "resolution": log.resolution,
                    "size_gb": log.size_gb,
                    "cached": log.cached,
                    "status": log.status,
                    "error_message": log.error_message,
                    "created_at": str(log.created_at) if log.created_at else None,
                })
            return result

    # ─── Ignored Items CRUD ──────────────────────────────────────────
    def add_ignored_item(self, **kwargs):
        with self.session_scope() as session:
            item = IgnoredItem(**kwargs)
            session.add(item)

    def remove_ignored_item(self, item_id=None, imdb_id=None):
        with self.session_scope() as session:
            if item_id:
                session.query(IgnoredItem).filter_by(id=item_id).delete()
            elif imdb_id:
                session.query(IgnoredItem).filter_by(imdb_id=imdb_id).delete()

    def get_ignored_items(self):
        with self.session_scope() as session:
            items = session.query(IgnoredItem).all()
            return [{
                "id": i.id, "title": i.title, "imdb_id": i.imdb_id,
                "tmdb_id": i.tmdb_id, "media_type": i.media_type,
                "reason": i.reason, "retry_count": i.retry_count,
                "created_at": str(i.created_at) if i.created_at else None
            } for i in items]

    def is_ignored(self, imdb_id=None, title=None):
        with self.session_scope() as session:
            if imdb_id:
                return session.query(IgnoredItem).filter_by(imdb_id=imdb_id).first() is not None
            if title:
                return session.query(IgnoredItem).filter_by(title=title).first() is not None
            return False

    # ─── Plex Users CRUD ─────────────────────────────────────────────
    def add_plex_user(self, **kwargs):
        with self.session_scope() as session:
            user = PlexUser(**kwargs)
            session.add(user)
            session.flush()
            return user.id

    def get_plex_users(self):
        with self.session_scope() as session:
            users = session.query(PlexUser).all()
            return [{
                "id": u.id, "username": u.username, "token": u.token,
                "client_id": u.client_id, "server_name": u.server_name,
                "server_url": u.server_url, "server_machine_id": u.server_machine_id,
                "library_sections": json.loads(u.library_sections) if u.library_sections else [],
                "is_primary": u.is_primary,
            } for u in users]

    def get_primary_plex_user(self):
        with self.session_scope() as session:
            user = session.query(PlexUser).filter_by(is_primary=True).first()
            if user:
                return {
                    "id": user.id, "username": user.username, "token": user.token,
                    "client_id": user.client_id, "server_name": user.server_name,
                    "server_url": user.server_url, "server_machine_id": user.server_machine_id,
                    "library_sections": json.loads(user.library_sections) if user.library_sections else [],
                    "is_primary": user.is_primary,
                }
            return None

    def update_plex_user(self, user_id, **kwargs):
        with self.session_scope() as session:
            user = session.query(PlexUser).get(user_id)
            if user:
                for k, v in kwargs.items():
                    if k == "library_sections" and isinstance(v, list):
                        v = json.dumps(v)
                    setattr(user, k, v)

    def delete_plex_user(self, user_id):
        with self.session_scope() as session:
            session.query(PlexUser).filter_by(id=user_id).delete()

    # ─── Trakt Users CRUD ────────────────────────────────────────────
    def add_trakt_user(self, **kwargs):
        with self.session_scope() as session:
            if "lists" in kwargs and isinstance(kwargs["lists"], list):
                kwargs["lists"] = json.dumps(kwargs["lists"])
            user = TraktUser(**kwargs)
            session.add(user)
            session.flush()
            return user.id

    def get_trakt_users(self):
        with self.session_scope() as session:
            users = session.query(TraktUser).all()
            return [{
                "id": u.id, "username": u.username,
                "access_token": u.access_token, "refresh_token": u.refresh_token,
                "lists": json.loads(u.lists) if u.lists else [],
                "is_primary": u.is_primary,
            } for u in users]

    # ─── Debrid Services CRUD ────────────────────────────────────────
    def add_debrid_service(self, **kwargs):
        with self.session_scope() as session:
            svc = DebridService(**kwargs)
            session.add(svc)

    def get_debrid_services(self, enabled_only=False):
        with self.session_scope() as session:
            query = session.query(DebridService)
            if enabled_only:
                query = query.filter_by(enabled=True)
            return [{
                "id": s.id, "name": s.name, "short_name": s.short_name,
                "api_key": s.api_key, "enabled": s.enabled,
            } for s in query.all()]

    def update_debrid_service(self, service_id, **kwargs):
        with self.session_scope() as session:
            svc = session.query(DebridService).get(service_id)
            if svc:
                for k, v in kwargs.items():
                    setattr(svc, k, v)

    # ─── Scraper Sources CRUD ────────────────────────────────────────
    def add_scraper_source(self, **kwargs):
        if "config" in kwargs and isinstance(kwargs["config"], dict):
            kwargs["config"] = json.dumps(kwargs["config"])
        with self.session_scope() as session:
            src = ScraperSource(**kwargs)
            session.add(src)

    def get_scraper_sources(self, enabled_only=False):
        with self.session_scope() as session:
            query = session.query(ScraperSource)
            if enabled_only:
                query = query.filter_by(enabled=True)
            return [{
                "id": s.id, "name": s.name, "enabled": s.enabled,
                "config": json.loads(s.config) if s.config else {},
            } for s in query.all()]

    def update_scraper_source(self, source_id, **kwargs):
        if "config" in kwargs and isinstance(kwargs["config"], dict):
            kwargs["config"] = json.dumps(kwargs["config"])
        with self.session_scope() as session:
            src = session.query(ScraperSource).get(source_id)
            if src:
                for k, v in kwargs.items():
                    setattr(src, k, v)

    # ─── Release Versions CRUD ───────────────────────────────────────
    def add_release_version(self, **kwargs):
        for key in ("triggers", "rules"):
            if key in kwargs and isinstance(kwargs[key], (list, dict)):
                kwargs[key] = json.dumps(kwargs[key])
        with self.session_scope() as session:
            ver = ReleaseVersion(**kwargs)
            session.add(ver)
            session.flush()
            return ver.id

    def get_release_versions(self, enabled_only=False):
        with self.session_scope() as session:
            query = session.query(ReleaseVersion)
            if enabled_only:
                query = query.filter_by(enabled=True)
            versions = query.order_by(ReleaseVersion.sort_order).all()
            return [{
                "id": v.id, "name": v.name, "enabled": v.enabled,
                "category": v.category or "default",
                "triggers": json.loads(v.triggers) if v.triggers else [],
                "language": v.language,
                "rules": json.loads(v.rules) if v.rules else [],
                "sort_order": v.sort_order,
            } for v in versions]

    def update_release_version(self, version_id, **kwargs):
        """Update a release version by ID."""
        for key in ("triggers", "rules"):
            if key in kwargs and isinstance(kwargs[key], (list, dict)):
                kwargs[key] = json.dumps(kwargs[key])
        with self.session_scope() as session:
            v = session.query(ReleaseVersion).get(version_id)
            if v:
                for k, val in kwargs.items():
                    setattr(v, k, val)

    def delete_release_version(self, version_id):
        """Delete a release version by ID."""
        with self.session_scope() as session:
            session.query(ReleaseVersion).filter_by(id=version_id).delete()

    # ─── Tracker Rules CRUD ──────────────────────────────────────────
    def add_tracker_rule(self, **kwargs):
        with self.session_scope() as session:
            rule = TrackerRule(**kwargs)
            session.add(rule)

    def get_tracker_rules(self, enabled_only=False):
        with self.session_scope() as session:
            query = session.query(TrackerRule)
            if enabled_only:
                query = query.filter_by(enabled=True)
            return [{
                "id": r.id, "tracker_pattern": r.tracker_pattern,
                "debrid_short": r.debrid_short, "enabled": r.enabled,
            } for r in query.order_by(TrackerRule.sort_order).all()]

    # ─── Legacy Migration ────────────────────────────────────────────
    def migrate_from_json(self, json_path=None):
        """Migrate settings from legacy settings.json to database."""
        if json_path is None:
            json_path = os.path.join(self.config_dir, "settings.json")
        if not os.path.exists(json_path):
            logger.warning(f"No legacy settings file found at {json_path}")
            return False

        try:
            with open(json_path, 'r') as f:
                settings = json.loads(f.read())
        except Exception as e:
            logger.error(f"Failed to read legacy settings: {e}")
            return False

        logger.info("Migrating legacy settings to database...")

        # Migrate all flat settings
        for key, value in settings.items():
            self.set_setting(key, value, category="legacy")

        # Migrate Plex users
        if "Plex users" in settings:
            for user_data in settings["Plex users"]:
                if isinstance(user_data, list) and len(user_data) >= 2:
                    self.add_plex_user(
                        username=user_data[0],
                        token=user_data[1],
                        client_id=settings.get("_plex_client_id", ""),
                        is_primary=True
                    )

        # Migrate debrid services
        if "Debrid Services" in settings:
            debrid_map = {
                "Real Debrid": ("RD", "Real Debrid API Key"),
                "Premiumize": ("PM", "Premiumize API Key"),
                "All Debrid": ("AD", "All Debrid API Key"),
                "Put.io": ("PUT", "Put.io API Key"),
                "Debrid Link": ("DL", "Debrid Link API Key"),
            }
            for svc_name in settings["Debrid Services"]:
                if svc_name in debrid_map:
                    short, key_name = debrid_map[svc_name]
                    self.add_debrid_service(
                        name=svc_name,
                        short_name=short,
                        api_key=settings.get(key_name, ""),
                        enabled=True
                    )

        # Migrate scraper sources
        if "Sources" in settings:
            scraper_configs = {
                "torrentio": {"default_opts": settings.get("Torrentio Scraper Parameters", "")},
                "jackett": {
                    "base_url": settings.get("Jackett Base URL", ""),
                    "api_key": settings.get("Jackett API Key", ""),
                    "resolver_timeout": settings.get("Jackett resolver timeout", "10"),
                    "filter": settings.get("Jackett indexer filter", "all"),
                },
                "prowlarr": {
                    "base_url": settings.get("Prowlarr Base URL", ""),
                    "api_key": settings.get("Prowlarr API Key", ""),
                },
                "orionoid": {"token": settings.get("Orionoid API Key", "")},
                "nyaa": {
                    "params": settings.get("Nyaa parameters", ""),
                    "sleep": settings.get("Nyaa sleep time", "5"),
                    "proxy": settings.get("Nyaa proxy", "nyaa.si"),
                },
            }
            for src_name in settings["Sources"]:
                config = scraper_configs.get(src_name.lower(), {})
                self.add_scraper_source(name=src_name, enabled=True, config=config)

        # Migrate tracker rules
        if "Tracker specific Debrid Services" in settings:
            for rule_data in settings["Tracker specific Debrid Services"]:
                if isinstance(rule_data, list) and len(rule_data) >= 2:
                    self.add_tracker_rule(
                        tracker_pattern=rule_data[0],
                        debrid_short=rule_data[1]
                    )

        self.set_setting("setup_complete", True, category="system")
        self.set_setting("migrated_from_json", True, category="system")

        logger.info("Legacy settings migration complete!")
        return True
