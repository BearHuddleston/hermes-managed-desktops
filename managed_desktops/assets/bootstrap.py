#!/usr/bin/python3
"""Fresh-guest provisioning; never executes the downloaded installer."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


def install_driver(config, destination):
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix="hermes-cua-") as temporary:
        archive = Path(temporary) / "driver.tar.gz"
        digest = hashlib.sha256()
        with urllib.request.urlopen(config["url"], timeout=120) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != config["sha256"]:
            raise RuntimeError("Cua archive SHA-256 mismatch; nothing extracted or installed")
        extracted = Path(temporary) / "extracted"
        extracted.mkdir()
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(extracted, filter="data")
        if not (extracted / "cua-driver").is_file():
            raise RuntimeError("Verified Cua archive does not contain cua-driver")
        # Keep sibling SDK/cursor files shipped in this exact archive together.
        shutil.copytree(extracted, destination, dirs_exist_ok=True)
        (destination / "cua-driver").chmod(0o755)


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Bootstrap requires guest root")
    agent = pwd.getpwnam("agent")
    if agent.pw_uid != 1000 or agent.pw_dir != "/home/agent":
        raise RuntimeError("Unexpected guest agent identity")
    state = Path("/var/lib/hermes-desktop-vm")
    state.mkdir(parents=True, exist_ok=True)
    (state / "ready").unlink(missing_ok=True)
    for name in ("workspace", "recordings"):
        directory = Path("/home/agent") / name
        directory.mkdir(exist_ok=True, mode=0o700)
        os.chown(directory, agent.pw_uid, agent.pw_gid)
    config = json.loads(Path("/etc/hermes-desktop-vm/cua.json").read_text(encoding="utf-8"))
    install_driver(config, "/opt/hermes-desktop-vm/cua")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "acpid.service"], check=True)
    subprocess.run(["systemctl", "restart", "systemd-logind.service"], check=True)
    units = [f"hermes-vm-{kind}@{screen}.service"
             for kind in ("desktop", "cua", "view") for screen in (1, 2)]
    subprocess.run(["systemctl", "enable", "--now", *units], check=True)
    (state / "installed").write_text(config["version"] + "\n")
    subprocess.run(["systemctl", "enable", "--now", "hermes-vm-ready.service"], check=True)


if __name__ == "__main__":
    main()
