#!/usr/bin/python3
"""Execute argv in one explicit desktop environment, never the SSH environment."""
import argparse
import json
import os
from pathlib import Path
import sys

ENVIRONMENT_KEYS = {
    "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "XDG_CACHE_HOME", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "GTK_MODULES", "NO_AT_BRIDGE",
}


def screen_environment(screen, runtime_root=Path("/run")):
    if str(screen) not in ("1", "2"):
        raise ValueError("Screen must be 1 or 2")
    directory = runtime_root / f"hermes-vm-desktop-{screen}"
    data = json.loads((directory / "environment.json").read_text())
    if set(data) != ENVIRONMENT_KEYS or not all(isinstance(v, str) for v in data.values()):
        raise ValueError("Invalid desktop environment")
    if data["DISPLAY"] != f":{screen}" or data["XDG_RUNTIME_DIR"] != str(directory):
        raise ValueError("Desktop environment belongs to a different screen")
    if data["XAUTHORITY"] != str(directory / "Xauthority") or not data["DBUS_SESSION_BUS_ADDRESS"]:
        raise ValueError("Desktop authentication environment is missing")
    return {"HOME": "/home/agent", "USER": "agent", "LOGNAME": "agent",
            "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
            "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0", **data}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screen", choices=("1", "2"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.command:
        parser.error("A command is required")
    environment = screen_environment(args.screen)
    os.execvpe(args.command[0], args.command, environment)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(f"Desktop unavailable: {error}")
