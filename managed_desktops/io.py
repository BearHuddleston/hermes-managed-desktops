"""Explicit guest SSH/Cua, file transfer, readiness and local viewer."""

from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import shlex
import socket
import subprocess
import tempfile
import time

from .config import settings
from .vm import preflight, require_running, status
from .state import VMError, host_env, load, lock, run, save, vm_dir


GUEST_INSTALLED = "/var/lib/hermes-desktop-vm/installed"


def screen_number(screen):
    if type(screen) is not int or screen not in (1, 2):
        raise VMError("Choose an explicit guest screen: 1 or 2.")
    return str(screen)


def ssh_options(state):
    secrets = vm_dir(state["name"]) / "secrets"
    for name in ("identity", "known_hosts"):
        path = secrets / name
        if not path.is_file() or path.is_symlink():
            raise VMError(f"Guest SSH {name} is missing or unsafe; refusing fallback.")
    # -o values are parsed as ssh_config (not argv): quote whitespace and escape
    # token expansion so an unusual profile path cannot become several trust files.
    known_hosts = str(secrets / "known_hosts").replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return [
        "-F", "/dev/null", "-i", str(secrets / "identity").replace("%", "%%"),
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
        "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "PermitLocalCommand=no",
        "-o", "ProxyCommand=none", "-o", "StrictHostKeyChecking=yes",
        "-o", "GlobalKnownHostsFile=/dev/null", "-o", f'UserKnownHostsFile="{known_hosts}"',
        "-o", f"HostKeyAlias=hermes-vm-{state['id']}", "-o", "ConnectTimeout=5",
        "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
        "-o", "ControlMaster=no", "-o", "ControlPath=none",
    ]


def ssh(state, argv, *, timeout=30, check=True):
    require_running(state)
    if not argv:
        raise VMError("A guest command is required.")
    return run([
        "ssh", *ssh_options(state), "-o", "ClearAllForwardings=yes", "-T",
        "-p", str(state["ssh_port"]), "agent@127.0.0.1", shlex.join(str(a) for a in argv),
    ], timeout=timeout, check=check)


@contextmanager
def _guest_operation(name):
    with lock(name):
        state = load(name)
    # SSH/SCP may block until their timeout. Keep lifecycle control available,
    # but never report success for a different instance that reused this name.
    yield state
    with lock(name):
        if load(name)["id"] != state["id"]:
            raise VMError("VM was replaced during guest I/O; refusing the new target.")


def execute(name, argv, screen=None, timeout=60):
    with _guest_operation(name) as state:
        if screen is not None:
            argv = ["/usr/local/bin/hermes-vm-screen", screen_number(screen), *argv]
        return ssh(state, argv, timeout=timeout)


def cua(name, screen, verb, arguments):
    if verb not in ("call", "describe", "tools"):
        raise VMError("Cua verb must be call, describe, or tools.")
    return execute(name, ["/usr/local/bin/hermes-vm-cua", screen_number(screen), verb, *arguments], timeout=90)


def record(name, screen, action, recording=None):
    if action not in ("start", "stop", "status"):
        raise VMError("Recording action must be start, stop, or status.")
    argv = ["/usr/local/bin/hermes-vm-record", screen_number(screen), action]
    if action == "start":
        from .state import validate_name

        argv.append(validate_name(recording))
    return execute(name, argv, timeout=90)


def capture(name, screen, output=None, pid=None, window_id=None):
    import uuid
    from .state import private_dir, write_private

    screen_number(screen)
    if (pid is None) != (window_id is None):
        raise VMError("Window capture requires both --pid and --window-id.")
    filename = f"screen-{screen}-{uuid.uuid4()}.png"
    remote = "/home/agent/workspace/" + filename
    session = f"hermes-vm-screen-{screen}"
    cua(name, screen, "call", ["start_session", json.dumps({"session": session})])
    data: dict = {"session": session, "screenshot_out_file": remote}
    tool = "get_desktop_state"
    if pid is not None:
        data.update(pid=pid, window_id=window_id, max_elements=500)
        tool = "get_window_state"
    result = cua(name, screen, "call", [tool, json.dumps(data)])
    snapshot = json.loads(result.stdout)
    artifacts = private_dir(vm_dir(name) / "artifacts")
    receipt = artifacts / (filename + ".json")
    write_private(receipt, json.dumps(snapshot, indent=2))
    destination = str(output or (artifacts / filename))
    transfer(name, remote, destination, upload=False)
    return {"name": name, "screen": screen, "image": destination, "snapshot": str(receipt), "session": session}


def readiness(state):
    units = [f"hermes-vm-{kind}@{screen}.service" for screen in (1, 2) for kind in ("desktop", "cua", "view")]
    # The oneshot ready service is stopped when a dependency stops and does not
    # re-arm itself. Installation is durable; health must be recomputed live.
    result = ssh(state, ["test", "-f", GUEST_INSTALLED], check=False)
    if result.returncode:
        return {"ready": False, "reason": "Guest provisioning has not completed; inspect doctor."}
    cloud = ssh(state, ["cloud-init", "status", "--format", "json"], check=False)
    try:
        clean_boot = cloud.returncode == 0 and json.loads(cloud.stdout).get("status") == "done"
    except (ValueError, AttributeError):
        clean_boot = False
    if not clean_boot:
        return {"ready": False, "reason": "Cloud-init has not finished cleanly; inspect doctor."}
    result = ssh(state, ["systemctl", "is-active", *units], check=False)
    if result.returncode:
        return {"ready": False, "reason": "Guest desktop/Cua/view services not active", "services": result.stdout.strip()}
    for screen in (1, 2):
        probe = ssh(state, ["/usr/local/bin/hermes-vm-cua", str(screen), "call", "list_windows", "{}"], check=False)
        if probe.returncode:
            return {"ready": False, "reason": f"Screen {screen} Cua not ready", "detail": probe.stderr.strip()}
        probe = ssh(state, [
            "/usr/bin/python3", "-c",
            "import runpy, sys; runpy.run_path('/usr/local/lib/hermes-desktop-vm/ready.py')['view_ready'](int(sys.argv[1]))",
            str(screen),
        ], check=False, timeout=10)
        if probe.returncode:
            return {"ready": False, "reason": f"Screen {screen} viewer not ready", "detail": probe.stderr.strip()}
    return {"ready": True, "screens": [1, 2]}


def wait(name, timeout=None):
    timeout = timeout if timeout is not None else settings().get("ready_timeout", 1200)
    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 3600:
        raise VMError("Readiness timeout must be between 1 and 3600 seconds.")
    state = load(name)
    deadline = time.monotonic() + timeout
    result = {"ready": False, "reason": "SSH not ready"}
    while time.monotonic() < deadline:
        # Readiness polling must not prevent an operator from stopping a broken
        # bootstrap. Identity remains fixed even if the name is removed/recreated.
        require_running(state)
        try:
            result = readiness(state)
        except VMError as exc:
            result = {"ready": False, "reason": str(exc)}
        if result["ready"]:
            with lock(name):
                current = load(name)
                if current["id"] != state["id"]:
                    raise VMError("VM was replaced while waiting; refusing the new target.")
                require_running(current)
                state = current
                if state["phase"] != "ready":
                    state["phase"] = "ready"
                    save(name, state)
            return {"name": name, **result}
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise VMError(f"VM {name!r} readiness timed out: {result}. Run desktop-vm doctor {name}; state retained.")


def doctor(name=None):
    result: dict = {"host": preflight()}
    if name is None:
        return result
    result["vm"] = status(name)
    state = load(name)
    if not result["vm"]["active"]:
        result["ready"] = False
        return result
    try:
        result.update(readiness(state))
        probes = {
            "cloud_init": ["cloud-init", "status", "--format", "json"],
            "failed_units": ["systemctl", "--failed", "--no-pager", "--no-legend"],
            "disk": ["df", "-h", "/home/agent/workspace"],
        }
        result["guest"] = {key: ssh(state, command, check=False).stdout.strip() for key, command in probes.items()}
    except VMError as exc:
        result.update(ready=False, error=str(exc))
    return result


def guest_path(value):
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or any(c in value for c in "\n\r\x00*?[]"):
        raise VMError("Specify a literal absolute guest file path, without traversal or glob characters.")
    return str(path)


def transfer(name, source, destination, *, upload):
    with _guest_operation(name) as state:
        require_running(state)
        argv = ["scp", *ssh_options(state), "-o", "ClearAllForwardings=yes", "-B", "-P", str(state["ssh_port"])]
        if upload:
            local = Path(source).expanduser().resolve()
            if not local.is_file():
                raise VMError("Upload source must be one existing local file (no recursive/home mounts).")
            remote = guest_path(destination)
            # Stage in the same guest directory, then atomically publish without
            # replacing files or following a destination symlink (even a dangling one).
            template = str(PurePosixPath(remote).parent / ".hermes-upload-XXXXXXXX")
            temporary = guest_path(ssh(state, ["mktemp", template]).stdout.strip())
            try:
                run([*argv, "--", local, f"agent@127.0.0.1:{temporary}"], timeout=300)
                with local.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                actual = ssh(state, ["sha256sum", "--", temporary]).stdout.split()[0]
                if actual != digest:
                    raise VMError("Uploaded file checksum mismatch; destination not published.")
                ssh(state, ["ln", "-T", "--", temporary, remote])
                if ssh(state, ["sha256sum", "--", remote]).stdout.split()[0] != digest:
                    raise VMError("Guest destination changed after upload.")
            finally:
                ssh(state, ["rm", "-f", "--", temporary], check=False)
            return {"uploaded": str(local), "guest": remote, "sha256": digest}
        remote = guest_path(source)
        local = Path(destination).expanduser().absolute()
        if local.exists() or local.is_symlink():
            raise VMError("Local destination already exists; download will not overwrite it.")
        if not local.parent.is_dir():
            raise VMError("Create the local destination directory explicitly before downloading.")
        fd, temporary = tempfile.mkstemp(prefix=".hermes-vm-download-", dir=local.parent)
        os.close(fd)
        try:
            run([*argv, "--", f"agent@127.0.0.1:{remote}", temporary], timeout=300)
            with open(temporary, "rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if ssh(state, ["sha256sum", "--", remote]).stdout.split()[0] != digest:
                raise VMError("Downloaded file checksum mismatch; destination not published.")
            # Atomic no-clobber publication even if another caller created the target.
            os.link(temporary, local)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"downloaded": remote, "local": str(local), "sha256": digest}


def view(name, screen, port=0):
    import psutil
    import urllib.request

    screen_number(screen)
    state = load(name)
    require_running(state)
    if type(port) is not int or not (port == 0 or 1024 <= port <= 65535):
        raise VMError("Viewer port must be 0 (auto) or 1024..65535.")
    if not port:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
    # Foreground SSH owns the viewer. No detached, forgotten, unauthenticated proxy.
    argv = [
        "ssh", *ssh_options(state), "-T", "-N", "-o", "ExitOnForwardFailure=yes",
        "-L", f"127.0.0.1:{port}:127.0.0.1:{6080 + screen}",
        "-p", str(state["ssh_port"]), "agent@127.0.0.1",
    ]
    process = subprocess.Popen(argv, env=host_env(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert process.stderr is not None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise VMError("Viewer SSH tunnel failed: " + process.stderr.read().decode(errors="replace"))
            try:
                listeners = psutil.Process(process.pid).net_connections(kind="tcp")
            except psutil.NoSuchProcess:
                listeners = []
            if any(c.status == psutil.CONN_LISTEN and c.laddr.ip == "127.0.0.1" and c.laddr.port == port for c in listeners):
                break
            time.sleep(0.1)
        else:
            raise VMError("Viewer SSH tunnel did not become ready.")
        url = f"http://127.0.0.1:{port}/vnc.html?autoconnect=1&resize=scale"
        # A local listener is not evidence that the guest HTTP backend is ready.
        # Bypass proxy environment variables for this strictly loopback request.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(url, timeout=5) as response:
                if response.status != 200:
                    raise VMError("Guest viewer did not return HTTP 200.")
        except OSError as exc:
            raise VMError(f"Guest viewer is not ready: {exc}; run desktop-vm doctor {name}.") from exc
        print(json.dumps({"name": name, "screen": screen, "url": url, "note": "Local host users can reach this viewer. Ctrl-C closes the tunnel."}), flush=True)
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stderr.close()
