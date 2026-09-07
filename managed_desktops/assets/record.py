#!/usr/bin/python3
"""Continuous per-screen FFmpeg recording, owned by systemd rather than SSH."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys
import time


def active(unit, run):
    result = run(["systemctl", "show", "--property=ActiveState", "--value", unit],
                 check=True, capture_output=True, text=True)
    return result.stdout.strip() in ("active", "activating", "deactivating", "reloading")


def start(screen, name, root, unit, run):
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,90}", name):
        raise ValueError("A recording name using letters, digits, dots, hyphens or underscores is required")
    destination = root / (name if name.endswith(".mp4") else name + ".mp4")
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Recording already exists: {destination}")
    if active(unit, run):
        raise ValueError("This screen is already recording; stop it first")
    command = [
        "sudo", "-n", "systemd-run", "--collect", "--service-type=exec",
        f"--unit={unit}", "--uid=agent", "--gid=agent",
        "--setenv=HOME=/home/agent", f"--working-directory={root}",
        "--property=KillSignal=SIGINT", "--property=TimeoutStopSec=60",
        "--property=Restart=no", "--property=UMask=0077",
        f"--property=After=hermes-vm-desktop@{screen}.service",
        f"--property=PartOf=hermes-vm-desktop@{screen}.service",
        "/usr/local/bin/hermes-vm-screen", str(screen),
        "/usr/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-n",
        "-f", "x11grab", "-framerate", "20", "-video_size", "1440x900", "-draw_mouse", "1",
        "-i", f":{screen}.0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
    ]
    run(command, check=True, stdout=subprocess.DEVNULL)
    # systemd-run's success only proves exec; wait for FFmpeg to open real output.
    deadline = time.monotonic() + 15
    while not destination.exists() or destination.stat().st_size == 0:
        if not active(unit, run) or time.monotonic() >= deadline:
            run(["sudo", "-n", "systemctl", "stop", unit], check=True)
            raise RuntimeError(f"Recording did not start; inspect journalctl -u {unit}")
        time.sleep(0.1)
    if not active(unit, run):
        raise RuntimeError(f"Recording exited; inspect journalctl -u {unit}")
    data = {"path": str(destination), "unit": unit, "screen": screen}
    state = root / f"active-{screen}.json"
    temporary = state.with_suffix(".tmp")
    temporary.write_text(json.dumps(data) + "\n")
    temporary.replace(state)
    return {"recording": True, **data}


def stop(screen, root, unit, run):
    if not active(unit, run):
        raise ValueError("No active recording on this screen")
    data = json.loads((root / f"active-{screen}.json").read_text())
    # systemctl waits for FFmpeg to receive SIGINT and write its MP4 trailer.
    run(["sudo", "-n", "systemctl", "stop", unit], check=True)
    probe = run(["ffprobe", "-v", "error", "-show_entries",
                 "format=duration,size:stream=codec_name,width,height", "-of", "json", data["path"]],
                check=True, capture_output=True, text=True)
    if active(unit, run):
        raise RuntimeError("Recording service is still active after stop")
    return {"recording": False, **data, "probe": json.loads(probe.stdout)}


def main(argv=None, root=Path("/home/agent/recordings"), run=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screen", type=int, choices=(1, 2))
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("name", nargs="?")
    args = parser.parse_args(argv)
    if (args.action == "start") != (args.name is not None):
        parser.error("Use start NAME, stop, or status")
    unit = f"hermes-vm-record-{args.screen}.service"
    # Serialize name checks across both screens, including simultaneous SSH calls.
    with (root / ".recording.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "start":
            result = start(args.screen, args.name, root, unit, run)
        elif args.action == "stop":
            result = stop(args.screen, root, unit, run)
        else:
            state = root / f"active-{args.screen}.json"
            result = {**(json.loads(state.read_text()) if state.exists() else {}),
                      "recording": active(unit, run), "unit": unit, "screen": args.screen}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
