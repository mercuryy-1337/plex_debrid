"""
Shared layout components for pd_reloaded web UI.
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
            ui.label("pd_reloaded").classes("text-lg font-bold").style(f"color: {COLORS['primary']}")
            ui.label(f"v{app_version}").classes("text-xs").style(f"color: {COLORS['text_muted']}")

        with ui.row().classes("items-center gap-4"):
            status_label = ui.label()
            if app_state.automation_running:
                status_label.text = "● Automation Running"
                status_label.classes("status-running text-sm font-medium")
            else:
                status_label.text = "○ Automation Stopped"
                status_label.classes("status-stopped text-sm font-medium")

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


def create_spa_shell(app_state, initial_page, on_navigate):
    """Build header + sidebar once and return the content container.

    Sidebar items trigger *on_navigate(page_name)* instead of doing a full
    page reload, so only the content area is swapped.

    Args:
        on_navigate: async callable(page_name: str) invoked when a sidebar
            item is clicked.

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
            ui.label("pd_reloaded").classes("text-lg font-bold").style(f"color: {COLORS['primary']}")
            ui.label(f"v{app_version}").classes("text-xs").style(f"color: {COLORS['text_muted']}")

        # ── Search bar ──────────────────────────────────────────────
        from webapp.cinemeta_feed import search as _feed_search
        import html as _html

        # Wrapper div with relative positioning so the dropdown sits underneath
        with ui.element("div").style(
            "flex: 1; max-width: 420px; min-width: 200px"
        ):
            search_input = ui.input(placeholder="Search movies & shows...").props(
                'outlined dense dark color=amber clearable autocomplete=off'
            ).classes("w-full")

            # Quasar QMenu popup — portals to <body> so no overflow clipping
            suggestions_menu = ui.menu().props(
                'no-parent-event no-focus no-refocus fit anchor="bottom left" self="top left"'
            ).classes("no-shadow").style(
                f"background: {COLORS['surface']}; border: 1px solid {COLORS['surface_light']}; "
                "border-radius: 0; max-height: 360px; overflow-y: auto; "
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

                    async def _pick(_, name=h_name):
                        _search_state["suppress"] = True
                        search_input.value = name
                        suggestions_menu.close()
                        active["page"] = "results"
                        _build_nav()
                        await on_navigate(f"results:{name}")

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
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            ui.label(h_name).classes("text-sm font-medium truncate").style(
                                f"color: {COLORS['text']}")
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

        with ui.row().classes("items-center gap-4"):
            status_label = ui.label()
            if app_state.automation_running:
                status_label.text = "● Automation Running"
                status_label.classes("status-running text-sm font-medium")
            else:
                status_label.text = "○ Automation Stopped"
                status_label.classes("status-stopped text-sm font-medium")

    # ── Sidebar ─────────────────────────────────────────────────────
    with ui.left_drawer(value=True, fixed=True).classes("flex flex-col").style(
        f"width: 220px; background-color: {COLORS['surface']}; padding: 0"
    ):
        nav_container = ui.column().classes("w-full gap-0 pt-2")

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
                    if i < len(nav_items) - 1:
                        ui.element("div").style(
                            "height: 1px; background: rgba(255,255,255,0.06); margin: 0"
                        )

        _build_nav()

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
    if media_type in ("show", "anime_show"):
        if is_anime:
            badge_class = "badge-anime"
            type_label = "Anime"
        else:
            badge_class = "badge-show"
            type_label = "Show"

    with ui.card().classes("content-card").style("width: 200px"):
        # Poster placeholder
        poster = item.get("poster_url")
        if poster:
            ui.image(poster).classes("w-full").style("height: 280px; object-fit: cover")
        else:
            with ui.element("div").classes("w-full flex items-center justify-center").style(
                f"height: 280px; background: {COLORS['surface_light']}"
            ):
                ui.icon("movie").classes("text-5xl").style(f"color: {COLORS['text_muted']}")

        with ui.column().classes("p-3 gap-1"):
            with ui.row().classes("items-center gap-2"):
                ui.html(f'<span class="{badge_class}">{type_label}</span>')
                if item.get("year"):
                    ui.label(str(item["year"])).classes("text-xs").style(f"color: {COLORS['text_muted']}")

            ui.label(item.get("title", "Unknown")).classes("text-sm font-semibold truncate").style(
                f"color: {COLORS['text']}; max-width: 180px"
            )

            # Status badge
            status = item.get("status", "unknown")
            status_colors = {
                "collected": COLORS["success"],
                "downloading": COLORS["warning"],
                "watchlisted": COLORS["info"],
                "ignored": COLORS["error"],
            }
            ui.label(status.capitalize()).classes("text-xs font-medium").style(
                f"color: {status_colors.get(status, COLORS['text_muted'])}"
            )

            # IDs
            ids = []
            if item.get("imdb_id"):
                ids.append(f"IMDB: {item['imdb_id']}")
            if item.get("tmdb_id"):
                ids.append(f"TMDB: {item['tmdb_id']}")
            if ids:
                ui.label(" | ".join(ids)).classes("text-xs").style(f"color: {COLORS['text_muted']}")


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
