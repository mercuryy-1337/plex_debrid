"""
Settings page for plex_debrid.
Full settings management via web UI.
"""

import asyncio
import json
import logging
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from nicegui import ui, Client

from webapp.components import page_header
from webapp.theme import COLORS
from webapp.plex_auth import (
    generate_client_id, create_pin, wait_for_auth,
    get_user_info, get_servers, get_libraries,
)

logger = logging.getLogger(__name__)


async def render(app_state, client: Client):

    with ui.column().classes("p-6 gap-6 w-full"):
        page_header("Settings", "Configure all aspects of plex_debrid")

        # ─── Settings Tabs ──────────────────────────────────
        with ui.tabs().classes("w-full").props("dense active-color=amber indicator-color=amber") as tabs:
            plex_tab = ui.tab("Plex", icon="tv")
            content_tab = ui.tab("Content", icon="movie")
            debrid_tab = ui.tab("Decypharr", icon="cloud_download")
            scraper_tab = ui.tab("Scrapers", icon="search")
            versions_tab = ui.tab("Versions", icon="tune")
            advanced_tab = ui.tab("Advanced", icon="settings")

        with ui.tab_panels(tabs, value=plex_tab).classes("w-full"):
            # ─── Plex Settings ─────────────────────────────
            with ui.tab_panel(plex_tab):
                await _render_plex_settings(app_state)

            # ─── Content Services ──────────────────────────
            with ui.tab_panel(content_tab):
                await _render_content_settings(app_state)

            # ─── Debrid Settings ───────────────────────────
            with ui.tab_panel(debrid_tab):
                await _render_debrid_settings(app_state)

            # ─── Scraper Settings ──────────────────────────
            with ui.tab_panel(scraper_tab):
                await _render_scraper_settings(app_state)

            # ─── Version Rules ─────────────────────────────
            with ui.tab_panel(versions_tab):
                await _render_version_settings(app_state)

            # ─── Advanced Settings ─────────────────────────
            with ui.tab_panel(advanced_tab):
                await _render_advanced_settings(app_state)


async def _render_plex_settings(app_state):
    """Render Plex account and server settings."""
    db = app_state.db

    with ui.card().classes("w-full p-4"):
        ui.label("Plex Account").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        plex_users = db.get_plex_users()

        if plex_users:
            for user in plex_users:
                with ui.card().classes("w-full p-4").style(
                    f"background: {COLORS['surface_light']}; border-radius: 0"
                ):
                    # ── User header row ──
                    with ui.row().classes("items-center gap-4 w-full"):
                        ui.icon("account_circle").classes("text-3xl").style(f"color: {COLORS['primary']}")
                        with ui.column().classes("flex-1 gap-0"):
                            ui.label(user["username"]).classes("font-semibold").style(f"color: {COLORS['text']}")
                        with ui.column().classes("gap-1"):
                            if user.get("is_primary"):
                                ui.badge("Primary").props("color=amber")

                            async def remove_user(uid=user["id"]):
                                db.delete_plex_user(uid)
                                ui.notify("User removed", type="warning")
                                ui.navigate.to("/settings")

                            ui.button("Remove", on_click=remove_user, icon="delete").props(
                                "flat color=red dense size=sm")

                    ui.separator().classes("my-3")

                    # ── Server selector & libraries ──
                    server_container = ui.column().classes("gap-3 w-full")
                    libs_container = ui.column().classes("gap-2 w-full mt-2")

                    # State for this user's server/library selection
                    user_state = {
                        "servers": [],
                        "selected_server": None,
                        "libraries": user.get("library_sections", []),
                        "selected_libs": list(user.get("library_sections", [])),
                    }

                    def _show_libraries(libraries, container, state):
                        """Populate library checkboxes."""
                        container.clear()
                        with container:
                            if libraries:
                                ui.label("Libraries:").classes("text-sm font-semibold").style(
                                    f"color: {COLORS['text']}")

                                def toggle_lib(checked, lib, st=state):
                                    if checked and lib not in st["selected_libs"]:
                                        st["selected_libs"].append(lib)
                                    elif not checked and lib in st["selected_libs"]:
                                        st["selected_libs"].remove(lib)

                                for lib in libraries:
                                    lib_type = lib.get("type", "unknown")
                                    # Check if this lib was previously selected
                                    selected = any(
                                        l.get("key") == lib.get("key") and l.get("title") == lib.get("title")
                                        for l in state["selected_libs"]
                                    )
                                    ui.checkbox(
                                        f"{lib['title']} ({lib_type})",
                                        value=selected,
                                        on_change=lambda e, l=lib: toggle_lib(e.value, l),
                                    ).style(f"color: {COLORS['text']}")
                            else:
                                ui.label("No libraries found on this server.").style(
                                    f"color: {COLORS['text_muted']}")

                    async def _fetch_libs_for_server(server, container, state, u=user):
                        """Fetch libraries for a server and show checkboxes."""
                        container.clear()
                        with container:
                            ui.label("Loading libraries...").style(f"color: {COLORS['text_muted']}")
                            ui.spinner(size="sm")

                        token = server.get("access_token", u["token"])
                        conns = server.get("connections", [])

                        loop = asyncio.get_event_loop()
                        with ThreadPoolExecutor() as pool:
                            libraries = await loop.run_in_executor(
                                pool, lambda: get_libraries(server["uri"], token, u["client_id"], conns)
                            )

                        state["libraries"] = libraries
                        # Reset selection to all libs on new server
                        state["selected_libs"] = libraries[:]
                        _show_libraries(libraries, container, state)

                    async def _load_servers(u=user, s_container=server_container,
                                           l_container=libs_container, state=user_state):
                        """Fetch available servers and build the selector."""
                        s_container.clear()
                        with s_container:
                            ui.label("Fetching servers...").style(f"color: {COLORS['text_muted']}")
                            ui.spinner(size="sm")

                        loop = asyncio.get_event_loop()
                        with ThreadPoolExecutor() as pool:
                            servers = await loop.run_in_executor(
                                pool, lambda: get_servers(u["token"], u["client_id"])
                            )

                        owned = [s for s in servers if s.get("owned")]
                        if not owned:
                            owned = servers
                        state["servers"] = owned

                        s_container.clear()
                        with s_container:
                            if not owned:
                                ui.label("No servers found.").style(f"color: {COLORS['error']}")
                                return

                            server_map = {s["name"]: s for s in owned}
                            # Pre-select current server if it matches
                            current = u.get("server_name", "")
                            default_val = current if current in server_map else owned[0]["name"]

                            async def on_server_change(e, sm=server_map, lc=l_container, st=state):
                                selected = sm.get(e.value)
                                if selected:
                                    st["selected_server"] = selected
                                    await _fetch_libs_for_server(selected, lc, st)

                            ui.select(
                                list(server_map.keys()),
                                label="Server",
                                value=default_val,
                                on_change=on_server_change,
                            ).classes("w-full").props("outlined dark color=amber")

                            state["selected_server"] = server_map[default_val]

                        # Fetch libs for the default/current server
                        await _fetch_libs_for_server(state["selected_server"], l_container, state)

                    # ── Current info & change server button ──
                    with server_container:
                        with ui.row().classes("items-center gap-2"):
                            ui.label(f"Server: {user.get('server_name', 'N/A')}").classes("text-sm").style(
                                f"color: {COLORS['text_muted']}")
                            ui.button("Change Server", on_click=_load_servers, icon="swap_horiz").props(
                                "flat dense color=amber size=sm")

                    # Show currently saved libraries
                    _show_libraries(user_state["libraries"], libs_container, user_state)

                    # ── Save button ──
                    async def save_user_server(uid=user["id"], state=user_state):
                        server = state.get("selected_server")
                        updates = {}
                        if server:
                            updates["server_name"] = server.get("name", "")
                            updates["server_url"] = server.get("uri", "")
                            updates["server_machine_id"] = server.get("machine_id", "")
                        updates["library_sections"] = [
                            {"key": l["key"], "title": l["title"], "type": l["type"]}
                            for l in state["selected_libs"]
                        ]
                        db.update_plex_user(uid, **updates)
                        # Also update the main server address setting if primary
                        puser = db.get_plex_users()
                        for pu in puser:
                            if pu["id"] == uid and pu.get("is_primary") and server:
                                db.set_setting("Plex server address", server.get("uri", ""), "library")
                        ui.notify("Server & libraries saved", type="positive")

                    ui.button("Save", on_click=save_user_server, icon="save").props(
                        "color=amber push dense").classes("mt-2")

        # Add new Plex user via browser auth
        async def add_plex_user():
            client_id = generate_client_id()
            pin_id, pin_code, auth_url = create_pin(client_id)
            if not auth_url:
                ui.notify("Failed to create Plex auth PIN", type="negative")
                return

            ui.run_javascript(f'window.open("{auth_url}", "_blank")')
            ui.notify("Please sign in with Plex in the opened window...", type="info")

            token = await wait_for_auth(pin_id, client_id)
            if token:
                user_data = get_user_info(token, client_id)
                servers = get_servers(token, client_id)
                owned = [s for s in servers if s.get("owned")]
                server = owned[0] if owned else (servers[0] if servers else {})

                libs = []
                if server:
                    libs = get_libraries(server.get("uri", ""), token, client_id)

                db.add_plex_user(
                    username=user_data.get("username", "Unknown") if user_data else "Unknown",
                    token=token,
                    client_id=client_id,
                    server_name=server.get("name", ""),
                    server_url=server.get("uri", ""),
                    server_machine_id=server.get("machine_id", ""),
                    library_sections=json.dumps([{"key": l["key"], "title": l["title"], "type": l["type"]} for l in libs]),
                    is_primary=len(plex_users) == 0,
                )
                ui.notify(f"Plex user added: {user_data.get('username', 'Unknown')}", type="positive")
                ui.navigate.to("/settings")
            else:
                ui.notify("Plex authentication timed out", type="negative")

        ui.button("Add Plex Account", on_click=add_plex_user, icon="person_add").props("color=amber push").classes("mt-4")

    # Plex server settings
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Plex Server Settings").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        server_url = db.get_setting("Plex server address", "")
        partial_scan = db.get_setting("Plex library partial scan", "true")
        scan_delay = db.get_setting("Plex library refresh delay", "10")
        auto_remove = db.get_setting("Plex auto remove", "movie")

        def _discover_server_url_options():
            users = db.get_plex_users()
            primary = next((u for u in users if u.get("is_primary")), users[0] if users else None)
            if not primary:
                return [server_url] if server_url else []

            token = primary.get("token", "")
            client_id = primary.get("client_id", "")
            if not token or not client_id:
                urls = [server_url] if server_url else []
                if primary.get("server_url") and primary.get("server_url") not in urls:
                    urls.append(primary.get("server_url"))
                return urls

            reachable = []

            def _add_url(url):
                if url and url not in reachable:
                    reachable.append(url)

            try:
                servers = get_servers(token, client_id)
            except Exception:
                servers = []

            target = None
            machine_id = primary.get("server_machine_id", "")
            server_name = primary.get("server_name", "")
            for srv in servers:
                if machine_id and srv.get("machine_id") == machine_id:
                    target = srv
                    break
            if target is None:
                for srv in servers:
                    if server_name and srv.get("name") == server_name:
                        target = srv
                        break
            if target is None and servers:
                target = servers[0]

            if target:
                access_token = target.get("access_token") or token
                for conn in target.get("connections", []) or []:
                    uri = conn.get("uri", "")
                    if not uri:
                        continue
                    try:
                        resp = requests.get(
                            f"{uri}/identity",
                            headers={"X-Plex-Token": access_token, "Accept": "application/json"},
                            timeout=3,
                            verify=False,
                        )
                        if resp.status_code == 200:
                            _add_url(uri)
                    except Exception:
                        continue
                _add_url(target.get("uri", ""))

            _add_url(primary.get("server_url", ""))
            _add_url(server_url)
            return reachable

        default_server_url = server_url
        server_url_options = [default_server_url] if default_server_url else []

        with ui.row().classes("items-center gap-2 w-full"):
            url_select = ui.select(
                server_url_options,
                value=default_server_url,
                label="Server Address",
            ).classes("flex-1").props("outlined dark color=amber")

            async def _refresh_server_urls(silent=False):
                loop_ = asyncio.get_event_loop()
                with ThreadPoolExecutor() as pool_:
                    refreshed = await loop_.run_in_executor(pool_, _discover_server_url_options)
                if not refreshed:
                    if not silent:
                        ui.notify("No reachable Plex server URLs found", type="warning")
                    return

                selected = url_select.value
                next_value = selected if selected in refreshed else refreshed[0]
                url_select.set_options(refreshed, value=next_value)
                if not silent:
                    ui.notify("Server URL list refreshed", type="positive")

            def _on_open(_):
                asyncio.create_task(_refresh_server_urls(silent=True))

            url_select.on("popup-show", _on_open)

            ui.button("Refresh", icon="refresh", on_click=_refresh_server_urls).props("flat color=amber")
        with ui.row().classes("gap-4 w-full"):
            partial_toggle = ui.switch("Enable Partial Library Scans", value=partial_scan == "true").style(
                f"color: {COLORS['text']}")
            delay_input = ui.input("Scan Delay (seconds)", value=str(scan_delay)).classes("w-40").props(
                "outlined dark color=amber dense")

        remove_select = ui.select(
            {"movie": "Movies only", "show": "Shows only", "both": "Both", "none": "None"},
            value=auto_remove,
            label="Auto-remove from watchlist after download",
        ).classes("w-full").props("outlined dark color=amber")

        async def save_plex_settings():
            db.set_setting("Plex server address", url_select.value or "", "library")
            db.set_setting("Plex library partial scan", "true" if partial_toggle.value else "false", "library")
            db.set_setting("Plex library refresh delay", delay_input.value, "library")
            db.set_setting("Plex auto remove", remove_select.value, "content")
            ui.notify("Plex settings saved", type="positive")

        ui.button("Save Plex Settings", on_click=save_plex_settings, icon="save").props("color=amber push").classes("mt-4")


async def _render_content_settings(app_state):
    """Render content service settings with inline config when enabled."""
    db = app_state.db

    # Active content services
    with ui.card().classes("w-full p-4"):
        ui.label("Content Services").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.label("Services that plex_debrid monitors for new content. Enable a service to configure it.").classes(
            "text-sm").style(f"color: {COLORS['text_muted']}")
        ui.separator().classes("my-2")

        active_services = db.get_setting("Content Services", ["Plex"])

        # ── Plex watchlist ──
        plex_toggle = ui.switch("Plex Watchlist", value="Plex" in active_services).style(f"color: {COLORS['text']}")
        plex_config = ui.column().classes("w-full gap-2 pl-8 pb-3")
        plex_config.bind_visibility_from(plex_toggle, "value")
        with plex_config:
            ui.label("Plex watchlists are monitored automatically via your connected Plex account.").classes(
                "text-xs").style(f"color: {COLORS['text_muted']}")

        ui.separator().classes("my-1").style("opacity: 0.2")

        # ── Trakt ──
        trakt_toggle = ui.switch("Trakt", value="Trakt" in active_services).style(f"color: {COLORS['text']}")
        trakt_config = ui.column().classes("w-full gap-2 pl-8 pb-3")
        trakt_config.bind_visibility_from(trakt_toggle, "value")
        with trakt_config:
            trakt_users = db.get_trakt_users()
            if trakt_users:
                ui.label("Connected Trakt accounts:").classes("text-xs").style(f"color: {COLORS['text_muted']}")
                for u in trakt_users:
                    with ui.row().classes("items-center gap-2 p-1 px-2").style(
                        f"background: {COLORS['surface_light']}; border-radius: 0"
                    ):
                        ui.icon("person", size="sm").style(f"color: {COLORS['primary']}")
                        ui.label(u["username"]).classes("text-sm").style(f"color: {COLORS['text']}")
            else:
                ui.label("No Trakt accounts linked. Add one via Trakt auth.").classes("text-xs").style(
                    f"color: {COLORS['text_muted']}")

            trakt_early = db.get_setting("Trakt early movie releases", "false")
            trakt_early_toggle = ui.switch(
                "Enable early movie release checking", value=trakt_early == "true"
            ).style(f"color: {COLORS['text']}")

            async def save_trakt_early():
                db.set_setting("Trakt early movie releases", "true" if trakt_early_toggle.value else "false", "content")
                ui.notify("Trakt setting saved", type="positive")

            trakt_early_toggle.on("change", save_trakt_early)

        ui.separator().classes("my-1").style("opacity: 0.2")

        # ── Overseerr ──
        overseerr_toggle = ui.switch("Overseerr", value="Overseerr" in active_services).style(
            f"color: {COLORS['text']}")
        overseerr_config = ui.column().classes("w-full gap-2 pl-8 pb-3")
        overseerr_config.bind_visibility_from(overseerr_toggle, "value")
        with overseerr_config:
            overseerr_url = db.get_setting("Overseerr Base URL", "")
            overseerr_key = db.get_setting("Overseerr API Key", "")

            ov_url_input = ui.input("Overseerr URL", value=overseerr_url, placeholder="http://localhost:5055").classes(
                "w-full").props("outlined dense dark color=amber")
            ov_key_input = ui.input("API Key", value=overseerr_key, password=True,
                                     password_toggle_button=True).classes("w-full").props("outlined dense dark color=amber")

            async def save_overseerr_inline():
                db.set_setting("Overseerr Base URL", ov_url_input.value.strip(), "content")
                db.set_setting("Overseerr API Key", ov_key_input.value.strip(), "content")
                ui.notify("Overseerr settings saved", type="positive")

            ui.button("Save Overseerr", on_click=save_overseerr_inline, icon="save").props(
                "color=amber push dense size=sm").classes("mt-1")

        # Master save for service toggles
        ui.separator().classes("my-2")

        async def save_content_services():
            active = []
            if plex_toggle.value:
                active.append("Plex")
            if trakt_toggle.value:
                active.append("Trakt")
            if overseerr_toggle.value:
                active.append("Overseerr")
            db.set_setting("Content Services", active, "content")
            ui.notify("Content services saved", type="positive")

        ui.button("Save Content Services", on_click=save_content_services, icon="save").props(
            "color=amber push").classes("mt-2")

    # Library services
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Library Services").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        lib_collection = db.get_setting("Library collection service", ["Plex Library"])
        lib_update = db.get_setting("Library update services", ["Plex Libraries"])
        lib_ignore = db.get_setting("Library ignore services", ["Local Ignore List"])

        collection_options = ["Plex Library", "Trakt Collection", "Jellyfin Library"]
        update_options = ["Plex Libraries", "Plex Labels", "Trakt Collection", "Overseerr Requests", "Jellyfin Libraries"]
        ignore_options = ["Plex Discover Watch Status", "Trakt Watch Status", "Local Ignore List"]

        col_select = ui.select(
            collection_options, value=lib_collection[0] if lib_collection else "Plex Library",
            label="Collection Service"
        ).classes("w-full").props("outlined dark color=amber")

        update_select = ui.select(
            update_options, value=lib_update, label="Update Services", multiple=True
        ).classes("w-full").props("outlined dark color=amber")

        ignore_select = ui.select(
            ignore_options, value=lib_ignore, label="Ignore Services", multiple=True
        ).classes("w-full").props("outlined dark color=amber")

        async def save_library_services():
            db.set_setting("Library collection service", [col_select.value], "library")
            db.set_setting("Library update services", update_select.value if isinstance(update_select.value, list) else [update_select.value], "library")
            db.set_setting("Library ignore services", ignore_select.value if isinstance(ignore_select.value, list) else [ignore_select.value], "library")
            ui.notify("Library services saved", type="positive")

        ui.button("Save", on_click=save_library_services, icon="save").props("color=amber push").classes("mt-4")

    # Discord webhook
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Discord Notifications").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        webhook_url = db.get_setting("Discord Webhook", "")
        webhook_input = ui.input("Discord Webhook URL", value=webhook_url).classes("w-full").props(
            "outlined dark color=amber")

        async def save_webhook():
            db.set_setting("Discord Webhook", webhook_input.value, "content")
            ui.notify("Discord webhook saved", type="positive")

        ui.button("Save", on_click=save_webhook, icon="save").props("color=amber push").classes("mt-4")


async def _render_debrid_settings(app_state):
    """Render Decypharr settings (replaces individual debrid services)."""
    db = app_state.db
    from webapp.decypharr import DecypharrClient

    with ui.card().classes("w-full p-4"):
        ui.label("Decypharr").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.label(
            "Decypharr handles all debrid operations. Configure your Decypharr instance URL, Arr host, "
            "and API key below. The API key authenticates all requests to Decypharr."
        ).classes("text-sm").style(f"color: {COLORS['text_muted']}")
        ui.separator().classes("my-2")

        decypharr_url = db.get_setting("Decypharr Base URL", "")
        decypharr_username = db.get_setting("Decypharr Username", "")
        decypharr_api_key = db.get_setting("Decypharr API Key", "")

        dec_url = ui.input(
            "Decypharr URL", value=decypharr_url,
            placeholder="http://localhost:8282"
        ).classes("w-full").props("outlined dark color=amber")

        dec_username = ui.input(
            "Username (Arr Host)", value=decypharr_username,
            placeholder="http://10.0.0.5:8989"
        ).classes("w-full").props("outlined dark color=amber")
        ui.label(
            "This is the hostname/IP that Decypharr sees as the Arr client. "
            "Typically your machine's IP + Arr port (e.g. http://10.0.0.5:8989)."
        ).classes("text-xs").style(f"color: {COLORS['text_muted']}")

        # Global API Key
        with ui.row().classes("w-full gap-3 items-end"):
            dec_api_key = ui.input(
                "API Key", value=decypharr_api_key,
            ).classes("flex-1").props("outlined dark color=amber readonly")

            async def refresh_key():
                from webapp.decypharr import generate_api_key
                new_key = generate_api_key()
                dec_api_key.value = new_key
                db.set_setting("Decypharr API Key", new_key, "debrid")
                ui.notify("API key regenerated", type="positive")

            ui.button("Regenerate", on_click=refresh_key, icon="vpn_key").props(
                "color=blue push dense")
        ui.label(
            "This key is used as the password for Basic auth on all Decypharr qBit API requests. "
            "Decypharr auto-creates an Arr interface when it receives a new username:password pair."
        ).classes("text-xs").style(f"color: {COLORS['text_muted']}")

        # Connection status row
        status_row = ui.row().classes("items-center gap-2 mt-2")

        async def test_decypharr():
            url = dec_url.value.strip()
            if not url:
                ui.notify("Please enter a Decypharr URL", type="warning")
                return
            # Version check doesn't require auth — use empty password
            client = DecypharrClient(url, username=dec_username.value.strip(), password="")
            ok, info = client.test_connection()
            status_row.clear()
            with status_row:
                if ok:
                    ui.icon("check_circle").style(f"color: {COLORS['success']}")
                    ui.label(f"Connected — Decypharr v{info}").classes("text-sm").style(
                        f"color: {COLORS['success']}")
                else:
                    ui.icon("error").style(f"color: {COLORS['error']}")
                    ui.label(f"Failed: {info}").classes("text-sm").style(f"color: {COLORS['error']}")

        with ui.row().classes("gap-2 mt-2"):
            ui.button("Test Connection", on_click=test_decypharr, icon="wifi_tethering").props(
                "color=blue push dense")

            async def save_decypharr():
                db.set_setting("Decypharr Base URL", dec_url.value.strip(), "debrid")
                db.set_setting("Decypharr Username", dec_username.value.strip(), "debrid")
                db.set_setting("Decypharr API Key", dec_api_key.value.strip(), "debrid")
                ui.notify("Decypharr settings saved", type="positive")

            ui.button("Save", on_click=save_decypharr, icon="save").props("color=amber push dense")

    # Global Download Folder
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Global Download Folder").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.label(
            "Base path where Decypharr places symlinks. Each release version automatically "
            "gets a sub-folder named after its category (e.g. /mnt/symlinks/default). "
            "Changing this will update the download folder for all existing versions."
        ).classes("text-sm").style(f"color: {COLORS['text_muted']}")
        ui.separator().classes("my-2")

        global_dl = db.get_setting("Global Download Folder", "")
        global_dl_input = ui.input(
            "Download Base Folder", value=global_dl,
            placeholder="/mnt/symlinks"
        ).classes("w-full").props("outlined dark color=amber")

        # Show current derived paths
        derived_label_container = ui.column().classes("w-full gap-1 mt-2")

        def _show_derived_paths(base_path):
            derived_label_container.clear()
            base = base_path.strip().rstrip("/")
            if not base:
                return
            versions = db.get_release_versions()
            with derived_label_container:
                for v in versions:
                    cat = v.get("category", "default")
                    ui.label(f"{v['name']}: {base}/{cat}").classes("text-xs font-mono").style(
                        f"color: {COLORS['text_muted']}")

        _show_derived_paths(global_dl)

        async def save_global_download_folder():
            new_base = global_dl_input.value.strip().rstrip("/")
            db.set_setting("Global Download Folder", new_base, "debrid")
            # Update all existing versions' download_folder
            if new_base:
                versions = db.get_release_versions()
                for v in versions:
                    cat = v.get("category", "default")
                    db.update_release_version(v["id"], download_folder=f"{new_base}/{cat}")
            _show_derived_paths(new_base)
            ui.notify("Global download folder saved and all versions updated", type="positive")

        ui.button("Save", on_click=save_global_download_folder, icon="save").props("color=amber push dense").classes("mt-2")

    # Global Media Folder
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Global Media Folder").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.label(
            "Base path where completed content is moved to. Each release version automatically "
            "gets a sub-folder named after its category (e.g. /mnt/media/default). "
            "Changing this will update the media folder for all existing versions."
        ).classes("text-sm").style(f"color: {COLORS['text_muted']}")
        ui.separator().classes("my-2")

        global_mf = db.get_setting("Global Media Folder", "")
        global_mf_input = ui.input(
            "Media Base Folder", value=global_mf,
            placeholder="/mnt/media"
        ).classes("w-full").props("outlined dark color=amber")

        # Show current derived paths
        mf_derived_label_container = ui.column().classes("w-full gap-1 mt-2")

        def _show_derived_media_paths(base_path):
            mf_derived_label_container.clear()
            base = base_path.strip().rstrip("/")
            if not base:
                return
            versions = db.get_release_versions()
            with mf_derived_label_container:
                for v in versions:
                    cat = v.get("category", "default")
                    ui.label(f"{v['name']}: {base}/{cat}").classes("text-xs font-mono").style(
                        f"color: {COLORS['text_muted']}")

        _show_derived_media_paths(global_mf)

        async def save_global_media_folder():
            new_base = global_mf_input.value.strip().rstrip("/")
            db.set_setting("Global Media Folder", new_base, "debrid")
            # Update all existing versions' media_folder
            if new_base:
                versions = db.get_release_versions()
                for v in versions:
                    cat = v.get("category", "default")
                    db.update_release_version(v["id"], media_folder=f"{new_base}/{cat}")
            _show_derived_media_paths(new_base)
            ui.notify("Global media folder saved and all versions updated", type="positive")

        ui.button("Save", on_click=save_global_media_folder, icon="save").props("color=amber push dense").classes("mt-2")

    # Decypharr info card
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Decypharr Status").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        info_container = ui.column().classes("w-full gap-2")

        async def load_decypharr_info():
            url = db.get_setting("Decypharr Base URL", "")
            username = db.get_setting("Decypharr Username", "")
            if not url:
                with info_container:
                    ui.label("Configure Decypharr URL above to see status.").classes("text-sm").style(
                        f"color: {COLORS['text_muted']}")
                return
            # Use empty password for status check (version endpoint is unauthenticated)
            client = DecypharrClient(url, username=username, password="")
            info_container.clear()
            with info_container:
                # Connection & version
                ok, version_info = client.test_connection()
                if ok:
                    ui.label(f"Decypharr v{version_info}").classes("text-sm").style(
                        f"color: {COLORS['success']}")
                else:
                    ui.label(f"Connection error: {version_info}").classes("text-sm").style(
                        f"color: {COLORS['error']}")

                # Username display
                if username:
                    ui.label(f"Arr Host: {username}").classes("text-sm").style(
                        f"color: {COLORS['text']}")

                # Categories from versions
                versions = db.get_release_versions()
                cats = [v["category"] for v in versions if v.get("category")]
                if cats:
                    ui.label(f"Configured categories: {', '.join(cats)}").classes("text-sm").style(
                        f"color: {COLORS['text']}")

        await load_decypharr_info()


async def _render_scraper_settings(app_state):
    """Render scraper source settings."""
    db = app_state.db

    with ui.card().classes("w-full p-4"):
        ui.label("Scraper Sources").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        sources = db.get_scraper_sources()
        all_source_names = ["torrentio", "jackett", "prowlarr", "orionoid", "nyaa", "1337x"]
        existing = {s["name"].lower(): s for s in sources}

        for src_name in all_source_names:
            src = existing.get(src_name)
            with ui.expansion(src_name.title(), icon="search").classes("w-full").style(f"color: {COLORS['text']}"):
                enabled = ui.switch("Enabled", value=src["enabled"] if src else False).style(f"color: {COLORS['text']}")

                config = src.get("config", {}) if src else {}

                config_inputs = {}
                if src_name == "torrentio":
                    config_inputs["default_opts"] = ui.input(
                        "Manifest URL", value=config.get("default_opts", "")
                    ).classes("w-full").props("outlined dark color=amber")
                elif src_name == "jackett":
                    config_inputs["base_url"] = ui.input(
                        "Base URL", value=config.get("base_url", "")
                    ).classes("w-full").props("outlined dark color=amber")
                    config_inputs["api_key"] = ui.input(
                        "API Key", value=config.get("api_key", ""), password=True, password_toggle_button=True
                    ).classes("w-full").props("outlined dark color=amber")
                    config_inputs["resolver_timeout"] = ui.input(
                        "Resolver Timeout (s)", value=config.get("resolver_timeout", "10")
                    ).classes("w-48").props("outlined dark color=amber")
                    config_inputs["filter"] = ui.input(
                        "Indexer Filter", value=config.get("filter", "all")
                    ).classes("w-full").props("outlined dark color=amber")
                elif src_name == "prowlarr":
                    config_inputs["base_url"] = ui.input(
                        "Base URL", value=config.get("base_url", "")
                    ).classes("w-full").props("outlined dark color=amber")
                    config_inputs["api_key"] = ui.input(
                        "API Key", value=config.get("api_key", ""), password=True, password_toggle_button=True
                    ).classes("w-full").props("outlined dark color=amber")
                elif src_name == "orionoid":
                    config_inputs["token"] = ui.input(
                        "API Key / Token", value=config.get("token", ""), password=True, password_toggle_button=True
                    ).classes("w-full").props("outlined dark color=amber")
                elif src_name == "nyaa":
                    config_inputs["params"] = ui.input(
                        "URL Parameters", value=config.get("params", "&c=1_0&s=seeders&o=desc")
                    ).classes("w-full").props("outlined dark color=amber")
                    config_inputs["sleep"] = ui.input(
                        "Sleep Time (s)", value=config.get("sleep", "5")
                    ).classes("w-48").props("outlined dark color=amber")
                    config_inputs["proxy"] = ui.input(
                        "Proxy Domain", value=config.get("proxy", "nyaa.si")
                    ).classes("w-full").props("outlined dark color=amber")

                async def save_source(name=src_name, s=src, en=enabled, ci=config_inputs):
                    new_config = {k: v.value for k, v in ci.items()}
                    if s:
                        db.update_scraper_source(s["id"], enabled=en.value, config=new_config)
                    else:
                        db.add_scraper_source(name=name, enabled=en.value, config=new_config)
                    # Update active list
                    all_sources = db.get_scraper_sources(enabled_only=True)
                    db.set_setting("Sources", [s["name"] for s in all_sources], "scraper")
                    ui.notify(f"{name} settings saved", type="positive")

                ui.button("Save", on_click=save_source, icon="save").props("color=amber push dense").classes("mt-2")

    # Special character renaming
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Special Character Renaming").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.label("Define character replacement rules for release title matching.").classes("text-sm").style(
            f"color: {COLORS['text_muted']}")
        ui.separator().classes("my-2")

        rename_rules = db.get_setting("Special character renaming", [])

        rules_container = ui.column().classes("w-full gap-1")
        if rename_rules:
            with rules_container:
                for rule in rename_rules:
                    if isinstance(rule, list) and len(rule) >= 2:
                        ui.label(f'"{rule[0]}" → "{rule[1]}"').classes("text-sm font-mono").style(
                            f"color: {COLORS['text']}")

        with ui.row().classes("gap-2 mt-2"):
            find_input = ui.input("Find (string or {{regex}})").props("outlined dark color=amber dense")
            replace_input = ui.input("Replace with").props("outlined dark color=amber dense")

            async def add_rename_rule():
                if find_input.value:
                    rules = db.get_setting("Special character renaming", [])
                    rules.append([find_input.value, replace_input.value])
                    db.set_setting("Special character renaming", rules, "scraper")
                    find_input.value = ""
                    replace_input.value = ""
                    ui.notify("Rename rule added", type="positive")

            ui.button("Add", on_click=add_rename_rule, icon="add").props("color=amber push dense")


async def _render_version_settings(app_state):
    """Render release version/rule settings with full GUI editor."""
    db = app_state.db

    # Known trigger/rule attributes and their operators (from releases/__init__.py)
    TRIGGER_ATTRS = {
        "retries": ["==", ">=", "<="],
        "media type": ["all", "movies", "shows"],
        "airtime offset": ["=="],
        "year": ["==", ">=", "<="],
        "title": ["==", "include", "exclude"],
        "user": ["==", "include", "exclude"],
        "genre": ["==", "include", "exclude"],
        "scraper sources": ["==", "include", "exclude"],
        "scraping adjustment": ["scrape w/ airdate format", "add text before title", "add text after title"],
    }
    RULE_ATTRS = {
        "resolution": {"operators": ["==", ">=", "<=", "highest", "lowest"], "weights": ["requirement", "preference", "upgrade"]},
        "bitrate": {"operators": ["==", ">=", "<=", "highest", "lowest"], "weights": ["requirement", "preference"]},
        "size": {"operators": ["==", ">=", "<=", "highest", "lowest"], "weights": ["requirement", "preference"]},
        "seeders": {"operators": ["==", ">=", "<=", "highest", "lowest"], "weights": ["requirement", "preference"]},
        "title": {"operators": ["==", "include", "exclude"], "weights": ["requirement", "preference", "upgrade"]},
        "source": {"operators": ["==", "include", "exclude"], "weights": ["requirement", "preference"]},
        "cache status": {"operators": [], "weights": []},
        "file names": {"operators": ["include", "exclude"], "weights": ["requirement", "preference"]},
        "file sizes": {"operators": ["all files >=", "all files <=", "video files >=", "video files <="], "weights": ["requirement", "preference"]},
    }

    # ── Main Layout ──
    with ui.element("div").classes("w-full").style(
        f"background: {COLORS['surface']}; border-radius: 0; overflow: hidden; border: 1px solid rgba(255,255,255,0.06)"
    ):
        # Title row with Add button
        with ui.row().classes("items-center justify-between w-full px-4 py-3").style(
            f"border-bottom: 1px solid rgba(255,255,255,0.08)"
        ):
            with ui.column().classes("gap-0"):
                ui.label("Release Versions").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
                ui.label(
                    "Define how releases are matched and sorted. Each version has triggers (when to apply) and rules (how to filter/sort)."
                ).classes("text-xs").style(f"color: {COLORS['text_muted']}")

            # Add New button
            async def open_add_dialog():
                versions = db.get_release_versions()
                if not versions:
                    default_data = {
                        "name": "New Version",
                        "language": "en",
                        "triggers": [["retries", "<=", "48"], ["media type", "all", ""]],
                        "rules": [
                            ["resolution", "requirement", "<=", "1080"],
                            ["resolution", "preference", "highest", ""],
                            ["title", "requirement", "exclude", "([^A-Z0-9]|HD|HQ)(CAM|T(ELE)?(S(YNC)?|C(INE)?)|ADS|HINDI)([^A-Z0-9]|RIP|$)"],
                            ["title", "requirement", "exclude", "(3D)"],
                            ["title", "preference", "include", "(EXTENDED|REMASTERED|DIRECTORS|THEATRICAL|UNRATED|UNCUT)"],
                            ["size", "preference", "highest", ""],
                            ["seeders", "preference", "highest", ""],
                            ["size", "requirement", ">=", "0.1"],
                        ],
                    }
                    _open_version_editor(default_data, is_new=True)
                    return

                with ui.dialog() as pick_dlg, ui.card().classes("p-4").style(
                    f"background: {COLORS['surface']}; min-width: 360px"
                ):
                    ui.label("Duplicate Existing Version").classes("text-base font-bold mb-2").style(
                        f"color: {COLORS['primary']}")
                    ui.label("Select a version to use as a starting template:").classes("text-xs mb-3").style(
                        f"color: {COLORS['text_muted']}")

                    for v in versions:
                        async def pick(src=v):
                            pick_dlg.close()
                            dup_cat = f"{src.get('category', 'default')}_copy"
                            _gl_base = db.get_setting("Global Download Folder", "").rstrip("/")
                            dup_dl = f"{_gl_base}/{dup_cat}" if _gl_base else src.get("download_folder", "")
                            _gl_mf_base = db.get_setting("Global Media Folder", "").rstrip("/")
                            dup_mf = f"{_gl_mf_base}/{dup_cat}" if _gl_mf_base else src.get("media_folder", "")
                            dup = {
                                "name": f"{src['name']} (copy)",
                                "language": src.get("language", "en"),
                                "category": dup_cat,
                                "download_folder": dup_dl,
                                "media_folder": dup_mf,
                                "triggers": [list(t) for t in src.get("triggers", [])],
                                "rules": [list(r) for r in src.get("rules", [])],
                            }
                            _open_version_editor(dup, is_new=True)

                        with ui.row().classes("items-center gap-2 w-full cursor-pointer p-2 rounded").style(
                            f"background: {COLORS['surface_light']}"
                        ).on("click", pick):
                            ui.icon("content_copy").style(f"color: {COLORS['primary']}")
                            with ui.column().classes("gap-0"):
                                ui.label(v["name"]).classes("text-sm font-medium").style(f"color: {COLORS['text']}")
                                ui.label(
                                    f"{len(v.get('triggers', []))} triggers, {len(v.get('rules', []))} rules"
                                ).classes("text-xs").style(f"color: {COLORS['text_muted']}")

                    ui.button("Cancel", on_click=pick_dlg.close).props("flat color=grey").classes("mt-2")
                pick_dlg.open()

            ui.button("Add Version", on_click=open_add_dialog, icon="add").props("color=amber push dense")

            async def open_import_dialog():
                """Open dialog to import versions from pasted JSON."""
                with ui.dialog() as import_dlg, ui.card().classes("p-4").style(
                    f"background: {COLORS['surface']}; min-width: 480px; max-width: 600px"
                ):
                    ui.label("Import Versions from JSON").classes("text-base font-bold mb-1").style(
                        f"color: {COLORS['primary']}")
                    ui.label(
                        'Paste your settings.json content (or just the "Versions" array) below.'
                    ).classes("text-xs mb-3").style(f"color: {COLORS['text_muted']}")

                    json_input = ui.textarea(
                        placeholder='{"Versions": [...]} or [[...], ...]'
                    ).classes("w-full").props("outlined rows=10").style(
                        f"background: {COLORS['surface_light']}; color: {COLORS['text']}; font-family: monospace; font-size: 12px"
                    )
                    result_label = ui.label("").classes("text-xs mt-1").style("color: transparent")

                    async def do_import():
                        raw = json_input.value.strip()
                        if not raw:
                            result_label.style(f"color: {COLORS['error']}")
                            result_label.text = "Please paste JSON content."
                            return
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError as e:
                            result_label.style(f"color: {COLORS['error']}")
                            result_label.text = f"Invalid JSON: {e}"
                            return

                        # Accept either {"Versions": [...]} or bare [...]
                        if isinstance(parsed, dict) and "Versions" in parsed:
                            versions_list = parsed["Versions"]
                        elif isinstance(parsed, list):
                            versions_list = parsed
                        else:
                            result_label.style(f"color: {COLORS['error']}")
                            result_label.text = 'Expected {"Versions": [...]} or a list of version arrays.'
                            return

                        if not isinstance(versions_list, list) or not versions_list:
                            result_label.style(f"color: {COLORS['error']}")
                            result_label.text = "Versions list is empty or invalid."
                            return

                        existing = db.get_release_versions()
                        start_order = max((v["sort_order"] for v in existing), default=-1) + 1
                        imported = 0
                        for i, ver in enumerate(versions_list):
                            if not isinstance(ver, list) or len(ver) < 4:
                                continue
                            name = ver[0]
                            triggers = ver[1] if isinstance(ver[1], list) else []
                            language = ver[2] if isinstance(ver[2], str) else "en"
                            rules = ver[3] if isinstance(ver[3], list) else []
                            category = ver[4] if len(ver) > 4 and isinstance(ver[4], str) else "default"

                            enabled = True
                            clean = name.replace("\u0336", "").replace("\u0335", "").replace("\u0334", "")
                            if clean != name:
                                name = clean
                                enabled = False

                            db.add_release_version(
                                name=name,
                                enabled=enabled,
                                triggers=triggers,
                                language=language,
                                rules=rules,
                                category=category,
                                sort_order=start_order + i,
                            )
                            imported += 1

                        if imported:
                            _save_versions_to_json()
                            _refresh_versions()
                            import_dlg.close()
                            ui.notify(f"Imported {imported} version(s).", type="positive")
                        else:
                            result_label.style(f"color: {COLORS['error']}")
                            result_label.text = "No valid versions found in the provided JSON."

                    with ui.row().classes("w-full justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=import_dlg.close).props("flat color=grey")
                        ui.button("Import", on_click=do_import, icon="file_upload").props("color=amber push")
                import_dlg.open()

            ui.button("Import", on_click=open_import_dialog, icon="file_upload").props(
                "flat color=amber dense"
            ).tooltip("Import versions from settings.json")

        # Versions list container
        versions_container = ui.column().classes("w-full gap-0")

    def _save_versions_to_json():
        """Persist current versions to settings.json."""
        versions = db.get_release_versions()
        version_list = []
        for v in versions:
            version_list.append([
                v["name"] if v["enabled"] else _strikethrough(v["name"]),
                v.get("triggers", []),
                v.get("language", "en"),
                v.get("rules", []),
                v.get("category", "default"),
            ])
        settings_path = os.path.join(app_state.config_dir, "settings.json")
        try:
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    data = json.load(f)
            else:
                data = {}
            data["Versions"] = version_list
            with open(settings_path, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.debug(f"Failed to save versions to settings.json: {e}")

    def _strikethrough(text):
        return "".join(c + "\u0336" for c in text)

    def _refresh_versions():
        versions_container.clear()
        versions = db.get_release_versions()
        with versions_container:
            if not versions:
                ui.label("No versions configured. Add one below.").classes("text-sm").style(
                    f"color: {COLORS['text_muted']}")
                return
            for ver in versions:
                _render_version_card(ver, versions)

    def _render_version_card(ver, all_versions):
        with ui.element("div").classes("w-full").style(
            f"border-left: 3px solid {COLORS['primary'] if ver['enabled'] else COLORS['error']};"
            f"border-bottom: 1px solid rgba(255,255,255,0.06);"
            f"background: {COLORS['surface']}"
        ):
            with ui.row().classes("items-center justify-between w-full px-3 py-2"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("tune").style(f"color: {COLORS['primary']}")
                    ui.label(ver["name"]).classes("text-base font-semibold").style(f"color: {COLORS['text']}")
                    if ver["enabled"]:
                        ui.badge("Enabled", color="green").props("dense")
                    else:
                        ui.badge("Disabled", color="red").props("dense")
                with ui.row().classes("items-center gap-1"):
                    async def toggle_enabled(v=ver):
                        db.update_release_version(v["id"], enabled=not v["enabled"])
                        _save_versions_to_json()
                        msg = f"{'Enabled' if not v['enabled'] else 'Disabled'} {v['name']}"
                        ui.notify(msg, type="info")
                        _refresh_versions()

                    async def delete_ver(v=ver):
                        db.delete_release_version(v["id"])
                        _save_versions_to_json()
                        ui.notify(f"Deleted {v['name']}", type="warning")
                        _refresh_versions()

                    async def edit_ver(v=ver):
                        _open_version_editor(v, is_new=False)

                    ui.button(icon="edit", on_click=edit_ver).props("flat dense size=sm color=amber")
                    ui.button(
                        icon="power_settings_new", on_click=toggle_enabled
                    ).props(f"flat dense size=sm color={'red' if ver['enabled'] else 'green'}")
                    ui.button(icon="delete", on_click=delete_ver).props("flat dense size=sm color=red")

            # Summary row
            with ui.row().classes("px-3 pb-2 gap-4 flex-wrap"):
                cat = ver.get('category', 'default')
                ui.label(f"Category: {cat}").classes("text-xs font-medium").style(
                    f"color: {COLORS['primary']}")
                ui.label(f"Language: {ver.get('language', 'en')}").classes("text-xs").style(
                    f"color: {COLORS['text_muted']}")
                trigger_count = len(ver.get("triggers", []))
                rule_count = len(ver.get("rules", []))
                ui.label(f"{trigger_count} triggers").classes("text-xs").style(f"color: {COLORS['text_muted']}")
                ui.label(f"{rule_count} rules").classes("text-xs").style(f"color: {COLORS['text_muted']}")
                dl_f = ver.get("download_folder", "")
                media_f = ver.get("media_folder", "")
                if dl_f:
                    ui.label(f"📥 {dl_f}").classes("text-xs").style(f"color: {COLORS['text_muted']}")
                if media_f:
                    ui.label(f"📁 {media_f}").classes("text-xs").style(f"color: {COLORS['text_muted']}")

    def _open_version_editor(ver_data, is_new=False):
        """Open a dialog to edit a version's triggers, rules, language, name, and category."""
        edit_state = {
            "name": ver_data["name"],
            "language": ver_data.get("language", "en"),
            "category": ver_data.get("category", ""),
            "download_folder": ver_data.get("download_folder", ""),
            "media_folder": ver_data.get("media_folder", ""),
            "triggers": [list(t) for t in ver_data.get("triggers", [])],
            "rules": [list(r) for r in ver_data.get("rules", [])],
        }

        with ui.dialog() as dlg, ui.card().classes("w-full").style(
            f"background: {COLORS['background']}; max-width: 1100px; width: 90vw; margin: auto; display: flex; flex-direction: column"
        ):
            # Header
            with ui.row().classes("items-center justify-between w-full px-4 py-2").style(
                f"background: {COLORS['surface']}; border-bottom: 1px solid rgba(255,255,255,0.08); flex-shrink: 0"
            ):
                ui.label("Edit Version" if not is_new else "New Version").classes(
                    "text-base font-bold").style(f"color: {COLORS['primary']}")
                ui.button(icon="close", on_click=dlg.close).props("flat dense color=grey")

            with ui.scroll_area().classes("w-full").style("min-height: 400px; max-height: 65vh"):
                with ui.column().classes("w-full gap-3 p-4"):
                    # Name + Language + Category
                    with ui.row().classes("w-full gap-3"):
                        name_input = ui.input("Version Name", value=edit_state["name"]).classes("flex-1").props(
                            "outlined dense dark color=amber")
                        lang_input = ui.input("Language", value=edit_state["language"]).classes("w-24").props(
                            "outlined dense dark color=amber")
                        category_input = ui.input(
                            "Category", value=edit_state["category"],
                            placeholder="e.g. movies, shows, anime"
                        ).classes("flex-1").props("outlined dense dark color=amber")
                        ui.label("*").classes("text-sm mt-2").style(f"color: {COLORS['error']}")

                    # Download Folder + Media Folder
                    with ui.card().classes("w-full p-3").style(f"background: {COLORS['surface_light']}"):
                        ui.label("Folder Paths").classes("text-sm font-bold mb-2").style(f"color: {COLORS['primary']}")
                        ui.label(
                            "Both folders are auto-derived from the global base folders + category name. "
                            "Change the category above to update them."
                        ).classes("text-xs mb-2").style(f"color: {COLORS['text_muted']}")

                        # Auto-derive download folder and media folder from global base + category
                        _global_dl_base = db.get_setting("Global Download Folder", "").rstrip("/")
                        _global_mf_base = db.get_setting("Global Media Folder", "").rstrip("/")
                        _cat = edit_state.get("category", "default") or "default"
                        _derived_dl = f"{_global_dl_base}/{_cat}" if _global_dl_base else edit_state.get("download_folder", "")
                        _derived_mf = f"{_global_mf_base}/{_cat}" if _global_mf_base else edit_state.get("media_folder", "")

                        with ui.row().classes("w-full gap-3"):
                            dl_folder_input = ui.input(
                                "Download Folder (auto-derived)",
                                value=_derived_dl,
                                placeholder="Set global download folder in Decypharr tab"
                            ).classes("flex-1").props("outlined dense dark color=amber readonly")
                            media_folder_input = ui.input(
                                "Media Folder (auto-derived)",
                                value=_derived_mf,
                                placeholder="Set global media folder in Decypharr tab"
                            ).classes("flex-1").props("outlined dense dark color=amber readonly")

                        # Update download & media folder when category changes
                        def _on_category_change(e):
                            cat_val = str(e.args).strip() if e.args else "default"
                            if _global_dl_base:
                                dl_folder_input.value = f"{_global_dl_base}/{cat_val}"
                            if _global_mf_base:
                                media_folder_input.value = f"{_global_mf_base}/{cat_val}"

                        category_input.on("update:model-value", _on_category_change)

                    # ── Triggers Section ──
                    with ui.card().classes("w-full p-3").style(f"background: {COLORS['surface_light']}"):
                        with ui.row().classes("items-center justify-between w-full mb-2"):
                            ui.label("Triggers").classes("text-sm font-bold").style(f"color: {COLORS['primary']}")

                            def add_trigger():
                                edit_state["triggers"].append(["retries", "<=", "48"])
                                _refresh_editor_triggers()

                            ui.button("Add Trigger", on_click=add_trigger, icon="add").props(
                                "flat dense size=sm color=amber")

                        triggers_container = ui.column().classes("w-full gap-2")

                        def _refresh_editor_triggers():
                            triggers_container.clear()
                            with triggers_container:
                                for idx, trig in enumerate(edit_state["triggers"]):
                                    _render_trigger_row(idx, trig)

                        def _render_trigger_row(idx, trig):
                            # Ensure trigger has 3 elements
                            while len(trig) < 3:
                                trig.append("")
                            with ui.row().classes("items-center gap-2 w-full"):
                                attr_val = trig[0] if trig[0] in TRIGGER_ATTRS else list(TRIGGER_ATTRS.keys())[0]
                                operators = TRIGGER_ATTRS.get(attr_val, ["=="])

                                def on_attr_change(e, i=idx):
                                    edit_state["triggers"][i][0] = e.value
                                    ops = TRIGGER_ATTRS.get(e.value, ["=="])
                                    edit_state["triggers"][i][1] = ops[0]
                                    edit_state["triggers"][i][2] = ""
                                    _refresh_editor_triggers()

                                ui.select(
                                    list(TRIGGER_ATTRS.keys()), value=attr_val,
                                    on_change=on_attr_change
                                ).classes("min-w-40").props("outlined dense dark color=amber")

                                def on_op_change(e, i=idx):
                                    edit_state["triggers"][i][1] = e.value

                                ui.select(
                                    operators, value=trig[1] if trig[1] in operators else operators[0],
                                    on_change=on_op_change
                                ).classes("min-w-40").props("outlined dense dark color=amber")

                                # Value - not needed for "media type" (operator IS the value)
                                if attr_val not in ("media type", "scraping adjustment"):
                                    def on_val_change(e, i=idx):
                                        edit_state["triggers"][i][2] = e.value

                                    ui.input(
                                        "Value", value=trig[2], on_change=on_val_change
                                    ).classes("flex-1").props("outlined dense dark color=amber")
                                else:
                                    ui.element("div").classes("flex-1")

                                def remove_trigger(i=idx):
                                    edit_state["triggers"].pop(i)
                                    _refresh_editor_triggers()

                                ui.button(icon="delete", on_click=remove_trigger).props("flat dense size=sm color=red")

                        _refresh_editor_triggers()

                    # ── Rules Section ──
                    with ui.card().classes("w-full p-3").style(f"background: {COLORS['surface_light']}"):
                        with ui.row().classes("items-center justify-between w-full mb-2"):
                            ui.label("Rules").classes("text-sm font-bold").style(f"color: {COLORS['primary']}")

                            def add_rule():
                                edit_state["rules"].append(["resolution", "requirement", "<=", "1080"])
                                _refresh_editor_rules()

                            ui.button("Add Rule", on_click=add_rule, icon="add").props(
                                "flat dense size=sm color=amber")

                        rules_container = ui.column().classes("w-full gap-2")

                        def _refresh_editor_rules():
                            rules_container.clear()
                            with rules_container:
                                for idx, rule in enumerate(edit_state["rules"]):
                                    _render_rule_row(idx, rule)

                        def _render_rule_row(idx, rule):
                            while len(rule) < 4:
                                rule.append("")
                            attr_val = rule[0] if rule[0] in RULE_ATTRS else list(RULE_ATTRS.keys())[0]
                            attr_info = RULE_ATTRS.get(attr_val, {"operators": ["=="], "weights": ["requirement"]})

                            with ui.row().classes("items-center gap-2 w-full"):
                                def on_attr_change(e, i=idx):
                                    edit_state["rules"][i][0] = e.value
                                    info = RULE_ATTRS.get(e.value, {"operators": ["=="], "weights": ["requirement"]})
                                    edit_state["rules"][i][1] = info["weights"][0]
                                    edit_state["rules"][i][2] = info["operators"][0]
                                    edit_state["rules"][i][3] = ""
                                    _refresh_editor_rules()

                                ui.select(
                                    list(RULE_ATTRS.keys()), value=attr_val,
                                    on_change=on_attr_change
                                ).classes("min-w-32").props("outlined dense dark color=amber")

                                def on_weight_change(e, i=idx):
                                    edit_state["rules"][i][1] = e.value

                                ui.select(
                                    attr_info["weights"],
                                    value=rule[1] if rule[1] in attr_info["weights"] else attr_info["weights"][0],
                                    on_change=on_weight_change
                                ).classes("min-w-32").props("outlined dense dark color=amber")

                                def on_op_change(e, i=idx):
                                    edit_state["rules"][i][2] = e.value

                                ui.select(
                                    attr_info["operators"],
                                    value=rule[2] if rule[2] in attr_info["operators"] else attr_info["operators"][0],
                                    on_change=on_op_change
                                ).classes("min-w-32").props("outlined dense dark color=amber")

                                # Value input (not needed for highest/lowest/cached/uncached)
                                no_value_ops = ("highest", "lowest", "cached", "uncached")
                                if rule[2] not in no_value_ops:
                                    def on_val_change(e, i=idx):
                                        edit_state["rules"][i][3] = e.value

                                    ui.input(
                                        "Value", value=rule[3], on_change=on_val_change
                                    ).classes("flex-1").props("outlined dense dark color=amber")
                                else:
                                    ui.element("div").classes("flex-1")

                                def remove_rule(i=idx):
                                    edit_state["rules"].pop(i)
                                    _refresh_editor_rules()

                                ui.button(icon="delete", on_click=remove_rule).props("flat dense size=sm color=red")

                        _refresh_editor_rules()

            # Footer
            with ui.row().classes("w-full justify-end gap-2 px-4 py-2").style(
                f"background: {COLORS['surface']}; border-top: 1px solid rgba(255,255,255,0.08); flex-shrink: 0"
            ):
                ui.button("Cancel", on_click=dlg.close, icon="close").props("flat color=grey")

                async def save():
                    if not name_input.value.strip():
                        ui.notify("Version name is required", type="negative")
                        return
                    if not category_input.value.strip():
                        ui.notify("Category is required (used for Decypharr routing)", type="negative")
                        return
                    # Auto-derive download folder and media folder from global base + category
                    cat_val = category_input.value.strip()
                    global_dl_base = db.get_setting("Global Download Folder", "").rstrip("/")
                    global_mf_base = db.get_setting("Global Media Folder", "").rstrip("/")
                    derived_dl = f"{global_dl_base}/{cat_val}" if global_dl_base and cat_val else dl_folder_input.value.strip()
                    derived_mf = f"{global_mf_base}/{cat_val}" if global_mf_base and cat_val else media_folder_input.value.strip()
                    data = {
                        "name": name_input.value.strip(),
                        "language": lang_input.value.strip() or "en",
                        "category": cat_val,
                        "download_folder": derived_dl,
                        "media_folder": derived_mf,
                        "triggers": edit_state["triggers"],
                        "rules": edit_state["rules"],
                    }
                    if is_new:
                        data["enabled"] = True
                        data["sort_order"] = len(db.get_release_versions())
                        db.add_release_version(**data)
                        ui.notify(f"Version '{data['name']}' created", type="positive")
                    else:
                        db.update_release_version(ver_data["id"], **data)
                        ui.notify(f"Version '{data['name']}' saved", type="positive")
                    _save_versions_to_json()
                    dlg.close()
                    _refresh_versions()

                ui.button("Save", on_click=save, icon="save").props("color=amber push")

        dlg.open()

    _refresh_versions()


async def _render_advanced_settings(app_state):
    """Render advanced/system settings."""
    db = app_state.db

    with ui.card().classes("w-full p-4"):
        ui.label("Logging").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        current_level = db.get_setting("Log level", "info")
        # Backwards compat: old bool → new level
        if current_level not in ("info", "debug", "trace"):
            current_level = "debug" if db.get_setting("Debug printing", "false") == "true" else "info"
        log_file = db.get_setting("Log to file", "false")

        level_select = ui.select(
            {"info": "Info  – normal output", "debug": "Debug  – app debug messages", "trace": "Trace  – everything (incl. urllib3, nicegui)"},
            value=current_level,
            label="Log Level",
        ).classes("w-full").props("outlined dark color=amber")

        log_toggle = ui.switch("Log to File (plex_debrid.log)", value=log_file == "true").style(f"color: {COLORS['text']}")
        ui.label(
            "When enabled a rotating log file is written to the config directory. "
            "Max 5 MB per file, 3 backups kept."
        ).classes("text-xs").style(f"color: {COLORS['text_muted']}")

        async def save_log_settings():
            chosen = level_select.value
            db.set_setting("Log level", chosen, "ui")
            # Keep legacy key in sync
            db.set_setting("Debug printing", "true" if chosen in ("debug", "trace") else "false", "ui")
            db.set_setting("Log to file", "true" if log_toggle.value else "false", "ui")
            # Hot-reload logging at runtime
            from webapp.log_config import reconfigure
            reconfigure(
                log_level=chosen,
                log_to_file=log_toggle.value,
                config_dir=app_state.config_dir,
            )
            ui.notify("Logging settings saved & applied", type="positive")

        ui.button("Save", on_click=save_log_settings, icon="save").props("color=amber push").classes("mt-4")

    # Database management
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Database Management").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        with ui.row().classes("gap-4"):
            async def export_settings():
                settings = db.get_all_settings()
                import json
                with open(app_state.config_dir + '/settings_export.json', 'w') as f:
                    json.dump(settings, f, indent=4)
                ui.notify("Settings exported to settings_export.json", type="positive")

            async def import_legacy():
                if db.has_legacy_settings():
                    db.migrate_from_json()
                    ui.notify("Legacy settings imported successfully", type="positive")
                else:
                    ui.notify("No legacy settings.json found", type="warning")

            async def reset_db():
                db.set_setting("setup_complete", False, "system")
                app_state.needs_onboarding = True
                ui.notify("Setup reset. Redirecting to onboarding...", type="warning")
                ui.navigate.to("/onboarding")

            ui.button("Export Settings", on_click=export_settings, icon="download").props("color=amber push")
            ui.button("Import Legacy Settings", on_click=import_legacy, icon="upload").props("color=blue push")
            ui.button("Reset Setup", on_click=reset_db, icon="restart_alt").props("color=red push")

    # Ignored media management
    with ui.card().classes("w-full p-4 mt-4"):
        ui.label("Ignored Media").classes("text-lg font-semibold").style(f"color: {COLORS['text']}")
        ui.separator().classes("my-2")

        ignored = db.get_ignored_items()

        if ignored:
            columns = [
                {"name": "title", "label": "Title", "field": "title", "align": "left"},
                {"name": "imdb_id", "label": "IMDB", "field": "imdb_id", "align": "left"},
                {"name": "media_type", "label": "Type", "field": "media_type", "align": "left"},
                {"name": "reason", "label": "Reason", "field": "reason", "align": "left"},
                {"name": "retry_count", "label": "Retries", "field": "retry_count", "align": "right"},
            ]
            ui.table(columns=columns, rows=ignored, row_key="id").classes("w-full").props("dark flat dense")
        else:
            ui.label("No ignored items.").classes("text-sm").style(f"color: {COLORS['text_muted']}")
