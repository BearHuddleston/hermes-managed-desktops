"""Profile-owned state and verified assets for Linux desktop VMs."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

from hermes_constants import get_hermes_home


# Debian's release directory, not the mutable latest alias. The digest is from
# its HTTPS SHA512SUMS; this pins bytes, not a claim of independent GPG verification.
IMAGE_URL = (
    "https://cloud.debian.org/images/cloud/trixie/20260831-2587/"
    "debian-13-genericcloud-amd64-20260831-2587.qcow2"
)
IMAGE_SHA512 = (
    "8ea9faae810043a0b35b0149f05014f26705c2339ffb11ead308f33e844a87cc3"
    "ef46ec81d5262b38817b6a88af404874d48a5857ebe072ef6a31dfb6e371f50"
)


class VMError(RuntimeError):
    """An actionable refusal or failure in a managed guest, never a host fallback."""


def require_linux():
    if sys.platform != "linux":
        raise VMError("Managed desktop VMs currently require Linux x86_64 with KVM.")


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise VMError("VM name must start with a lowercase letter and contain only a-z, 0-9, - (max 40).")
    return name


def root():
    try:
        from hermes_cli.profile_resources import resource_root
    except ImportError as exc:
        raise VMError(
            "This plugin requires Hermes's profile-resource guard prerequisite. "
            "No VM state was created; see the plugin README."
        ) from exc
    try:
        return resource_root("managed-desktops")
    except ValueError as exc:
        raise VMError(str(exc)) from exc


def reserve(name):
    # Register ownership before downloads, generated credentials or QEMU. The
    # durable directory keeps guarding the profile if preparation is interrupted.
    root()
    from hermes_cli.profile_resources import reserve_resource

    try:
        return reserve_resource("managed-desktops", validate_name(name))
    except FileExistsError as exc:
        raise VMError(f"VM {name!r} already exists; nothing overwritten.") from exc
    except ValueError as exc:
        raise VMError(str(exc)) from exc


def private_dir(path):
    if path.is_symlink():
        raise VMError(f"Refusing symlink state directory: {path}")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise VMError(f"State directory is not owned by the current user: {path}")
    path.chmod(0o700)
    return path


def vm_dir(name):
    directory = root() / "resources"
    if directory.is_symlink():
        raise VMError("Refusing symlink VM resources directory.")
    path = directory / validate_name(name)
    if path.is_symlink():
        raise VMError(f"Refusing symlink VM directory: {path}")
    return path


@contextmanager
def lock(name, *, kind="vm"):
    require_linux()
    import fcntl

    if not get_hermes_home().is_dir():
        raise VMError("Owner profile is missing; refusing to recreate it.")
    private_dir(root())
    path = private_dir(root() / ".locks") / (kind + "-" + validate_name(name) + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def write_private(path, content):
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)


def save(name, state):
    path = vm_dir(name)
    write_private(path / "instance.json.tmp", json.dumps(state, indent=2) + "\n")
    (path / "instance.json.tmp").replace(path / "instance.json")


def load(name):
    path = vm_dir(name) / "instance.json"
    if path.is_symlink():
        raise VMError("Refusing symlink instance metadata.")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VMError(f"VM {name!r} not found in {root()}.") from exc
    except (ValueError, OSError) as exc:
        raise VMError(f"Cannot read VM {name!r} metadata: {exc}") from exc
    import uuid

    try:
        if state["name"] != name or state["schema"] != 1:
            raise ValueError("name/schema mismatch")
        if state.get("owner_root") != str(root().resolve()):
            raise ValueError("owner profile changed; copied VM state cannot control the original guest")
        if str(uuid.UUID(state["id"])) != state["id"]:
            raise ValueError("invalid instance identity")
        for key, low, high in (("cpus", 1, 256), ("memory_mib", 1024, 1048576), ("disk_gib", 8, 4096), ("ssh_port", 1024, 65535)):
            value = state[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"invalid {key}")
        if state["network"] not in ("nat", "isolated"):
            raise ValueError("invalid network")
    except (KeyError, ValueError, TypeError) as exc:
        raise VMError(f"Invalid VM {name!r} metadata: {exc}") from exc
    return state


def host_env():
    # Neither provider credentials nor SSH agent/config inheritance is needed.
    return {
        "PATH": os.defpath, "HOME": str(Path.home()), "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
    }


def run(argv, *, timeout=30, check=True, input=None):
    try:
        result = subprocess.run(
            [str(a) for a in argv], input=input, capture_output=True,
            text=True, timeout=timeout, env=host_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VMError(f"{Path(str(argv[0])).name}: {exc}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise VMError(f"{Path(str(argv[0])).name} failed ({result.returncode}): {detail}")
    return result


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha512").hexdigest()


def download_image():
    with lock("image-cache", kind="asset"):
        path = private_dir(root() / ".images") / "debian-13-20260831-2587.qcow2"
        if path.is_symlink():
            raise VMError("Refusing symlink image cache.")
        if path.exists():
            if file_digest(path) != IMAGE_SHA512:
                raise VMError(f"Cached image checksum mismatch: {path}. Remove the corrupt cache explicitly.")
            return path
        partial = path.with_suffix(".partial")
        try:
            # Never publish a partial or unverified image for a second creator.
            fd = os.open(partial, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream, urllib.request.urlopen(IMAGE_URL, timeout=60) as response:
                if not response.url.startswith("https://"):
                    raise VMError("Image download redirected away from HTTPS.")
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
            if file_digest(partial) != IMAGE_SHA512:
                raise VMError("Downloaded Debian image checksum mismatch; image was not used.")
            partial.replace(path)
        except (OSError, ValueError) as exc:
            raise VMError(f"Debian image download failed: {exc}") from exc
        finally:
            partial.unlink(missing_ok=True)
        return path
