#!/usr/bin/python3
"""Only address an existing screen daemon; never start or discover another one."""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import sys


def daemon_request(endpoint, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(15)
        connection.connect(str(endpoint))
        connection.sendall((json.dumps(payload) + "\n").encode())
        with connection.makefile("rb") as stream:
            response = json.loads(stream.readline(16 * 1024 * 1024))
    if not response.get("ok"):
        raise ValueError(response.get("error", "Cua daemon refused request"))
    return response["result"]


def main(argv=None, runtime_root=Path("/run"), run=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screen", choices=("1", "2"))
    parser.add_argument("action", choices=("call", "describe", "tools"))
    parser.add_argument("args", nargs=argparse.REMAINDER)
    opts = parser.parse_args(argv)
    if opts.action == "tools":
        if opts.args:
            parser.error("tools takes no arguments")
    else:
        if not opts.args or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", opts.args[0]):
            parser.error("A tool name is required")
        if opts.action == "describe" and len(opts.args) != 1:
            parser.error("describe takes one tool name")
    data = {}
    if opts.action == "call":
        if len(opts.args) > 2:
            parser.error("call takes TOOL and optional JSON object; driver flags are not accepted")
        data = json.loads(opts.args[1]) if len(opts.args) == 2 else {}
        if not isinstance(data, dict):
            parser.error("Tool arguments must be a JSON object")
    endpoint = runtime_root / f"hermes-vm-desktop-{opts.screen}" / "cua.sock"
    # This is a real connection and protocol probe, not just a stale socket-file test.
    if opts.action != "call":
        payload = {"method": "list" if opts.action == "tools" else "describe"}
        if opts.args:
            payload["name"] = opts.args[0]
        print(json.dumps(daemon_request(endpoint, payload)))
        return 0
    daemon_request(endpoint, {"method": "metadata"})
    # Pinned 0.23.2's call verb is service-only even if the daemon dies after this
    # probe. Never use `mcp`, whose proxy path may start a new runtime.
    command = ["/usr/local/bin/hermes-vm-screen", opts.screen,
               "/opt/hermes-desktop-vm/cua/cua-driver", "call", opts.args[0],
               json.dumps(data), "--socket", str(endpoint)]
    return run(command, check=False, env={"PATH": "/usr/bin:/bin", "HOME": "/home/agent",
               "LANG": "C.UTF-8", "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0"}).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"Cua daemon unavailable: {error}")
