"""
Decypharr qBittorrent-compatible client for pd_reloaded.

Decypharr exposes a qBittorrent WebAPI-compatible interface. This module
mimics how Sonarr/Radarr interact with a qBittorrent download client:

  - Add torrents via /api/v2/torrents/add (multipart form w/ Basic auth)
  - Poll /api/v2/torrents/info for torrent state (unauthenticated)
  - Remove completed torrents via /api/v2/torrents/delete
  - Version check via /version

Auth model:
  - username = user-configured Arr host (e.g. "http://sonarr:8989")
  - password = global Decypharr API Key (stored in settings, auto-generated)
  - All qBit /api/v2/ endpoints use HTTP Basic auth (base64 of username:password)
  - get_torrents is unauthenticated (read-only listing)
  - Decypharr auto-creates an Arr interface when it receives a new
    Basic auth username:password on /api/v2/torrents/add
  - Bearer tokens are NOT supported on qBit routes (only Decypharr's Web UI)
"""

import logging
import secrets
import time
from enum import Enum
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def generate_api_key() -> str:
    """Generate a random API key for Decypharr authentication."""
    return secrets.token_hex(16)


# ─── Torrent States (matching qBittorrent) ─────────────────────────────
class TorrentState(str, Enum):
    """qBittorrent torrent states as returned by /api/v2/torrents/info."""
    DOWNLOADING = "downloading"
    STALLED_DL = "stalledDL"
    PAUSED_DL = "pausedDL"
    QUEUED_DL = "queuedDL"
    CHECKING_DL = "checkingDL"
    FORCED_DL = "forcedDL"
    META_DL = "metaDL"
    ALLOCATING = "allocating"
    UPLOADING = "uploading"
    STALLED_UP = "stalledUP"
    PAUSED_UP = "pausedUP"
    QUEUED_UP = "queuedUP"
    CHECKING_UP = "checkingUP"
    FORCED_UP = "forcedUP"
    MOVING = "moving"
    MISSING_FILES = "missingFiles"
    ERROR = "error"
    UNKNOWN = "unknown"

    @classmethod
    def is_completed(cls, state: str) -> bool:
        """Whether a torrent is in a completed/seeding state."""
        return state in (
            cls.UPLOADING, cls.STALLED_UP, cls.PAUSED_UP,
            cls.QUEUED_UP, cls.CHECKING_UP, cls.FORCED_UP,
        )

    @classmethod
    def is_downloading(cls, state: str) -> bool:
        return state in (
            cls.DOWNLOADING, cls.STALLED_DL, cls.FORCED_DL,
            cls.META_DL, cls.ALLOCATING, cls.CHECKING_DL,
            cls.QUEUED_DL,
        )

    @classmethod
    def is_failed(cls, state: str) -> bool:
        return state in (cls.ERROR, cls.MISSING_FILES)


# ─── Client ─────────────────────────────────────────────────────────────
class DecypharrClient:
    """qBittorrent-compatible client for Decypharr.

    Mimics Sonarr/Radarr's qBittorrent download-client integration.

    Args:
        base_url:  Decypharr base URL (e.g. http://localhost:8282)
        username:  Arr host identifier (user-entered, e.g. "http://10.0.0.5:8989")
        password:  Global Decypharr API key (from settings)
    """

    def __init__(self, base_url: str, username: str = "", password: str = ""):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = 30
        self.add_timeout = 120  # longer timeout for add operations

    # ── Auth ────────────────────────────────────────────────────────

    def _basic_auth(self) -> Optional[tuple]:
        """Return ``(username, password)`` for HTTP Basic auth.

        Decypharr's ``decodeAuthHeader`` base64-decodes and splits on ":".
        When a new username:password pair is presented on /api/v2/torrents/add,
        Decypharr auto-creates an Arr interface for it.

        Used on all write endpoints (add, delete, setCategory).
        get_torrents is unauthenticated.
        """
        if self.username or self.password:
            return (self.username, self.password)
        return None

    # ── Version / Connection ────────────────────────────────────────

    def get_version(self) -> str:
        """Get Decypharr version string via /version."""
        resp = requests.get(f"{self.base_url}/version", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        version = data.get("version", "unknown")
        if isinstance(version, str) and version.startswith("v"):
            version = version[1:]
        return version

    def test_connection(self) -> tuple:
        """Test connectivity. Returns (success: bool, version_or_error: str)."""
        try:
            version = self.get_version()
            return True, version
        except requests.ConnectionError:
            return False, "Connection refused"
        except Exception as e:
            return False, str(e)

    # ── Torrents (qBit WebAPI v2) ───────────────────────────────────

    def get_torrents(self, category: str = None,
                     torrent_hash: str = None) -> list:
        """GET /api/v2/torrents/info — list torrents.

        Mimics Sonarr's ``GetTorrents(settings)`` which filters by category.
        """
        params = {}
        if category:
            params["category"] = category
        if torrent_hash:
            params["hashes"] = torrent_hash
        try:
            resp = requests.get(
                f"{self.base_url}/api/v2/torrents/info",
                params=params,
                timeout=None,
            )
            if resp.ok:
                return resp.json() if resp.text else []
            logger.warning("get_torrents failed: %s", resp.status_code)
            return []
        except Exception as e:
            logger.error("get_torrents error: %s", e)
            return []

    def get_torrent_properties(self, torrent_hash: str) -> dict:
        """GET /api/v2/torrents/properties — detailed torrent info."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v2/torrents/properties",
                params={"hash": torrent_hash},
                timeout=self.timeout,
                auth=self._basic_auth(),
            )
            return resp.json() if resp.ok else {}
        except Exception as e:
            logger.error("get_torrent_properties error: %s", e)
            return {}

    def add_torrent_url(self, magnet_or_url: str,
                        category: str = None) -> bool:
        """POST /api/v2/torrents/add — add torrent from magnet/URL.

        Mimics Sonarr: ``AddTorrentFromUrl(torrentUrl, seedConfig, settings)``
        which posts ``urls`` + ``category`` as form data.
        """
        data = {"urls": magnet_or_url}
        if category:
            data["category"] = category
        try:
            resp = requests.post(
                f"{self.base_url}/api/v2/torrents/add",
                auth=self._basic_auth(),
                data=data,
                timeout=self.add_timeout,
            )
            body = (resp.text or "").strip()
            logger.info(
                "add_torrent_url response: status=%s body=%r category=%s",
                resp.status_code, body[:200], category,
            )
            # Decypharr may return 200 with "Ok.", empty body, or JSON.
            # Only treat as failure if status >= 400 or body is exactly "Fails."
            if resp.status_code >= 400:
                logger.warning(
                    "add_torrent_url HTTP error: %s - %s", resp.status_code, body[:200]
                )
                return False
            if body == "Fails.":
                logger.warning("add_torrent_url: Decypharr returned 'Fails.'")
                return False
            logger.info("Torrent accepted by Decypharr (category=%s)", category)
            return True
        except requests.Timeout:
            logger.error(
                "add_torrent_url timed out after %ds — torrent may still be processing",
                self.add_timeout,
            )
            return False
        except requests.ConnectionError as e:
            logger.error("add_torrent_url connection error: %s", e)
            return False
        except Exception as e:
            logger.error("add_torrent_url error: %s", e)
            return False

    def remove_torrent(self, torrent_hash: str,
                       delete_files: bool = False) -> bool:
        """POST /api/v2/torrents/delete — remove a torrent.

        Mimics Sonarr: ``RemoveTorrent(hash, removeData, settings)``
        """
        data = {
            "hashes": torrent_hash,
            "deleteFiles": "true" if delete_files else "false",
        }
        try:
            resp = requests.post(
                f"{self.base_url}/api/v2/torrents/delete",
                data=data,
                timeout=self.timeout,
                auth=self._basic_auth(),
            )
            if resp.ok:
                logger.info("Torrent removed: %s", torrent_hash[:16])
                return True
            logger.warning("remove_torrent failed: %s", resp.status_code)
            return False
        except Exception as e:
            logger.error("remove_torrent error: %s", e)
            return False

    def set_category(self, torrent_hash: str, category: str) -> bool:
        """POST /api/v2/torrents/setCategory."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/v2/torrents/setCategory",
                data={"hashes": torrent_hash, "category": category},
                timeout=self.timeout,
                auth=self._basic_auth(),
            )
            return resp.ok
        except Exception as e:
            logger.error("set_category error: %s", e)
            return False

    # ── High-level helpers ──────────────────────────────────────────

    def download_magnet(self, magnet: str, category: str = "default") -> bool:
        """Add a magnet link."""
        return self.add_torrent_url(magnet, category=category)

    def download_hash(self, info_hash: str, category: str = "default") -> bool:
        """Build minimal magnet from info-hash and add."""
        magnet = f"magnet:?xt=urn:btih:{info_hash}"
        return self.download_magnet(magnet, category=category)

    def wait_for_completion(self, torrent_hash: str,
                            timeout: Optional[int] = None,
                            poll_interval: int = 5,
                            remove_on_complete: bool = True,
                            progress_callback=None) -> dict:
        """Poll /api/v2/torrents/info until torrent completes or fails.

        Args:
            progress_callback: optional callable(state: str, progress: float)
                called on each poll iteration with the current state and
                progress (0.0–1.0).

        Returns: {"success": bool, "state": str, "torrent": dict|None}
        Removes the torrent from Decypharr history on completion.
        """
        start = time.time()
        last_state = "unknown"

        while timeout is None or (time.time() - start < timeout):
            torrents = self.get_torrents(torrent_hash=torrent_hash)
            if not torrents:
                time.sleep(poll_interval)
                continue

            torrent = torrents[0] if isinstance(torrents, list) else torrents
            state = torrent.get("state", "unknown")
            progress = torrent.get("progress", 0.0)
            last_state = state

            if progress_callback:
                try:
                    progress_callback(state, progress)
                except Exception:
                    pass

            if TorrentState.is_completed(state):
                logger.info("Torrent completed: %s (%s)",
                            torrent_hash[:16], state)
                if remove_on_complete:
                    self.remove_torrent(torrent_hash, delete_files=False)
                return {"success": True, "state": state, "torrent": torrent}

            if TorrentState.is_failed(state):
                logger.warning("Torrent failed: %s (%s)",
                               torrent_hash[:16], state)
                self.remove_torrent(torrent_hash, delete_files=True)
                return {"success": False, "state": state, "torrent": torrent}

            time.sleep(poll_interval)

        logger.warning("Torrent timed out after %ss: %s (last state: %s)",
                str(timeout), torrent_hash[:16], last_state)
        return {"success": False, "state": f"timeout ({last_state})",
                "torrent": None}

    def get_completed_torrents(self, category: str = None) -> list:
        """Get all torrents in a completed/seeding state."""
        torrents = self.get_torrents(category=category)
        return [
            t for t in torrents
            if TorrentState.is_completed(t.get("state", ""))
        ]

    def cleanup_completed(self, category: str = None) -> int:
        """Remove all completed torrents from history. Returns count removed."""
        completed = self.get_completed_torrents(category=category)
        removed = 0
        for t in completed:
            h = t.get("hash", "")
            if h and self.remove_torrent(h, delete_files=False):
                removed += 1
        if removed:
            logger.info("Cleaned up %d completed torrent(s) (category=%s)",
                        removed, category or "all")
        return removed
