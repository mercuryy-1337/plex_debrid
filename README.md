# plex_debrid

> **Branch: `revamp`** — This is a work-in-progress rewrite. Expect breaking changes.

A ground-up modernisation of [plex_debrid](https://github.com/itsToggle/plex_debrid). Plex torrent streaming through Debrid Services, powered by [Decypharr](https://github.com/sirrobot01/decypharr), with a modern web UI.

plex_debrid replaces the legacy CLI with a full NiceGUI web frontend, SQLite database, Decypharr-only debrid pipeline, and guided onboarding — while preserving the core automation that made plex_debrid great.

---

## What's New (revamp branch)

### Modern Web UI
- Full **NiceGUI** web interface on port **8008** (dark theme, Plex gold accent)
- **Dashboard** with download stats, recent activity, and system status
- **Settings page** with categorized tabs — no more editing JSON files
- **Content browser** with poster art, filtering by type/status, and metadata from TMDB/IMDB
- **Download logs** with full history, resolution, size, source, and status tracking
- **Guided onboarding wizard** — walks through Plex auth, Decypharr, scrapers, and content services on first run

### Decypharr Integration (replaces all legacy debrid services)
- **All debrid operations go through [Decypharr](https://github.com/sirrobot01/decypharr)** — a qBittorrent WebAPI-compatible debrid gateway
- Supports **Real-Debrid, AllDebrid, Premiumize, Debrid-Link** etc. via Decypharr
- Per-version auth: each version profile carries its own **category + API key** (no global token)
- Torrent state polling (`/api/v2/torrents/info`) with automatic cleanup of completed downloads
- Auth model mirrors Sonarr/Radarr's qBittorrent download client (Basic auth on all qBit API endpoints)

### Manual Scrape & Download
- **Search & download directly from the web UI** — search by title, browse results, pick a version, and send to Decypharr
- Anime detection via AniDB title matching (animetitles XML)
- Automatic metadata resolution (IMDB/TMDB IDs, posters, genres, year)
- Always requires **version selection** so the correct category + API key is used

### SQLite Database
- All settings, content, users, versions, download logs, and scraper sources stored in **SQLite** via SQLAlchemy
- Replaces the old `settings.json` — legacy settings are auto-migrated on first run
- Proper relational schema: `Setting`, `PlexUser`, `TraktUser`, `ScraperSource`, `ReleaseVersion`, `ContentItem`, `DownloadLog`

### Plex Browser Auth
- OAuth-based Plex sign-in via browser popup — no more copy-pasting tokens
- Multi-user support with per-user library selection and server switching

### Version Profiles
- GUI-based version editor with **rules**, **triggers**, **language**, **category**, and **folder path** fields
- Each version maps to a Decypharr category for isolated download handling
- **Download Folder** — where Decypharr places symlinks (e.g. `/mnt/symlinks/version1/`)
- **Media Folder** — where the app moves completed content (e.g. `/mnt/media/shows/`)
- Flow: Decypharr downloads → symlinks in `download_folder/show.name/` → on completion, app moves to `media_folder/show.name/`

### Other Improvements
- Docker-ready with `docker-compose.yml` (port 8008, config volume)
- Legacy CLI mode still available via `--legacy` flag
- Debug logging toggle from the UI
- Automation engine with background scheduling
- **Concurrent downloads** — up to 4 movies + 1 series simultaneously (5 total), semaphore-controlled
- **Post-download processing** — on completion: move symlinks from download folder to media folder → mark collected → trigger Plex partial scan
- **Duplicate prevention** — torrent info hashes stored in download logs; already-downloaded hashes are skipped
- **Plex Location-aware refresh** — only scans the library section whose root path contains the moved content
- **Live activity badge** — sidebar shows count of active downloads, auto-refreshes every 2 seconds
- **Watchlist auto-remove** — respects "none" setting; DB setting properly loaded into legacy modules

---

## What's Been Removed

| Removed | Replacement |
|---------|-------------|
| Direct Real-Debrid, Premiumize, AllDebrid, Debrid-Link, PUT.io clients | **Decypharr** handles all debrid services |
| `settings.json` configuration | **SQLite database** with web UI settings |
| Terminal/CLI-only interface | **NiceGUI web UI** (legacy CLI still available) |
| rclone mount requirement | **Decypharr symlink-based** file management |
| Discord webhook integration | *(planned for future)* |
| Emby/Jellyfin as primary library servers | **Plex-focused** (Jellyfin support planned) |
| Plex Discover Watch Status ignore | **Local database** ignore tracking |
| Multiple debrid service failover | **Single Decypharr instance** with debrid provider configured in Decypharr |

---

## Quick Start

### Docker (recommended)

```yaml
version: '3.8'
services:
  plex_debrid:
    container_name: plex_debrid
    build: .
    ports:
      - "8008:8008"
    volumes:
      - ./config:/app/config:rw
      - /mnt:/mnt
    restart: unless-stopped
```

```bash
docker compose up -d
```

Open **http://localhost:8008** — the onboarding wizard will guide you through setup.

### Manual

```bash
git clone -b revamp https://github.com/mercuryy-1337/plex_debrid.git
cd plex_debrid
pip install -r requirements.txt
python main.py
```

Open **http://localhost:8008**.

---

## Prerequisites

1. **[Decypharr](https://github.com/sirrobot01/decypharr)** — running and configured with your debrid provider
2. **[Plex Media Server](https://plex.tv/)** — with libraries pointed at Decypharr's symlink output
3. **Python 3.11+** (if running without Docker)

---

## Configuration

All configuration is done through the web UI at **http://localhost:8008**:

- **Plex** — Browser-based OAuth sign-in, multi-user, server & library selection
- **Decypharr** — Base URL + username (Arr host). Each version profile provides its own API key
- **Content Services** — Plex Watchlists, Trakt lists, Overseerr requests
- **Scraper Sources** — Torrentio, Jackett, Prowlarr, Orionoid, NYAA, 1337x
- **Versions** — Define quality profiles (resolution, HDR, size rules, triggers) with per-version Decypharr categories

---

## Architecture

```
Plex Watchlist / Trakt / Overseerr
        │
        ▼
   plex_debrid (automation engine)
        │
        ├── Scrape: Torrentio / Jackett / Prowlarr / ...
        │
        ├── Match: Version rules & triggers
        │
        ▼
   Decypharr (qBit WebAPI)
        │
        ├── Debrid: Real-Debrid / AllDebrid / Premiumize / ...
        │
        ▼
   Download Folder (symlinks)  ──[on completion]──▶  Media Folder
        │                                                │
        └── /version1/show.name/file.mkv                └── /shows/show.name/file.mkv
                                                              │
                                                              ▼
                                                         Plex Libraries
```

---

## Status

**Work in progress** — this is the `revamp` branch. Core functionality works:

- [x] Web UI with dashboard, settings, content browser, logs
- [x] Onboarding wizard
- [x] Plex browser auth + multi-user
- [x] Decypharr integration (add, poll, cleanup)
- [x] Manual scrape & download with version selection
- [x] Per-version download & media folder paths
- [x] Automated content monitoring & download
- [x] SQLite database with migration from legacy settings
- [x] Concurrent download processing (4 movies + 1 series simultaneously)
- [x] Automatic symlink relocation (download folder → media folder on completion)
- [x] Plex partial library scan after download (Location-aware section matching)
- [x] Duplicate download prevention (info_hash tracking)
- [x] Live activity tracking with sidebar badge
- [x] Watchlist auto-remove respects "none" setting
- [ ] Discord notifications
- [ ] Jellyfin library support
- [ ] Upgrade detection (re-download higher quality releases)
- [ ] Import/export settings

---

## Credits

Based on [plex_debrid](https://github.com/itsToggle/plex_debrid) by [itsToggle](https://github.com/itsToggle). This rewrite wouldn't exist without the original project and community.

Search powered by a Python port of [Stremio local-search](https://github.com/Stremio/local-search) (MIT licence) — TF-IDF + Levenshtein fuzzy matching + prefix boosting for typo-tolerant title search.
