"""
Onboarding page for plex_debrid.
Multi-step wizard for first-time setup.
"""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from nicegui import ui, Client

from webapp.theme import COLORS, apply_theme
from webapp.plex_auth import (
    generate_client_id, create_pin, check_pin,
    get_user_info, get_servers, get_libraries, wait_for_auth,
)

logger = logging.getLogger(__name__)

# Keys to persist in DB for resume
_PERSIST_KEYS = [
    "step", "plex_client_id", "plex_token", "plex_user", "plex_servers",
    "plex_selected_server", "plex_libraries", "plex_selected_libs",
    "decypharr_url", "decypharr_username", "scraper_sources",
    "trakt_enabled", "overseerr_enabled", "overseerr_url", "overseerr_key",
]


def _save_wizard_progress(wizard, app_state):
    """Persist wizard state to DB so the user can resume."""
    try:
        data = {k: wizard.get(k) for k in _PERSIST_KEYS}
        app_state.db.set_setting("onboarding_wizard_state", data, "system")
    except Exception as e:
        logger.debug(f"Could not save wizard progress: {e}")


def _load_wizard_progress(app_state):
    """Load saved wizard state from DB."""
    try:
        data = app_state.db.get_setting("onboarding_wizard_state")
        if data and isinstance(data, dict) and data.get("plex_token"):
            return data
    except Exception:
        pass
    return None


async def render(app_state, client: Client):
    apply_theme()
    
    # State for the wizard — try to resume from saved progress
    saved = _load_wizard_progress(app_state)
    wizard = {
        "step": 0,
        "plex_client_id": generate_client_id(),
        "plex_token": None,
        "plex_user": None,
        "plex_servers": [],
        "plex_selected_server": None,
        "plex_libraries": [],
        "plex_selected_libs": [],
        "debrid_service": None,
        "debrid_api_key": "",
        "scraper_sources": [],
        "trakt_enabled": False,
        "overseerr_enabled": False,
        "overseerr_url": "",
        "overseerr_key": "",
        "decypharr_url": "",
        "decypharr_username": "",
        "download_folder": "",
        "media_folder": "",
    }
    if saved:
        wizard.update(saved)
        logger.info(f"Resuming onboarding at step {wizard['step']}")

    # Render generation counter — incremented on every step change.
    # Async callbacks must check this before touching UI elements.
    render_gen = [0]

    steps = [
        "Welcome",
        "Plex Account",
        "Plex Server",
        "Decypharr",
        "Scraper Sources",
        "Extras",
        "Complete",
    ]

    with ui.column().classes("w-full items-center justify-center min-h-screen p-8"):
        # Logo and title
        with ui.column().classes("items-center gap-2 mb-8"):
            ui.label("🎬").classes("text-5xl")
            ui.label("plex_debrid").classes("text-3xl font-bold").style(f"color: {COLORS['primary']}")
            ui.label("Setup Wizard").classes("text-lg").style(f"color: {COLORS['text_muted']}")

        # Progress bar
        progress_container = ui.column().classes("w-full max-w-2xl mb-6")
        with progress_container:
            progress_row = ui.row().classes("w-full items-center justify-between")
            with progress_row:
                for i, step_name in enumerate(steps):
                    with ui.column().classes("items-center gap-1"):
                        step_dot = ui.element("div").classes("w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold")
                        step_dot.style(
                            f"background: {COLORS['primary'] if i <= wizard['step'] else COLORS['surface_light']}; "
                            f"color: {'#000' if i <= wizard['step'] else COLORS['text_muted']}"
                        )
                        step_dot.text = str(i + 1)
                        ui.label(step_name).classes("text-xs").style(
                            f"color: {COLORS['primary'] if i <= wizard['step'] else COLORS['text_muted']}"
                        )

        # Step content container
        step_container = ui.column().classes("w-full max-w-2xl")

        async def render_step():
            render_gen[0] += 1
            gen = render_gen[0]
            step_container.clear()
            with step_container:
                if wizard["step"] == 0:
                    render_welcome(wizard)
                elif wizard["step"] == 1:
                    render_plex_auth(wizard, app_state, render_gen)
                elif wizard["step"] == 2:
                    render_plex_server(wizard, app_state, render_gen)
                elif wizard["step"] == 3:
                    render_debrid(wizard, app_state)
                elif wizard["step"] == 4:
                    render_scrapers(wizard, app_state)
                elif wizard["step"] == 5:
                    render_extras(wizard, app_state)
                elif wizard["step"] == 6:
                    render_complete(wizard, app_state)

        def _update_progress():
            """Refresh the progress dots to reflect current step."""
            try:
                progress_row.clear()
                with progress_row:
                    for i, step_name in enumerate(steps):
                        with ui.column().classes("items-center gap-1"):
                            with ui.element("div").classes(
                                "w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold"
                            ).style(
                                f"background: {COLORS['primary'] if i <= wizard['step'] else COLORS['surface_light']}; "
                                f"color: {'#000' if i <= wizard['step'] else COLORS['text_muted']}"
                            ):
                                ui.label(str(i + 1))
                            ui.label(step_name).classes("text-xs").style(
                                f"color: {COLORS['primary'] if i <= wizard['step'] else COLORS['text_muted']}"
                            )
            except RuntimeError:
                pass  # UI elements may have been deleted on page nav

        async def next_step():
            if wizard["step"] < len(steps) - 1:
                wizard["step"] += 1
                _save_wizard_progress(wizard, app_state)
                _update_progress()
                await render_step()

        async def prev_step():
            if wizard["step"] > 0:
                wizard["step"] -= 1
                _save_wizard_progress(wizard, app_state)
                _update_progress()
                await render_step()

        # Store navigation functions for steps to use
        wizard["next_step"] = next_step
        wizard["prev_step"] = prev_step

        # Initialize progress dots for resume
        _update_progress()

        await render_step()


def render_welcome(wizard):
    with ui.card().classes("onboarding-step w-full"):
        ui.label("Welcome to plex_debrid").classes("text-2xl font-bold").style(f"color: {COLORS['text']}")
        ui.label(
            "This wizard will guide you through setting up plex_debrid. "
            "You'll connect your Plex account, configure a debrid service, "
            "and choose your scraping sources."
        ).classes("text-sm mt-2").style(f"color: {COLORS['text_muted']}")

        ui.separator().classes("my-4")

        with ui.column().classes("gap-3"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("check_circle").style(f"color: {COLORS['success']}")
                ui.label("Plex browser authentication (no manual tokens)").style(f"color: {COLORS['text']}")
            with ui.row().classes("items-center gap-3"):
                ui.icon("check_circle").style(f"color: {COLORS['success']}")
                ui.label("Automatic server and library discovery").style(f"color: {COLORS['text']}")
            with ui.row().classes("items-center gap-3"):
                ui.icon("check_circle").style(f"color: {COLORS['success']}")
                ui.label("Multiple scraping sources with version rules").style(f"color: {COLORS['text']}")
            with ui.row().classes("items-center gap-3"):
                ui.icon("check_circle").style(f"color: {COLORS['success']}")
                ui.label("Decypharr integration support").style(f"color: {COLORS['text']}")

        with ui.row().classes("mt-6 justify-end"):
            ui.button("Get Started", on_click=wizard["next_step"]).props("color=amber push").classes("px-6")


def render_plex_auth(wizard, app_state, render_gen):
    with ui.card().classes("onboarding-step w-full"):
        ui.label("Connect Your Plex Account").classes("text-2xl font-bold").style(f"color: {COLORS['text']}")
        ui.label(
            "Click the button below to open Plex authentication in a new window. "
            "Sign in with your Plex account to continue."
        ).classes("text-sm mt-2").style(f"color: {COLORS['text_muted']}")

        ui.separator().classes("my-4")

        # Show existing auth if resuming
        if wizard.get("plex_token") and wizard.get("plex_user"):
            user_data = wizard["plex_user"]
            with ui.row().classes("items-center gap-3 p-4").style(
                f"background: {COLORS['surface_light']}; border-radius: 0"
            ):
                if user_data and user_data.get("thumb"):
                    ui.image(user_data["thumb"]).classes("w-12 h-12 rounded-full")
                else:
                    ui.icon("account_circle").classes("text-4xl").style(f"color: {COLORS['primary']}")
                with ui.column().classes("gap-0"):
                    ui.label(user_data.get("username", "Unknown User")).classes("font-semibold").style(
                        f"color: {COLORS['text']}")
                    ui.label(user_data.get("email", "")).classes("text-xs").style(
                        f"color: {COLORS['text_muted']}")

        status_label = ui.label("").classes("text-sm").style(f"color: {COLORS['text_muted']}")
        server_spinner = ui.spinner("dots", size="lg", color="amber").classes("ml-2")
        server_spinner.visible = False
        user_info_container = ui.column().classes("gap-2")

        # Capture generation at render time
        my_gen = render_gen[0]

        async def start_plex_auth():
            if render_gen[0] != my_gen:
                return

            import asyncio
            loop = asyncio.get_event_loop()

            # Run create_pin in executor to avoid blocking the event loop
            status_label.text = "Creating Plex PIN..."
            status_label.style(f"color: {COLORS['warning']}")
            pin_id, pin_code, auth_url = await loop.run_in_executor(
                None, create_pin, wizard["plex_client_id"]
            )
            if not auth_url:
                if render_gen[0] != my_gen:
                    return
                status_label.text = "Failed to create Plex PIN. Please try again."
                status_label.style(f"color: {COLORS['error']}")
                return

            # Open auth URL in a centered popup window.
            # Append forwardUrl on the JS side so it uses the correct origin
            # (works behind reverse proxies, non-localhost, etc.).
            ui.run_javascript(f'''
                var callbackUrl = window.location.origin + "/api/plex/callback";
                var fullUrl = "{auth_url}" + "&forwardUrl=" + encodeURIComponent(callbackUrl);
                var popup = window.open(fullUrl, "PlexAuth",
                    "width=800,height=700,scrollbars=yes,resizable=yes,"
                    + "left=" + (screen.width/2 - 400) + ",top=" + (screen.height/2 - 350));
                if (!popup) {{
                    // Popup blocked — open in new tab as fallback
                    window.open(fullUrl, "_blank");
                }}
            ''')
            status_label.text = "Waiting for Plex authentication... (check your popup window)"
            status_label.style(f"color: {COLORS['warning']}")

            # Poll for auth (uses run_in_executor internally)
            token = await wait_for_auth(pin_id, wizard["plex_client_id"], timeout=300)

            # Check if step is still active after await
            if render_gen[0] != my_gen:
                return

            if token:
                wizard["plex_token"] = token
                # Run get_user_info in executor to avoid blocking
                user_data = await loop.run_in_executor(
                    None, get_user_info, token, wizard["plex_client_id"]
                )
                wizard["plex_user"] = user_data

                status_label.text = "Successfully authenticated!"
                status_label.style(f"color: {COLORS['success']}")

                user_info_container.clear()
                with user_info_container:
                    with ui.row().classes("items-center gap-3 p-4").style(
                        f"background: {COLORS['surface_light']}; border-radius: 0"
                    ):
                        if user_data and user_data.get("thumb"):
                            ui.image(user_data["thumb"]).classes("w-12 h-12 rounded-full")
                        else:
                            ui.icon("account_circle").classes("text-4xl").style(f"color: {COLORS['primary']}")
                        with ui.column().classes("gap-0"):
                            ui.label(user_data.get("username", "Unknown User")).classes("font-semibold").style(
                                f"color: {COLORS['text']}")
                            ui.label(user_data.get("email", "")).classes("text-xs").style(
                                f"color: {COLORS['text_muted']}")

                # Fetch servers
                status_label.text = "Fetching your Plex servers..."
                status_label.style(f"color: {COLORS['warning']}")
                server_spinner.visible = True

                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor() as pool:
                    wizard["plex_servers"] = await loop.run_in_executor(
                        pool, lambda: get_servers(token, wizard["plex_client_id"])
                    )

                server_spinner.visible = False
                _save_wizard_progress(wizard, app_state)

                # Check again after await
                if render_gen[0] != my_gen:
                    return

                if wizard["plex_servers"]:
                    status_label.text = f"Found {len(wizard['plex_servers'])} server(s). Click Next to continue."
                    status_label.style(f"color: {COLORS['success']}")
                else:
                    status_label.text = "Authenticated but no servers found. Click Next to continue anyway."
                    status_label.style(f"color: {COLORS['warning']}")

                # Enable the Next button
                if next_btn:
                    next_btn.props(remove="disable")
                    next_btn.update()
            else:
                status_label.text = "Authentication timed out. Please try again."
                status_label.style(f"color: {COLORS['error']}")

        with ui.row().classes("gap-3"):
            ui.button("Sign in with Plex", on_click=start_plex_auth).props("color=amber push icon=login").classes("px-6")

        with ui.row().classes("mt-6 justify-between w-full"):
            ui.button("Back", on_click=wizard["prev_step"]).props("flat color=grey")
            next_btn = ui.button("Next", on_click=wizard["next_step"]).props(
                f"color=amber push {'disable' if not wizard.get('plex_token') else ''}"
            ).classes("px-6")


def render_plex_server(wizard, app_state, render_gen):
    my_gen = render_gen[0]

    with ui.card().classes("onboarding-step w-full"):
        ui.label("Select Your Plex Server").classes("text-2xl font-bold").style(f"color: {COLORS['text']}")
        ui.label("Choose the server and libraries to monitor.").classes("text-sm mt-2").style(
            f"color: {COLORS['text_muted']}")

        ui.separator().classes("my-4")

        libs_container = ui.column().classes("gap-2 w-full")

        if wizard["plex_servers"]:
            owned_servers = [s for s in wizard["plex_servers"] if s.get("owned")]
            if not owned_servers:
                owned_servers = wizard["plex_servers"]

            server_names = [s["name"] for s in owned_servers]

            def toggle_lib(checked, lib):
                if checked and lib not in wizard["plex_selected_libs"]:
                    wizard["plex_selected_libs"].append(lib)
                elif not checked and lib in wizard["plex_selected_libs"]:
                    wizard["plex_selected_libs"].remove(lib)

            def _show_libraries(libraries, container):
                """Populate the libs_container with library checkboxes."""
                container.clear()
                with container:
                    if libraries:
                        ui.label("Select libraries to use:").classes("text-sm font-semibold").style(
                            f"color: {COLORS['text']}")
                        for lib in libraries:
                            lib_type = lib.get("type", "unknown")
                            ui.checkbox(
                                f"{lib['title']} ({lib_type})",
                                value=True,
                                on_change=lambda e, l=lib: toggle_lib(e.value, l),
                            ).style(f"color: {COLORS['text']}")
                    else:
                        ui.label("Could not load libraries. Try selecting a different server or check your network.").style(
                            f"color: {COLORS['error']}")

            async def _fetch_and_show_libs(server, container):
                """Fetch libraries in background and update UI."""
                token = server.get("access_token", wizard["plex_token"])
                conns = server.get("connections", [])

                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor() as pool:
                    libraries = await loop.run_in_executor(
                        pool, lambda: get_libraries(server["uri"], token, wizard["plex_client_id"], conns)
                    )

                # Bail out if user navigated away
                if render_gen[0] != my_gen:
                    return

                wizard["plex_libraries"] = libraries
                wizard["plex_selected_libs"] = libraries[:]
                _show_libraries(libraries, container)

            async def on_server_select(e):
                if render_gen[0] != my_gen:
                    return
                selected_name = e.value
                for server in owned_servers:
                    if server["name"] == selected_name:
                        wizard["plex_selected_server"] = server
                        libs_container.clear()
                        with libs_container:
                            ui.label("Loading libraries...").style(f"color: {COLORS['text_muted']}")
                            ui.spinner(size="sm")
                        await _fetch_and_show_libs(server, libs_container)
                        break

            ui.select(
                server_names,
                label="Select Server",
                value=server_names[0] if server_names else None,
                on_change=on_server_select,
            ).classes("w-full").props("outlined dark color=amber")

            # If libraries already fetched (resume), show them immediately
            if wizard.get("plex_selected_libs"):
                with libs_container:
                    ui.label("Select libraries to use:").classes("text-sm font-semibold").style(
                        f"color: {COLORS['text']}")
                    for lib in wizard["plex_selected_libs"]:
                        lib_type = lib.get("type", "unknown")
                        ui.checkbox(
                            f"{lib['title']} ({lib_type})",
                            value=True,
                            on_change=lambda e, l=lib: toggle_lib(e.value, l),
                        ).style(f"color: {COLORS['text']}")
            elif owned_servers and not wizard.get("plex_selected_server"):
                # Auto-select first server — show loading, fetch in background task
                wizard["plex_selected_server"] = owned_servers[0]
                with libs_container:
                    ui.label("Loading libraries...").style(f"color: {COLORS['text_muted']}")
                    ui.spinner(size="sm")
                # Launch as a fire-and-forget task so render_step() returns immediately
                asyncio.ensure_future(_fetch_and_show_libs(owned_servers[0], libs_container))
        else:
            ui.label("No Plex servers found. Please go back and authenticate.").style(f"color: {COLORS['error']}")

        with ui.row().classes("mt-6 justify-between w-full"):
            ui.button("Back", on_click=wizard["prev_step"]).props("flat color=grey")
            ui.button("Next", on_click=wizard["next_step"]).props("color=amber push").classes("px-6")


def render_debrid(wizard, app_state):
    """Decypharr configuration step (replaces legacy debrid service selection)."""
    with ui.card().classes("onboarding-step w-full"):
        ui.label("Configure Decypharr").classes("text-2xl font-bold").style(f"color: {COLORS['text']}")
        ui.label(
            "Decypharr handles all debrid operations (Real-Debrid, AllDebrid, etc.). "
            "Enter your Decypharr instance URL and Arr host (username)."
        ).classes("text-sm mt-2").style(f"color: {COLORS['text_muted']}")

        ui.separator().classes("my-4")

        dec_url = ui.input(
            "Decypharr URL",
            value=wizard.get("decypharr_url", ""),
            placeholder="http://localhost:8282",
            on_change=lambda e: wizard.update({"decypharr_url": e.value}),
        ).classes("w-full").props("outlined dark color=amber")

        dec_username = ui.input(
            "Username (Arr Host)",
            value=wizard.get("decypharr_username", ""),
            placeholder="http://10.0.0.5:8989",
            on_change=lambda e: wizard.update({"decypharr_username": e.value}),
        ).classes("w-full mt-2").props("outlined dark color=amber")
        ui.label(
            "This is the hostname/IP that Decypharr sees as the Arr client. "
            "Typically your machine's IP + Arr port."
        ).classes("text-xs").style(f"color: {COLORS['text_muted']}")

        status_label = ui.label("").classes("text-sm mt-2")

        async def test_connection():
            url = dec_url.value.strip()
            if not url:
                ui.notify("Enter a Decypharr URL first", type="warning")
                return
            from webapp.decypharr import DecypharrClient
            client = DecypharrClient(url, username=dec_username.value.strip(), password="")
            ok, info = client.test_connection()
            if ok:
                status_label.text = f"✓ Connected — Decypharr v{info}"
                status_label.style(f"color: {COLORS['success']}")
                ui.notify("Decypharr connected!", type="positive")
            else:
                status_label.text = f"✗ Failed: {info}"
                status_label.style(f"color: {COLORS['error']}")
                ui.notify(f"Connection failed: {info}", type="negative")

        ui.button("Test Connection", on_click=test_connection, icon="wifi_tethering").props(
            "color=blue push").classes("mt-2")

        ui.separator().classes("my-4")

        ui.label("Folder Paths").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.label(
            "Download base folder: Decypharr will place symlinks here. "
            "Each release version automatically gets a sub-folder named after its category "
            "(e.g. /mnt/symlinks/default)."
        ).classes("text-xs mt-1").style(f"color: {COLORS['text_muted']}")
        ui.label(
            "Media base folder: the app will move completed content here. "
            "Each release version automatically gets a sub-folder named after its category "
            "(e.g. /mnt/media/default)."
        ).classes("text-xs").style(f"color: {COLORS['text_muted']}")

        ui.input(
            "Download Base Folder",
            value=wizard.get("download_folder", ""),
            placeholder="/mnt/symlinks",
            on_change=lambda e: wizard.update({"download_folder": e.value}),
        ).classes("w-full mt-2").props("outlined dark color=amber")

        ui.input(
            "Media Base Folder",
            value=wizard.get("media_folder", ""),
            placeholder="/mnt/media",
            on_change=lambda e: wizard.update({"media_folder": e.value}),
        ).classes("w-full mt-2").props("outlined dark color=amber")

        with ui.row().classes("mt-6 justify-between w-full"):
            ui.button("Back", on_click=wizard["prev_step"]).props("flat color=grey")
            ui.button("Next", on_click=wizard["next_step"]).props("color=amber push").classes("px-6")


def render_scrapers(wizard, app_state):
    with ui.card().classes("onboarding-step w-full"):
        ui.label("Select Scraping Sources").classes("text-2xl font-bold").style(f"color: {COLORS['text']}")
        ui.label("Choose which sources to use for finding content.").classes("text-sm mt-2").style(
            f"color: {COLORS['text_muted']}")

        ui.separator().classes("my-4")

        sources = [
            {"name": "torrentio", "desc": "Stremio-based addon (recommended)", "default": True},
            {"name": "jackett", "desc": "Jackett indexer proxy", "default": False},
            {"name": "prowlarr", "desc": "Prowlarr indexer manager", "default": False},
            {"name": "orionoid", "desc": "Orionoid aggregator", "default": False},
            {"name": "nyaa", "desc": "Anime-focused torrent site", "default": False},
            {"name": "1337x", "desc": "General torrent site", "default": False},
        ]

        selected = wizard.get("scraper_sources", ["torrentio"])

        for src in sources:
            def on_change(e, name=src["name"]):
                if e.value and name not in selected:
                    selected.append(name)
                elif not e.value and name in selected:
                    selected.remove(name)
                wizard["scraper_sources"] = selected

            ui.checkbox(
                f'{src["name"]} — {src["desc"]}',
                value=src["name"] in selected,
                on_change=on_change,
            ).style(f"color: {COLORS['text']}")

        # Torrentio config
        ui.separator().classes("my-4")
        ui.label("Torrentio Configuration").classes("text-sm font-semibold").style(f"color: {COLORS['text']}")
        ui.label(
            'Visit torrentio.strem.fun/configure to get your manifest URL. '
            'Do not select a debrid service there.'
        ).classes("text-xs").style(f"color: {COLORS['text_muted']}")
        torrentio_input = ui.input(
            "Torrentio Manifest URL (optional)",
            value=wizard.get("torrentio_url", ""),
            on_change=lambda e: wizard.update({"torrentio_url": e.value}),
        ).classes("w-full").props("outlined dark color=amber")

        with ui.row().classes("mt-6 justify-between w-full"):
            ui.button("Back", on_click=wizard["prev_step"]).props("flat color=grey")
            ui.button("Next", on_click=wizard["next_step"]).props("color=amber push").classes("px-6")


def render_extras(wizard, app_state):
    with ui.card().classes("onboarding-step w-full"):
        ui.label("Additional Services").classes("text-2xl font-bold").style(f"color: {COLORS['text']}")
        ui.label("Configure optional integrations.").classes("text-sm mt-2").style(f"color: {COLORS['text_muted']}")

        ui.separator().classes("my-4")

        # Trakt
        with ui.expansion("Trakt Integration", icon="list").classes("w-full").style(f"color: {COLORS['text']}"):
            ui.label("Connect your Trakt account to monitor watchlists and collections.").classes("text-sm mb-2").style(
                f"color: {COLORS['text_muted']}")
            trakt_toggle = ui.switch(
                "Enable Trakt",
                value=wizard.get("trakt_enabled", False),
                on_change=lambda e: wizard.update({"trakt_enabled": e.value}),
            ).style(f"color: {COLORS['text']}")

        # Overseerr
        with ui.expansion("Overseerr Integration", icon="request_page").classes("w-full").style(
            f"color: {COLORS['text']}"
        ):
            ui.label("Connect to Overseerr for request management.").classes("text-sm mb-2").style(
                f"color: {COLORS['text_muted']}")
            overseerr_toggle = ui.switch(
                "Enable Overseerr",
                value=wizard.get("overseerr_enabled", False),
                on_change=lambda e: wizard.update({"overseerr_enabled": e.value}),
            ).style(f"color: {COLORS['text']}")
            overseerr_url = ui.input(
                "Overseerr URL",
                value=wizard.get("overseerr_url", ""),
                on_change=lambda e: wizard.update({"overseerr_url": e.value}),
            ).classes("w-full").props("outlined dark color=amber")
            overseerr_key = ui.input(
                "Overseerr API Key",
                value=wizard.get("overseerr_key", ""),
                password=True,
                password_toggle_button=True,
                on_change=lambda e: wizard.update({"overseerr_key": e.value}),
            ).classes("w-full").props("outlined dark color=amber")

        # Discord webhook
        with ui.expansion("Discord Notifications", icon="notifications").classes("w-full").style(
            f"color: {COLORS['text']}"
        ):
            ui.label("Get notified on Discord when content is downloaded.").classes("text-sm mb-2").style(
                f"color: {COLORS['text_muted']}")
            discord_input = ui.input("Discord Webhook URL", value="").classes("w-full").props(
                "outlined dark color=amber")

        # Import from legacy settings.json
        if app_state.has_legacy_settings:
            ui.separator().classes("my-4")
            with ui.card().classes("w-full p-4").style(
                f"background: {COLORS['surface_light']}; border: 1px solid {COLORS['primary']}"
            ):
                with ui.row().classes("items-center gap-3"):
                    ui.icon("file_upload").classes("text-2xl").style(f"color: {COLORS['primary']}")
                    with ui.column().classes("gap-0 flex-1"):
                        ui.label("Import from settings.json").classes("text-base font-semibold").style(
                            f"color: {COLORS['text']}")
                        ui.label(
                            "A legacy settings.json file was found. You can import your existing "
                            "configuration (Plex users, debrid services, scraper sources, versions, etc.) "
                            "instead of configuring everything from scratch."
                        ).classes("text-xs").style(f"color: {COLORS['text_muted']}")

                import_status = ui.label("").classes("text-sm mt-2")

                async def do_import_legacy():
                    try:
                        ok = app_state.db.migrate_from_json()
                        if ok:
                            import_status.text = "✓ Settings imported successfully! Click Complete Setup to finish."
                            import_status.style(f"color: {COLORS['success']}")
                            ui.notify("Legacy settings imported", type="positive")
                        else:
                            import_status.text = "✗ Import failed — file may be missing or invalid."
                            import_status.style(f"color: {COLORS['error']}")
                            ui.notify("Import failed", type="negative")
                    except Exception as e:
                        import_status.text = f"✗ Import error: {e}"
                        import_status.style(f"color: {COLORS['error']}")
                        ui.notify(f"Import error: {e}", type="negative")

                ui.button(
                    "Import Settings", on_click=do_import_legacy, icon="file_upload"
                ).props("color=blue push").classes("mt-2")

        with ui.row().classes("mt-6 justify-between w-full"):
            ui.button("Back", on_click=wizard["prev_step"]).props("flat color=grey")
            ui.button("Complete Setup", on_click=lambda: asyncio.ensure_future(save_and_finish(wizard, app_state))).props(
                "color=amber push").classes("px-6")


async def save_and_finish(wizard, app_state):
    """Save all wizard settings to the database."""
    db = app_state.db

    # Save Plex user
    if wizard.get("plex_token") and wizard.get("plex_user"):
        server = wizard.get("plex_selected_server", {})
        libs = wizard.get("plex_selected_libs", [])
        db.add_plex_user(
            username=wizard["plex_user"].get("username", "Unknown"),
            token=wizard["plex_token"],
            client_id=wizard["plex_client_id"],
            server_name=server.get("name", ""),
            server_url=server.get("uri", ""),
            server_machine_id=server.get("machine_id", ""),
            library_sections=json.dumps([{"key": l["key"], "title": l["title"], "type": l["type"]} for l in libs]),
            is_primary=True,
        )

    # Save content services
    active_content = ["Plex"]
    if wizard.get("trakt_enabled"):
        active_content.append("Trakt")
    if wizard.get("overseerr_enabled"):
        active_content.append("Overseerr")
    db.set_setting("Content Services", active_content, "content")

    # Save library services
    db.set_setting("Library collection service", ["Plex Library"], "library")
    db.set_setting("Library update services", ["Plex Libraries"], "library")
    db.set_setting("Library ignore services", ["Local Ignore List"], "library")

    # Save Plex server settings
    if wizard.get("plex_selected_server"):
        server = wizard["plex_selected_server"]
        db.set_setting("Plex server address", server.get("uri", ""), "library")
        libs = wizard.get("plex_selected_libs", [])
        db.set_setting("Plex library refresh", [[l["key"], l["title"]] for l in libs], "library")

    # Save Decypharr settings
    if wizard.get("decypharr_url"):
        db.set_setting("Decypharr Base URL", wizard["decypharr_url"].strip(), "debrid")
        db.set_setting("Decypharr Username", wizard.get("decypharr_username", "").strip(), "debrid")
        from webapp.decypharr import generate_api_key
        db.set_setting("Decypharr API Key", generate_api_key(), "debrid")

    # Save global download base folder
    global_download_base = wizard.get("download_folder", "").strip().rstrip("/")
    if global_download_base:
        db.set_setting("Global Download Folder", global_download_base, "debrid")

    # Save global media base folder
    global_media_base = wizard.get("media_folder", "").strip().rstrip("/")
    if global_media_base:
        db.set_setting("Global Media Folder", global_media_base, "debrid")

    # Save scraper sources
    for src in wizard.get("scraper_sources", ["torrentio"]):
        cfg = {}
        if src == "torrentio" and wizard.get("torrentio_url"):
            cfg["default_opts"] = wizard["torrentio_url"]
        db.add_scraper_source(name=src, enabled=True, config=cfg)
    db.set_setting("Sources", wizard.get("scraper_sources", ["torrentio"]), "scraper")

    # Save Overseerr
    if wizard.get("overseerr_enabled"):
        db.set_setting("Overseerr Base URL", wizard.get("overseerr_url", ""), "content")
        db.set_setting("Overseerr API Key", wizard.get("overseerr_key", ""), "content")

    # Save default release version (1080p SDR)
    default_triggers = [["retries", "<=", "48"], ["media type", "all", ""]]
    default_rules = [
        ["resolution", "requirement", "<=", "1080"],
        ["resolution", "preference", "highest", ""],
        ["title", "requirement", "exclude", "([^A-Z0-9]|HD|HQ)(CAM|T(ELE)?(S(YNC)?|C(INE)?)|ADS|HINDI)([^A-Z0-9]|RIP|$)"],
        ["title", "requirement", "exclude", "(3D)"],
        ["title", "requirement", "exclude", "(DV|DOLBY.VISION|HDR)"],
        ["title", "preference", "include", "(EXTENDED|REMASTERED|DIRECTORS|THEATRICAL|UNRATED|UNCUT)"],
        ["size", "preference", "highest", ""],
        ["seeders", "preference", "highest", ""],
        ["size", "requirement", ">=", "0.1"],
    ]
    # Auto-derive download_folder and media_folder from global base + category
    default_category = "default"
    derived_download_folder = (
        f"{global_download_base}/{default_category}" if global_download_base else ""
    )
    derived_media_folder = (
        f"{global_media_base}/{default_category}" if global_media_base else ""
    )
    db.add_release_version(
        name="1080p SDR",
        enabled=True,
        triggers=default_triggers,
        language="en",
        rules=default_rules,
        sort_order=0,
        category=default_category,
        download_folder=derived_download_folder,
        media_folder=derived_media_folder,
    )

    # Mark setup complete
    db.set_setting("setup_complete", True, "system")
    db.set_setting("Show Menu on Startup", "false", "ui")

    # Clear saved wizard progress
    db.delete_setting("onboarding_wizard_state")

    app_state.needs_onboarding = False
    
    # Navigate to step 6 (complete)
    await wizard["next_step"]()


def render_complete(wizard, app_state):
    with ui.card().classes("onboarding-step w-full"):
        with ui.column().classes("items-center gap-4 py-8"):
            ui.icon("check_circle").classes("text-6xl").style(f"color: {COLORS['success']}")
            ui.label("Setup Complete!").classes("text-2xl font-bold").style(f"color: {COLORS['text']}")
            ui.label(
                "plex_debrid is ready to use. You can start the automation, "
                "browse your content, or configure additional settings."
            ).classes("text-center text-sm").style(f"color: {COLORS['text_muted']}")

            with ui.row().classes("gap-4 mt-6"):
                ui.button("Go to Dashboard", on_click=lambda: ui.navigate.to("/dashboard")).props(
                    "color=amber push icon=dashboard").classes("px-6")
                ui.button("Open Settings", on_click=lambda: ui.navigate.to("/settings")).props(
                    "flat color=grey icon=settings").classes("px-6")
