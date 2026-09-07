"""Render the self-contained Debian guest seed for managed agent desktops.

Only the Cua archive is byte-pinned here. Debian packages use the image's signed
Debian 13 apt repositories, receiving security fixes rather than frozen versions.
The returned seed contains a newly generated SSH host private key: callers must
store it privately and must never log the YAML. No host environment is captured.
"""

from importlib.resources import files
import json

import yaml

CUA_VERSION = "0.23.2"
CUA_ARCHIVE_URL = (
    "https://github.com/trycua/cua/releases/download/"
    f"cua-driver-rs-v{CUA_VERSION}/cua-driver-rs-{CUA_VERSION}-linux-x86_64-binary.tar.gz"
)
CUA_ARCHIVE_SHA256 = "01bf8339ec129cc00f4b4b2c6056ef1a7c5b52df39ff83ad17c9b16818aec500"
GUEST_USER = "agent"
GUEST_HOME = "/home/agent"
READY_PATH = "/var/lib/hermes-desktop-vm/ready"

# Paths are explicit so a new incidental asset cannot become a root boot command.
_ASSETS = {
    "bootstrap.py": "/usr/local/lib/hermes-desktop-vm/bootstrap.py",
    "desktop-launch": "/usr/local/lib/hermes-desktop-vm/desktop-launch",
    "desktop-session": "/usr/local/lib/hermes-desktop-vm/desktop-session",
    "screen.py": "/usr/local/bin/hermes-vm-screen",
    "cua.py": "/usr/local/bin/hermes-vm-cua",
    "record.py": "/usr/local/bin/hermes-vm-record",
    "view.py": "/usr/local/lib/hermes-desktop-vm/view.py",
    "view-launch": "/usr/local/lib/hermes-desktop-vm/view-launch",
    "ready.py": "/usr/local/lib/hermes-desktop-vm/ready.py",
    "hermes-vm-desktop@.service": "/etc/systemd/system/hermes-vm-desktop@.service",
    "hermes-vm-cua@.service": "/etc/systemd/system/hermes-vm-cua@.service",
    "hermes-vm-view@.service": "/etc/systemd/system/hermes-vm-view@.service",
    "hermes-vm-ready.service": "/etc/systemd/system/hermes-vm-ready.service",
}


def build_cloud_config(
    public_key: str,
    host_private_key: str,
    host_public_key: str,
    permission_mode: str = "standard",
) -> str:
    """Build cloud-init YAML using VM-only authorized and SSH server keys.

    ``public_key`` authorizes the host's VM-only SSH client; ``host_*`` are the
    guest SSH server keypair, supplied by the host for first-connection pinning.
    Key generation/cryptographic validation belongs to the host provisioner.
    """
    if permission_mode not in ("standard", "unrestricted"):
        raise ValueError("permission_mode must be standard or unrestricted")
    for key in (public_key, host_public_key):
        if not isinstance(key, str) or not key.startswith("ssh-ed25519 ") or any(
            char in key for char in "\r\n\x00"
        ):
            raise ValueError("VM public keys must be single-line Ed25519 keys")
    if not isinstance(host_private_key, str) or not host_private_key.startswith(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    ) or "\x00" in host_private_key:
        raise ValueError("VM SSH host key must use OpenSSH private-key format")

    assets = files(__package__).joinpath("assets")
    written = []
    for name, path in _ASSETS.items():
        written.append({
            "path": path,
            "owner": "root:root",
            "permissions": "0644" if name.endswith(".service") else "0755",
            "content": assets.joinpath(name).read_text(encoding="utf-8").replace(
                "@PERMISSION_MODE@", permission_mode
            ),
        })
    written.extend([
        {
            "path": "/etc/hermes-desktop-vm/cua.json", "owner": "root:root",
            "permissions": "0644",
            "content": json.dumps({"version": CUA_VERSION, "url": CUA_ARCHIVE_URL,
                                   "sha256": CUA_ARCHIVE_SHA256}) + "\n",
        },
        {
            "path": "/etc/systemd/logind.conf.d/hermes-desktop-vm.conf",
            "owner": "root:root", "permissions": "0644",
            "content": "[Login]\nHandlePowerKey=poweroff\nPowerKeyIgnoreInhibited=yes\n",
        },
        {
            "path": "/etc/ssh/sshd_config.d/00-hermes-desktop-vm.conf",
            "owner": "root:root", "permissions": "0644",
            "content": "PasswordAuthentication no\nKbdInteractiveAuthentication no\n"
                       "PermitRootLogin no\nAllowAgentForwarding no\nX11Forwarding no\n",
        },
    ])
    config = {
        "hostname": "hermes-desktop-vm",
        "manage_etc_hosts": True,
        "users": [{
            "name": GUEST_USER, "uid": 1000, "homedir": GUEST_HOME,
            "shell": "/bin/bash", "primary_group": GUEST_USER,
            "groups": ["sudo", "audio", "video"], "lock_passwd": True,
            "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
            "ssh_authorized_keys": [public_key],
        }],
        "disable_root": True,
        "ssh_pwauth": False,
        "ssh_deletekeys": True,
        "ssh_genkeytypes": ["ed25519"],
        "ssh_keys": {"ed25519_private": host_private_key, "ed25519_public": host_public_key},
        "ssh_quiet_keygen": True,
        "no_ssh_fingerprints": True,
        "ssh": {"emit_keys_to_console": False},
        "package_update": True,
        "package_upgrade": False,
        "packages": [
            "acpid", "at-spi2-core", "ca-certificates", "chromium", "curl",
            "dbus", "dbus-x11", "ffmpeg", "fonts-dejavu", "fonts-liberation",
            "libatk-adaptor", "libglib2.0-bin", "libxkbcommon0", "mousepad",
            "novnc", "python3", "python3-websockify", "sudo", "thunar",
            "x11-utils", "x11-xserver-utils", "x11vnc", "xauth", "xfce4-settings",
            "xfwm4", "xterm", "xvfb",
        ],
        "write_files": written,
        # Remove a success marker even after an unclean prior shutdown.
        "bootcmd": [["rm", "-f", READY_PATH]],
        "runcmd": [["/usr/local/lib/hermes-desktop-vm/bootstrap.py"]],
        "final_message": "Managed agent desktop bootstrap finished. Check the ready service.",
    }
    return "#cloud-config\n" + yaml.safe_dump(config, sort_keys=False)
