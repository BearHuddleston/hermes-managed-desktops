#!/usr/bin/python3
"""Bounded live probes; a marker never substitutes for service readiness."""
import argparse
from pathlib import Path
import socket
import subprocess
import time
import urllib.request


def view_ready(screen):
    with socket.create_connection(("127.0.0.1", 5900 + screen), timeout=2) as vnc:
        if not vnc.recv(12).startswith(b"RFB "):
            raise RuntimeError("VNC protocol not ready")
    with urllib.request.urlopen(f"http://127.0.0.1:{6080 + screen}/vnc.html", timeout=2) as response:
        if response.status != 200:
            raise RuntimeError("noVNC HTTP not ready")


def cua_ready(screen):
    subprocess.run(["/usr/local/bin/hermes-vm-cua", str(screen), "call", "list_windows", "{}"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)


def wait_probe(probe, screen, seconds=60):
    deadline = time.monotonic() + seconds
    while True:
        try:
            probe(screen)
            return
        except (OSError, RuntimeError, subprocess.SubprocessError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("cua", "view", "all"))
    parser.add_argument("screen", type=int, nargs="?", choices=(1, 2))
    args = parser.parse_args()
    if args.kind != "all":
        if args.screen is None:
            parser.error("A screen is required")
        wait_probe({"cua": cua_ready, "view": view_ready}[args.kind], args.screen)
        return
    marker = Path("/var/lib/hermes-desktop-vm/ready")
    marker.unlink(missing_ok=True)
    if not marker.with_name("installed").exists():
        raise RuntimeError("Bootstrap has not completed")
    units = [f"hermes-vm-{kind}@{screen}.service"
             for kind in ("desktop", "cua", "view") for screen in (1, 2)]
    states = subprocess.check_output(["systemctl", "is-active", *units], text=True).splitlines()
    if states != ["active"] * len(units):
        raise RuntimeError("Not all desktop services are active")
    for screen in (1, 2):
        wait_probe(cua_ready, screen)
        wait_probe(view_ready, screen)
    temporary = marker.with_suffix(".tmp")
    temporary.write_text("ready\n")
    temporary.replace(marker)


if __name__ == "__main__":
    main()
