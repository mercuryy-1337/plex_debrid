"""
Shared layout components for plex_debrid web UI.
"""

from nicegui import ui


def create_layout(app_state, active_page="dashboard"):
    """Create the shared page layout with sidebar navigation (legacy, used by onboarding)."""
    from webapp.theme import apply_theme, COLORS
    from ui import ui_settings
    apply_theme()

    nav_items = [
        {"icon": "dashboard", "label": "Dashboard", "page": "dashboard"},
        {"icon": "movie", "label": "Content", "page": "content"},
        {"icon": "sync", "label": "Activity", "page": "activity"},
        {"icon": "settings", "label": "Settings", "page": "settings"},
        {"icon": "article", "label": "Logs", "page": "logs"},
    ]

    app_version = ui_settings.version[0]

    with ui.header().classes("items-center justify-between px-6 py-2"):
        with ui.row().classes("items-center gap-2"):
            ui.label("🎬").classes("text-xl")
            ui.label("plex_debrid").classes("text-lg font-bold").style(f"color: {COLORS['primary']}")
            ui.label(f"v{app_version}").classes("text-xs").style(f"color: {COLORS['text_muted']}")

    with ui.left_drawer(value=True, fixed=True).classes("flex flex-col").style(
        f"width: 220px; background-color: {COLORS['surface']}; padding: 0"
    ) as drawer:
        with ui.column().classes("w-full gap-0 pt-2"):
            for i, item in enumerate(nav_items):
                is_active = item["page"] == active_page.lower()
                with ui.element("a").props(f'href="/{item["page"]}"').classes(
                    "no-underline w-full"
                ).style("text-decoration: none; display: block"):
                    with ui.row().classes(
                        "items-center gap-3 w-full cursor-pointer"
                    ).style(
                        f"padding: 14px 20px;"
                        f"background: {'rgba(229,160,13,0.12)' if is_active else 'transparent'};"
                        f"border-left: 3px solid {COLORS['primary'] if is_active else 'transparent'};"
                        f"transition: background 0.15s ease"
                    ):
                        ui.icon(item["icon"]).classes("text-xl").style(
                            f"color: {COLORS['primary'] if is_active else COLORS['text_muted']}"
                        )
                        ui.label(item["label"]).style(
                            f"color: {COLORS['primary'] if is_active else COLORS['text']};"
                            f"font-size: 0.95rem"
                        ).classes("font-medium")
                if i < len(nav_items) - 1:
                    ui.element("div").style(
                        "height: 1px; background: rgba(255,255,255,0.06); margin: 0"
                    )

        ui.element("div").classes("flex-1")

        stats = app_state.get_stats()
        with ui.column().classes("w-full gap-1").style(
            "border-top: 1px solid rgba(255,255,255,0.08); padding: 14px 20px 10px"
        ):
            ui.label("QUICK STATS").classes("text-xs font-semibold tracking-wider mb-1").style(
                f"color: {COLORS['text_muted']}"
            )
            _sidebar_stat("Movies", stats.get("movies", 0))
            _sidebar_stat("Shows", stats.get("shows", 0))
            _sidebar_stat("Anime", stats.get("anime", 0))

        with ui.row().classes("w-full justify-center py-2").style(
            "border-top: 1px solid rgba(255,255,255,0.06)"
        ):
            ui.label(f"v{app_version}").classes("text-xs").style(f"color: {COLORS['text_muted']}")

    return drawer


def create_spa_shell(app_state, initial_page, on_navigate, client=None):
    """Build header + sidebar once and return the content container.

    Sidebar items trigger *on_navigate(page_name)* instead of doing a full
    page reload, so only the content area is swapped.

    Args:
        on_navigate: async callable(page_name: str) invoked when a sidebar
            item is clicked.
        client: the NiceGUI ``Client`` instance for this connection.

    Returns:
        content_area: the ``ui.column`` to render page content into.
    """
    from webapp.theme import apply_theme, COLORS
    from ui import ui_settings
    apply_theme()

    nav_items = [
        {"icon": "dashboard", "label": "Dashboard", "page": "dashboard"},
        {"icon": "movie", "label": "Content", "page": "content"},
        {"icon": "sync", "label": "Activity", "page": "activity"},
        {"icon": "settings", "label": "Settings", "page": "settings"},
        {"icon": "article", "label": "Logs", "page": "logs"},
    ]

    app_version = ui_settings.version[0]
    active = {"page": initial_page}

    # ── Header ──────────────────────────────────────────────────────
    with ui.header().classes("items-center justify-between px-6 py-2"):
        with ui.row().classes("items-center gap-2"):
            ui.label("🎬").classes("text-xl")
            ui.label("plex_debrid").classes("text-lg font-bold").style(f"color: {COLORS['primary']}")
            ui.label(f"v{app_version}").classes("text-xs").style(f"color: {COLORS['text_muted']}")

        # ── Search bar ──────────────────────────────────────────────
        from webapp.cinemeta_feed import search as _feed_search
        from webapp.pages.scraper_page import _open_scrape_dialog
        import html as _html

        # Wrapper div with relative positioning so the dropdown sits underneath
        with ui.element("div").style(
            "flex: 1; max-width: 420px; min-width: 200px"
        ):
            search_input = ui.input(placeholder="Search movies & shows...").props(
                'outlined dense dark color=amber clearable autocomplete=off'
            ).classes("w-full")

            # Quasar QMenu popup — portals to <body> so no overflow clipping
            # 'fit' matches parent width; max-width clamp + overflow-hidden prevents blowout
            suggestions_menu = ui.menu().props(
                'no-parent-event no-focus no-refocus fit anchor="bottom left" self="top left"'
            ).classes("no-shadow").style(
                f"background: {COLORS['surface']}; border: 1px solid {COLORS['surface_light']}; "
                "border-radius: 0; max-height: 360px; overflow-y: auto; overflow-x: hidden; "
                "max-width: min(420px, 100vw - 32px); width: 420px; "
                "box-shadow: 0 8px 24px rgba(0,0,0,0.5);"
            )

        _search_state = {"suppress": False}

        def _show_suggestions(query_text):
            """Build suggestion items inside the Quasar menu."""
            suggestions_menu.clear()
            if not query_text or len(query_text) < 2:
                suggestions_menu.close()
                return

            hits = _feed_search(query_text, limit=5)
            if not hits:
                suggestions_menu.close()
                return

            with suggestions_menu:
                for hit in hits:
                    h_name = hit.get("name", "")
                    h_year = hit.get("releaseInfo", "")
                    h_type = hit.get("type", "movie")
                    h_poster = hit.get("poster", "")
                    h_rating = hit.get("imdbRating", "")
                    type_icon = "movie" if h_type == "movie" else "tv"
                    type_label = "Movie" if h_type == "movie" else "Series"

                    async def _pick(
                        _,
                        _name=h_name, _type=h_type, _poster=h_poster,
                        _year=h_year, _rating=h_rating, _imdb=hit.get("id", ""),
                    ):
                        _search_state["suppress"] = True
                        search_input.value = _name
                        suggestions_menu.close()
                        # Show loading overlay while scrape dialog prepares
                        with ui.dialog().props("persistent seamless") as loading_dlg:
                            with ui.card().style(
                                f"background: {COLORS['surface']}; padding: 12px 24px;"
                                " border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.5);"
                            ).classes("items-center"):
                                with ui.row().classes("items-center gap-3 no-wrap"):
                                    ui.spinner("dots", size="md", color="amber")
                                    ui.label(f"Loading {_name}...").style(
                                        f"color: {COLORS['text']}; font-size: 0.85rem;"
                                    )
                        loading_dlg.open()
                        try:
                            await _open_scrape_dialog(
                                app_state, client,
                                imdb_id=_imdb, title=_name, media_type=_type,
                                poster_url=_poster, year=_year, rating=_rating,
                            )
                        finally:
                            loading_dlg.close()

                    with ui.row().classes(
                        "items-center gap-3 w-full cursor-pointer px-3 py-2"
                    ).style(
                        f"border-bottom: 1px solid {COLORS['surface_light']}; "
                        "transition: background 0.15s"
                    ).on("click", _pick):
                        # Tiny poster thumbnail
                        if h_poster:
                            ui.image(h_poster).style(
                                "width: 32px; height: 46px; object-fit: cover; border-radius: 0; flex-shrink: 0"
                            )
                        else:
                            ui.icon(type_icon).style(
                                f"color: {COLORS['text_muted']}; font-size: 1.4rem; width: 32px; text-align: center"
                            )
                        with ui.column().classes("gap-0 flex-1 min-w-0").style(
                            "overflow: hidden"
                        ):
                            ui.label(h_name).classes("text-sm font-medium").style(
                                f"color: {COLORS['text']}; "
                                "word-wrap: break-word; overflow-wrap: break-word; white-space: normal")
                            with ui.row().classes("items-center gap-2"):
                                ui.label(type_label).classes("text-xs").style(
                                    f"color: {COLORS['primary']}")
                                if h_year:
                                    ui.label(h_year).classes("text-xs").style(
                                        f"color: {COLORS['text_muted']}")
                                if h_rating:
                                    ui.html(
                                        f'<span style="color: #F59E0B; font-size: 0.7rem">★ {h_rating}</span>'
                                    )

                # "Search {query}" action row
                async def _search_query(_, q=query_text):
                    _search_state["suppress"] = True
                    search_input.value = q
                    suggestions_menu.close()
                    active["page"] = "results"
                    _build_nav()
                    await on_navigate(f"results:{q}")

                with ui.row().classes(
                    "items-center gap-3 w-full cursor-pointer px-3 py-2"
                ).style(
                    f"border-top: 1px solid {COLORS['surface_light']}; "
                    "transition: background 0.15s"
                ).on("click", _search_query):
                    ui.icon("search").style(
                        f"color: {COLORS['primary']}; font-size: 1.2rem; width: 32px; text-align: center"
                    )
                    safe_q = _html.escape(query_text)
                    ui.html(
                        f'<span style="color: {COLORS["text_muted"]}; font-size: 0.85rem">'
                        f'Search <b style="color: {COLORS["primary"]}">{safe_q}</b></span>'
                    )

                # Attribution footer
                ui.html(
                    '<div style="padding: 4px 12px; text-align: right; opacity: 0.45; '
                    'font-size: 0.65rem; border-top: 1px solid rgba(255,255,255,0.06)">'
                    'Search by <a href="https://github.com/Stremio/local-search" '
                    'target="_blank" style="color: #E5A00D; text-decoration: none">'
                    'Stremio local-search</a></div>'
                )

            suggestions_menu.open()

        def _on_search_input(e):
            """Fires on every keystroke via on_value_change (value is synced)."""
            if _search_state["suppress"]:
                _search_state["suppress"] = False
                return
            val = (e.value or "").strip() if e.value else ""
            _show_suggestions(val)

        async def _on_search_enter(e):
            val = (search_input.value or "").strip()
            if not val:
                return
            suggestions_menu.close()
            active["page"] = "results"
            _build_nav()
            await on_navigate(f"results:{val}")

        # on_value_change fires AFTER NiceGUI syncs the model — e.value is reliable
        search_input.on_value_change(_on_search_input)
        search_input.on("keydown.enter", _on_search_enter)

    # ── Sidebar ─────────────────────────────────────────────────────
    with ui.left_drawer(value=True, fixed=True).classes("flex flex-col").style(
        f"width: 220px; background-color: {COLORS['surface']}; padding: 0"
    ):
        nav_container = ui.column().classes("w-full gap-0 pt-2")

        # Dict to hold the activity badge reference for live updates
        _activity_badge_ref = {"label": None}

        def _build_nav():
            nav_container.clear()
            with nav_container:
                for i, item in enumerate(nav_items):
                    is_active = item["page"] == active["page"]
                    page_name = item["page"]

                    async def _go(_, p=page_name):
                        if p == active["page"]:
                            return
                        active["page"] = p
                        _build_nav()
                        await on_navigate(p)

                    with ui.row().classes(
                        "items-center gap-3 w-full cursor-pointer"
                    ).style(
                        f"padding: 14px 20px;"
                        f"background: {'rgba(229,160,13,0.12)' if is_active else 'transparent'};"
                        f"border-left: 3px solid {COLORS['primary'] if is_active else 'transparent'};"
                        f"transition: background 0.15s ease"
                    ).on("click", _go):
                        ui.icon(item["icon"]).classes("text-xl").style(
                            f"color: {COLORS['primary'] if is_active else COLORS['text_muted']}"
                        )
                        ui.label(item["label"]).style(
                            f"color: {COLORS['primary'] if is_active else COLORS['text']};"
                            f"font-size: 0.95rem"
                        ).classes("font-medium")
                        # Activity badge — live count of items in queue
                        if item["page"] == "activity":
                            count = len(app_state.get_activities())
                            badge = ui.label(str(count)).style(
                                f"background: {COLORS['primary']}; color: #000;"
                                " font-size: 0.65rem; font-weight: 700;"
                                " min-width: 20px; height: 20px; border-radius: 10px;"
                                " display: flex; align-items: center; justify-content: center;"
                                " padding: 0 5px; margin-left: auto;"
                            )
                            badge.set_visibility(count > 0)
                            _activity_badge_ref["label"] = badge
                    if i < len(nav_items) - 1:
                        ui.element("div").style(
                            "height: 1px; background: rgba(255,255,255,0.06); margin: 0"
                        )

        _build_nav()

        # Timer to refresh activity badge count every 2 seconds
        def _refresh_activity_badge():
            badge = _activity_badge_ref.get("label")
            if badge:
                count = len(app_state.get_activities())
                badge.text = str(count)
                badge.set_visibility(count > 0)

        _badge_timer = ui.timer(2.0, _refresh_activity_badge)
        if client is not None:
            client.on_disconnect(lambda: setattr(_badge_timer, 'active', False))

        # Spacer
        ui.element("div").classes("flex-1")

        # Quick stats
        stats = app_state.get_stats()
        with ui.column().classes("w-full gap-1").style(
            "border-top: 1px solid rgba(255,255,255,0.08); padding: 14px 20px 10px"
        ):
            ui.label("QUICK STATS").classes("text-xs font-semibold tracking-wider mb-1").style(
                f"color: {COLORS['text_muted']}"
            )
            _sidebar_stat("Movies", stats.get("movies", 0))
            _sidebar_stat("Shows", stats.get("shows", 0))
            _sidebar_stat("Anime", stats.get("anime", 0))

        with ui.row().classes("w-full justify-center py-2").style(
            "border-top: 1px solid rgba(255,255,255,0.06)"
        ):
            ui.label(f"v{app_version}").classes("text-xs").style(f"color: {COLORS['text_muted']}")

    # ── Content area ────────────────────────────────────────────────
    content_area = ui.column().classes("w-full")
    return content_area


def _sidebar_stat(label, value):
    from webapp.theme import COLORS
    with ui.row().classes("items-center justify-between w-full"):
        ui.label(label).classes("text-sm").style(f"color: {COLORS['text_muted']}")
        ui.label(str(value)).classes("text-sm font-semibold").style(f"color: {COLORS['text']}")


def stat_card(icon, label, value, color="#E5A00D"):
    """Create a statistics card."""
    with ui.card().classes("stat-card w-full"):
        with ui.row().classes("items-center gap-3"):
            ui.icon(icon).classes("text-3xl").style(f"color: {color}")
            with ui.column().classes("gap-0"):
                ui.label(str(value)).classes("text-2xl font-bold").style("color: #E5E7EB")
                ui.label(label).classes("text-sm").style("color: #9CA3AF")


def content_card(item):
    """Create a content item card for the dashboard/content grid."""
    from webapp.theme import COLORS

    media_type = item.get("media_type", "movie")
    is_anime = item.get("is_anime", False)
    badge_class = "badge-movie"
    type_label = "Movie"
    if media_type in ("show", "anime_show", "season"):
        if is_anime:
            badge_class = "badge-anime"
            type_label = "Anime"
        else:
            badge_class = "badge-show"
            type_label = "Show" if media_type in ("show", "anime_show") else "Season"

    status = item.get("status", "unknown")
    status_colors = {
        "collected": COLORS["success"],
        "downloading": COLORS["warning"],
        "watchlisted": COLORS["info"],
        "ignored": COLORS["error"],
    }
    s_color = status_colors.get(status, COLORS["text_muted"])

    card = ui.card().classes("content-card").style(
        "width: 185px; overflow: hidden; padding: 0; border-radius: 8px;"
    )
    imdb_id = item.get("imdb_id")
    if media_type in ("show", "anime_show") and imdb_id:
        card.style("cursor: pointer;")
        card.on("click", lambda _, iid=imdb_id: ui.navigate.to(f"/series/{iid}"))
    elif media_type in ("movie", "anime_movie") and imdb_id:
        card.style("cursor: pointer;")
        card.on("click", lambda _, iid=imdb_id: ui.navigate.to(f"/movies/{iid}"))

    with card:
        # Poster
        poster = item.get("poster_url")
        if poster:
            ui.image(poster).classes("w-full").style(
                "height: 260px; object-fit: cover; display: block;"
            )
        else:
            with ui.element("div").classes("w-full flex items-center justify-center").style(
                f"height: 260px; background: {COLORS['surface_light']};"
            ):
                ui.icon("movie").classes("text-5xl").style(f"color: {COLORS['text_muted']}")

        # Info section — fixed layout, left-aligned, compact
        with ui.column().style(
            "padding: 10px 12px; gap: 4px;"
        ):
            # Row 1: type badge + year
            with ui.row().style("align-items: center; gap: 6px;"):
                ui.html(f'<span class="{badge_class}">{type_label}</span>')
                if item.get("year"):
                    ui.label(str(item["year"])).style(
                        f"color: {COLORS['text_muted']}; font-size: 0.75rem; line-height: 1;"
                    )

            # Row 2: title
            ui.label(item.get("title", "Unknown")).style(
                f"color: {COLORS['text']}; font-size: 0.85rem; font-weight: 600;"
                " line-height: 1.2; white-space: nowrap; overflow: hidden;"
                " text-overflow: ellipsis; max-width: 165px;"
            )

            # Row 3: status
            ui.label(status.capitalize()).style(
                f"color: {s_color}; font-size: 0.7rem; font-weight: 500;"
            )


def page_header(title, subtitle=None):
    """Create a page header."""
    from webapp.theme import COLORS
    with ui.column().classes("gap-1 mb-6"):
        ui.label(title).classes("text-2xl font-bold").style(f"color: {COLORS['text']}")
        if subtitle:
            ui.label(subtitle).classes("text-sm").style(f"color: {COLORS['text_muted']}")


def empty_state(icon, message, action_label=None, action_callback=None):
    """Display an empty state placeholder."""
    from webapp.theme import COLORS
    with ui.column().classes("items-center justify-center py-8 gap-3 w-full"):
        ui.icon(icon).classes("text-4xl").style(f"color: {COLORS['text_muted']}; opacity: 0.5")
        ui.label(message).classes("text-sm").style(f"color: {COLORS['text_muted']}")
        if action_label and action_callback:
            ui.button(action_label, on_click=action_callback).props("color=amber")
