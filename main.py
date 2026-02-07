"""
pd_reloaded - Modern web-based debrid media manager.

Supports two modes:
  1. Web UI mode (default): Launches NiceGUI web interface on port 8008
  2. Legacy CLI mode: Original terminal-based interface (--legacy flag)

Usage:
  python main.py                          # Web UI mode (default)
  python main.py --config-dir /path       # Specify config directory
  python main.py --legacy                 # Run legacy CLI interface
  python main.py --legacy -service        # Run legacy CLI in service mode
  python main.py --port 9090              # Run web UI on custom port
"""

import os
import sys
import logging
import argparse


def parse_args():
    parser = argparse.ArgumentParser(description="pd_reloaded - Debrid media manager")
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Directory for configuration and database files",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Run in legacy CLI mode instead of web UI",
    )
    parser.add_argument(
        "-service",
        action="store_true",
        help="Run in service mode (legacy CLI only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8008,
        help="Port for the web UI (default: 8008)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind the web UI to (default: 0.0.0.0)",
    )
    return parser.parse_args()


def detect_config_dir(explicit_dir=None):
    """Determine the config directory to use."""
    if explicit_dir:
        os.makedirs(explicit_dir, exist_ok=True)
        return os.path.abspath(explicit_dir)

    # Check current directory for legacy settings.json
    if os.path.exists("./settings.json"):
        if os.path.getsize("./settings.json") > 0 and os.path.isfile("./settings.json"):
            return os.path.abspath(".")

    # Check standard config directory
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
    if os.path.exists(config_path):
        return config_path

    # Default to current directory
    return os.path.abspath(".")


def run_legacy(config_dir, service_mode):
    """Run the original CLI interface."""
    import ui
    ui.run(config_dir, service_mode)


def run_webapp(config_dir, host="0.0.0.0", port=8008):
    """Run the modern NiceGUI web interface."""
    # Configure logging so it appears on CLI
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    log_datefmt = "%H:%M:%S"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=log_datefmt,
        stream=sys.stdout,
    )
    # Apply saved debug mode if any
    try:
        from database.manager import DatabaseManager
        _db = DatabaseManager(config_dir)
        _db.initialize(config_dir)
        if _db.get_setting("Debug printing", "false") == "true":
            logging.getLogger().setLevel(logging.DEBUG)
            logging.getLogger("webapp").setLevel(logging.DEBUG)
            print("[pd_reloaded] Debug logging enabled (from saved settings)")
    except Exception:
        pass

    from webapp.app import create_app, run_app
    create_app(config_dir)
    run_app(host=host, port=port)


if __name__ == "__main__":
    args = parse_args()
    config_dir = detect_config_dir(args.config_dir)

    print(f"[pd_reloaded] Config directory: {config_dir}")

    if args.legacy:
        print("[pd_reloaded] Starting in legacy CLI mode...")
        run_legacy(config_dir, args.service)
    else:
        print(f"[pd_reloaded] Starting web UI on {args.host}:{args.port}...")
        run_webapp(config_dir, host=args.host, port=args.port)