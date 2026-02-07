"""
Plex OAuth browser authentication for pd_reloaded.
Replaces manual token entry with proper browser-based auth flow.
"""

import uuid
import time
import logging
import requests
import urllib3
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

# Suppress SSL warnings for local Plex connections
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

PLEX_AUTH_URL = "https://app.plex.tv/auth#!"
PLEX_PIN_URL = "https://plex.tv/api/v2/pins"
PLEX_RESOURCES_URL = "https://plex.tv/api/v2/resources"
PLEX_USER_URL = "https://plex.tv/api/v2/user"

PLEX_HEADERS = {
    "Accept": "application/json",
    "X-Plex-Product": "pd_reloaded",
    "X-Plex-Version": "3.0.0",
    "X-Plex-Platform": "Web",
}


def generate_client_id():
    """Generate a unique client identifier."""
    return str(uuid.uuid4())


def create_pin(client_id):
    """
    Create a Plex PIN for authentication.
    Returns (pin_id, pin_code, auth_url).
    """
    headers = {**PLEX_HEADERS, "X-Plex-Client-Identifier": client_id}
    data = {"strong": "true", "X-Plex-Product": "pd_reloaded", "X-Plex-Client-Identifier": client_id}

    try:
        response = requests.post(PLEX_PIN_URL, headers=headers, data=data, timeout=10)
        response.raise_for_status()
        result = response.json()

        pin_id = result["id"]
        pin_code = result["code"]

        auth_url = (
            f"{PLEX_AUTH_URL}?clientID={client_id}"
            f"&code={pin_code}"
            f"&context%5Bdevice%5D%5Bproduct%5D=pd_reloaded"
        )

        return pin_id, pin_code, auth_url

    except Exception as e:
        logger.error(f"Failed to create Plex PIN: {e}")
        return None, None, None


def check_pin(pin_id, client_id):
    """
    Check if the user has authenticated the PIN.
    Returns the auth token if successful, None otherwise.
    """
    headers = {**PLEX_HEADERS, "X-Plex-Client-Identifier": client_id}

    try:
        response = requests.get(
            f"{PLEX_PIN_URL}/{pin_id}",
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()

        auth_token = result.get("authToken")
        if auth_token:
            return auth_token
        return None

    except Exception as e:
        logger.error(f"Failed to check Plex PIN: {e}")
        return None


async def wait_for_auth(pin_id, client_id, timeout=300, poll_interval=2):
    """
    Poll the Plex API until the user authenticates or timeout.
    Returns the auth token or None.
    Uses run_in_executor so synchronous HTTP calls don't block the event loop.
    """
    import asyncio

    loop = asyncio.get_event_loop()
    start = time.time()
    while time.time() - start < timeout:
        token = await loop.run_in_executor(None, check_pin, pin_id, client_id)
        if token:
            return token
        await asyncio.sleep(poll_interval)
    return None


def get_user_info(token, client_id):
    """Get user information from Plex."""
    headers = {
        **PLEX_HEADERS,
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Token": token,
    }

    try:
        response = requests.get(PLEX_USER_URL, headers=headers, timeout=10)
        response.raise_for_status()
        user = response.json()
        return {
            "username": user.get("username", user.get("title", "Unknown")),
            "email": user.get("email", ""),
            "thumb": user.get("thumb", ""),
            "id": user.get("id"),
        }
    except Exception as e:
        logger.error(f"Failed to get Plex user info: {e}")
        return None


def _rank_connection(conn):
    """Rank a Plex connection for preference (lower = better)."""
    uri = conn.get("uri", "")
    local = conn.get("local", False)
    relay = conn.get("relay", False)

    if relay:
        return 100  # Last resort
    if local and uri.startswith("http://"):
        return 0  # Best: local HTTP (direct LAN IP)
    if local and uri.startswith("https://"):
        return 10  # Local HTTPS (.plex.direct) — may not resolve
    if uri.startswith("http://"):
        return 20  # Remote HTTP
    if uri.startswith("https://"):
        return 30  # Remote HTTPS (.plex.direct)
    return 50


def _find_reachable_uri(connections, token):
    """
    Try connections concurrently, return the first one that responds.
    Uses ThreadPoolExecutor for parallel probing with a short timeout.
    """
    ranked = sorted(connections, key=_rank_connection)
    if not ranked:
        return ""

    def _probe(conn):
        uri = conn.get("uri", "")
        if not uri:
            return None
        try:
            resp = requests.get(
                f"{uri}/identity",
                headers={"X-Plex-Token": token, "Accept": "application/json"},
                timeout=3,
                verify=False,
            )
            if resp.status_code == 200:
                logger.info(f"Reachable Plex connection: {uri}")
                return uri
        except Exception:
            logger.debug(f"Connection not reachable: {uri}")
        return None

    # Probe all connections concurrently
    try:
        with ThreadPoolExecutor(max_workers=min(len(ranked), 8)) as pool:
            futures = {pool.submit(_probe, conn): conn for conn in ranked}
            for future in as_completed(futures, timeout=8):
                try:
                    uri = future.result()
                    if uri:
                        # Cancel remaining futures
                        for f in futures:
                            f.cancel()
                        return uri
                except Exception:
                    pass
    except TimeoutError:
        logger.warning("Connection probing timed out")

    # Fallback: return best-ranked URI even if unreachable
    logger.warning(f"No reachable connection found, falling back to {ranked[0].get('uri', '')}")
    return ranked[0].get("uri", "")


def get_servers(token, client_id):
    """Get list of servers owned by the user."""
    headers = {
        **PLEX_HEADERS,
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Token": token,
    }

    try:
        response = requests.get(
            PLEX_RESOURCES_URL,
            headers=headers,
            params={"includeHttps": 1, "includeRelay": 1},
            timeout=10,
        )
        response.raise_for_status()
        resources = response.json()

        servers = []
        for resource in resources:
            if resource.get("provides") == "server":
                connections = resource.get("connections", [])
                access_token = resource.get("accessToken", token)
                best_uri = _find_reachable_uri(connections, access_token)

                servers.append({
                    "name": resource.get("name", "Unknown"),
                    "machine_id": resource.get("clientIdentifier", ""),
                    "owned": resource.get("owned", False),
                    "uri": best_uri,
                    "access_token": access_token,
                    "connections": connections,
                })

        return servers

    except Exception as e:
        logger.error(f"Failed to get Plex servers: {e}")
        return []


def get_libraries(server_url, token, client_id, connections=None):
    """
    Get library sections from a Plex server.
    If server_url fails and connections are provided, tries alternative URIs.
    """
    headers = {
        **PLEX_HEADERS,
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Token": token,
        "Accept": "application/json",
    }

    # Build list of URIs to try: primary first, then alternatives
    uris_to_try = [server_url]
    if connections:
        ranked = sorted(connections, key=_rank_connection)
        for conn in ranked:
            uri = conn.get("uri", "")
            if uri and uri != server_url:
                uris_to_try.append(uri)

    for uri in uris_to_try:
        try:
            response = requests.get(
                f"{uri}/library/sections",
                headers=headers,
                timeout=5,
                verify=False,
            )
            response.raise_for_status()
            data = response.json()

            libraries = []
            container = data.get("MediaContainer", {})
            for section in container.get("Directory", []):
                libraries.append({
                    "key": section.get("key"),
                    "title": section.get("title", "Unknown"),
                    "type": section.get("type", ""),  # movie, show
                    "agent": section.get("agent", ""),
                    "scanner": section.get("scanner", ""),
                })

            logger.info(f"Got {len(libraries)} libraries from {uri}")
            return libraries

        except Exception as e:
            logger.warning(f"Failed to get Plex libraries from {uri}: {e}")
            continue

    return []


def verify_token(token):
    """Verify a Plex token is still valid."""
    try:
        url = f"https://plex.tv/api/v2/user"
        headers = {
            **PLEX_HEADERS,
            "X-Plex-Token": token,
        }
        response = requests.get(url, headers=headers, timeout=10)
        return response.status_code == 200
    except Exception:
        return False
