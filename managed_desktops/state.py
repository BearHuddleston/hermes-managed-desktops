"""Plugin-owned VM storage with explicit, non-portable profile bindings."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

_profile: ContextVar[Path | None] = ContextVar(__name__ + ".profile", default=None)
_store: ContextVar[Path | None] = ContextVar(__name__ + ".store", default=None)


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


class BindingError(VMError):
    """A scoped caller cannot use this VM; no fallback to global authority."""


@contextmanager
def profile_scope(home):
    """None is the explicitly selected operator scope, never an inferred fallback."""
    token = _profile.set(Path(home).expanduser().resolve() if home is not None else None)
    try:
        store_token = _store.set(_select_root())
        try:
            yield
        finally:
            _store.reset(store_token)
    finally:
        _profile.reset(token)


def current_profile():
    return _profile.get()


def require_linux():
    if sys.platform != "linux":
        raise VMError("Managed desktop VMs currently require Linux x86_64 with KVM.")


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise VMError("VM name must start with a lowercase letter and contain only a-z, 0-9, - (max 40).")
    return name


def account_home():
    require_linux()
    import pwd

    # Hermes can intentionally put subprocess HOME inside a profile. The
    # authoritative package must not inherit that directory's lifetime.
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def _select_root():
    base = Path(os.environ.get("XDG_STATE_HOME") or account_home() / ".local" / "state")
    if not base.is_absolute():
        raise VMError("XDG_STATE_HOME must be an absolute directory outside Hermes profiles.")
    return (base / "hermes-managed-desktops").resolve()


def root():
    path = _store.get() or _select_root()
    for home in (account_home() / ".hermes", current_profile(), os.environ.get("HERMES_HOME")):
        if home is not None:
            home = Path(home).expanduser().resolve()
            _outside_profile(path, home)
    return path


def _outside_profile(path, home):
    # A named profile identifies its enclosing Hermes root, even when it is an
    # explicit global bind target rather than the active/ambient profile.
    homes = (home, home.parent.parent) if home.parent.name == "profiles" else (home,)
    for candidate in homes:
        if path.is_relative_to(candidate) or candidate.is_relative_to(path):
            raise VMError("VM storage must be outside the profile directory, with no overlapping roots.")


def profile_identity(home):
    """Bind to an existing directory, not a name or a clonable profile token.

    Birth time distinguishes inode reuse after deletion. Pin the directory while
    GNU/uutils stat reads its birth time through our fd; ctime is unsuitable since
    normal configuration/session writes change it. No Hermes imports or writes.
    """
    require_linux()
    home = Path(home).expanduser().resolve(strict=True)
    _outside_profile(root(), home)
    legacy = home / "managed-resources" / "managed-desktops" / "resources"
    if legacy.is_symlink() or (legacy.is_dir() and any(legacy.iterdir())):
        raise VMError("This profile contains legacy 0.1.0 VM resources. Use that version and its prerequisite checkout to stop/remove them; no automatic migration or adoption.")
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            raise BindingError("Profile directory is not owned by the current user.")
        output = run([
            "stat", "--dereference", "--printf=%d:%i:%w", "--", f"/proc/{os.getpid()}/fd/{fd}",
        ]).stdout
        device, inode, birth = output.split(":", 2)
        if (int(device), int(inode)) != (info.st_dev, info.st_ino) or birth == "-" or not birth:
            raise BindingError("Profile filesystem must provide stable directory birth time; use explicit --global management without a profile binding on unsupported filesystems.")
        current = home.stat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise BindingError("Profile was replaced during binding validation.")
        return {"home": str(home), "device": info.st_dev, "inode": info.st_ino, "birth": birth}
    finally:
        os.close(fd)


def assert_profile():
    home = current_profile()
    if home is None:
        return None
    try:
        return profile_identity(home)
    except (OSError, ValueError) as exc:
        raise BindingError(f"Profile identity is unavailable; no global fallback: {exc}") from exc


def check_access(metadata):
    if current_profile() is not None and metadata.get("binding") != assert_profile():
        raise BindingError("VM is not bound to this profile identity. Use the standalone --global CLI for explicit recovery/rebinding; no inherited or global fallback.")


def binding_status(metadata):
    binding = metadata.get("binding")
    if binding is None:
        return {"status": "unbound"}
    try:
        valid = profile_identity(binding["home"]) == binding
    except (VMError, OSError, ValueError) as exc:
        return {"status": "stale", "home": binding["home"], "reason": str(exc)}
    return {"status": "bound" if valid else "stale", "home": binding["home"]}


def reserve(name):
    # Caller holds the VM lock. An interrupted initial write remains visible and
    # recoverable from the independent CLI, even if its profile no longer exists.
    assert_profile()
    private_dir(root())
    directory = private_dir(root() / "resources")
    path = directory / validate_name(name)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise VMError(f"VM {name!r} already exists; nothing overwritten.") from exc
    return path


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
        stream.flush()
        os.fsync(stream.fileno())


def save(name, state):
    path = vm_dir(name)
    write_private(path / "instance.json.tmp", json.dumps(state, indent=2) + "\n")
    (path / "instance.json.tmp").replace(path / "instance.json")
    # Publish identity and newly reserved directory entries before provisioning.
    for directory in (path, path.parent, path.parent.parent):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


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
        if state["name"] != name or state["schema"] != 2:
            raise ValueError("name/schema mismatch; legacy state is not adopted automatically")
        if state.get("owner_root") != str(root().resolve()):
            raise ValueError("owner store changed; copied VM state cannot control the original guest")
        if not isinstance(state["id"], str) or str(uuid.UUID(state["id"])) != state["id"]:
            raise ValueError("invalid instance identity")
        for key, low, high in (("cpus", 1, 256), ("memory_mib", 1024, 1048576), ("disk_gib", 8, 4096), ("ssh_port", 1024, 65535)):
            value = state[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"invalid {key}")
        if state["network"] not in ("nat", "isolated"):
            raise ValueError("invalid network")
        revision = state.get("binding_revision", 0)
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid binding revision")
        binding = state["binding"]
        if binding is not None and (
            not isinstance(binding, dict) or set(binding) != {"home", "device", "inode", "birth"}
            or not isinstance(binding["home"], str) or not Path(binding["home"]).is_absolute()
            or type(binding["device"]) is not int or type(binding["inode"]) is not int
            or not isinstance(binding["birth"], str) or not binding["birth"]
        ):
            raise ValueError("invalid profile binding")
    except (KeyError, ValueError, TypeError) as exc:
        raise VMError(f"Invalid VM {name!r} metadata: {exc}") from exc
    check_access(state)
    return state


def instance_stamp(state):
    return state["id"], state.get("binding_revision", 0)


def host_env():
    # Neither provider credentials nor SSH agent/config inheritance is needed.
    return {
        "PATH": os.defpath, "HOME": str(Path.home()), "LANG": "C.UTF-8", "TZ": "UTC",
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
