"""QEMU/KVM lifecycle. Only profile-owned systemd units are controlled."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import socket
import time
import uuid

from .config import settings
from .state import (
    IMAGE_SHA512, IMAGE_URL, VMError, download_image, load, lock, private_dir,
    require_linux, reserve, root, run, save, validate_name, vm_dir, write_private,
)


NETWORK_WARNING = "NAT permits internet and potential host/LAN access; it is not network isolation."


def firmware(config):
    code = Path(config.get("ovmf_code") or "/usr/share/OVMF/OVMF_CODE_4M.fd")
    variables = Path(config.get("ovmf_vars") or "/usr/share/OVMF/OVMF_VARS_4M.fd")
    return code, variables


def preflight():
    require_linux()
    config = settings()
    binaries = ["qemu-system-x86_64", "qemu-img", "cloud-localds", "ssh", "ssh-keygen", "scp", "systemd-run", "systemctl"]
    checks = {name: bool(shutil.which(name, path=os.defpath)) for name in binaries}
    checks["x86_64"] = platform.machine() in ("x86_64", "amd64")
    checks["kvm"] = os.access("/dev/kvm", os.R_OK | os.W_OK)
    code, variables = firmware(config)
    checks["uefi_code"] = code.is_file()
    checks["uefi_vars"] = variables.is_file()
    checks["user_systemd"] = Path(f"/run/user/{os.getuid()}/bus").exists()
    if checks["systemctl"] and checks["user_systemd"]:
        checks["user_systemd"] = run(["systemctl", "--user", "list-units", "--no-pager", "--no-legend"], check=False).returncode == 0
    return {
        "ready": all(checks.values()), "checks": checks, "state_root": str(root()),
        "network": NETWORK_WARNING,
        "hint": "On Debian/Ubuntu install qemu-system-x86 qemu-utils ovmf cloud-image-utils openssh-client; enable KVM access and a user systemd session. No packages are installed on the host automatically.",
    }


def unit(state):
    return f"hermes-desktop-vm-{state['id']}.service"


def runtime_dir(state):
    return Path(f"/run/user/{os.getuid()}/hermes-desktop-vms") / state["id"]


def unit_active(state):
    result = run(["systemctl", "--user", "show", unit(state), "--property=ActiveState", "--value"], check=False)
    if result.returncode:
        raise VMError("Cannot query the user systemd manager; refusing to infer the VM is stopped.")
    return result.stdout.strip() in ("active", "activating", "deactivating", "reloading")


def qmp(state, command="query-status"):
    address = runtime_dir(state) / "qmp"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(address))
            with sock.makefile("rwb") as stream:
                def receive():
                    while True:
                        line = stream.readline(1024 * 1024)
                        if not line:
                            raise VMError("QMP disconnected.")
                        message = json.loads(line)
                        if "event" not in message:
                            return message

                def execute(name):
                    stream.write(json.dumps({"execute": name}).encode() + b"\n")
                    stream.flush()
                    response = receive()
                    if "error" in response:
                        raise VMError(f"QMP {name} refused: {response['error']}")
                    return response.get("return")

                if "QMP" not in receive():
                    raise VMError("Not a QMP endpoint.")
                execute("qmp_capabilities")
                if execute("query-uuid") != {"UUID": state["id"]}:
                    raise VMError("QMP VM identity mismatch; refusing this target.")
                return execute(command)
    except (OSError, ValueError) as exc:
        raise VMError(f"VM {state['name']!r} QMP target unavailable: {exc}") from exc


def require_running(state):
    if not unit_active(state):
        raise VMError(f"VM {state['name']!r} is stopped. Start it explicitly; no host fallback.")
    if not qmp(state).get("running"):
        raise VMError(f"VM {state['name']!r} is not running.")


def status(name):
    state = load(name)
    active = unit_active(state)
    result = {**state, "unit": unit(state), "active": active, "state_directory": str(vm_dir(name))}
    if active:
        try:
            result["qemu"] = qmp(state)
        except VMError as exc:
            result["error"] = str(exc)
    if state["network"] == "nat":
        result["warning"] = NETWORK_WARNING
    return result


def list_vms():
    directory = root() / "resources"
    if directory.is_symlink():
        raise VMError("Refusing symlink VM resources directory.")
    if not directory.exists():
        return []
    entries = []
    for path in sorted(directory.iterdir()):
        if not path.is_dir():
            continue
        with lock(path.name):
            try:
                entries.append(status(path.name))
            except VMError as exc:
                entries.append({
                    "name": path.name, "phase": "incomplete", "active": None,
                    "error": str(exc), "removable_unprovisioned": _unprovisioned(path),
                })
    return entries


def _unprovisioned(path):
    # Before the first successful metadata publication, no keys, disk, or
    # process can exist. Never infer that an unknown disk/identity is stopped.
    return path.is_dir() and not path.is_symlink() and all(
        child.name == "instance.json.tmp" and child.is_file() and not child.is_symlink()
        for child in path.iterdir()
    )


def _qemu_path(path):
    return str(path).replace(",", ",,")


def qemu_argv(state):
    path = vm_dir(state["name"])
    runtime = runtime_dir(state)
    restricted = "on" if state["network"] == "isolated" else "off"
    argv = [
        "qemu-system-x86_64", "-name", f"hermes-{state['name']}", "-uuid", state["id"],
        "-machine", "q35,accel=kvm", "-cpu", "host", "-smp", str(state["cpus"]),
        "-m", str(state["memory_mib"]), "-display", "none", "-nodefaults",
        "-drive", f"if=pflash,format=raw,readonly=on,file={_qemu_path(path / 'uefi-code.fd')}",
        "-drive", f"if=pflash,format=raw,file={_qemu_path(path / 'uefi-vars.fd')}",
        "-drive", f"file={_qemu_path(path / 'disk.qcow2')},if=virtio,format=qcow2",
        "-device", "virtio-rng-pci",
        "-netdev", f"user,id=net0,restrict={restricted},ipv6=off,hostfwd=tcp:127.0.0.1:{state['ssh_port']}-:22",
        "-device", "virtio-net-pci,netdev=net0",
        "-serial", f"file:{path / 'serial.log'}",
        "-qmp", f"unix:{_qemu_path(runtime / 'qmp')},server=on,wait=off",
    ]
    if (path / "seed.iso").exists():
        argv += ["-drive", f"file={_qemu_path(path / 'seed.iso')},media=cdrom,readonly=on,format=raw"]
    return argv


def _start(state):
    if unit_active(state):
        require_running(state)
        return
    private_dir(runtime_dir(state).parent)
    private_dir(runtime_dir(state))
    (runtime_dir(state) / "qmp").unlink(missing_ok=True)
    run([
        "systemd-run", "--user", f"--unit={unit(state)}", "--collect", "--quiet",
        "--property=Type=exec", "--property=UMask=0077", "--property=TimeoutStopSec=30",
        "--", "/usr/bin/env", "-i", "PATH=" + os.defpath,
        "HOME=" + str(vm_dir(state["name"])), *qemu_argv(state),
    ])
    deadline = time.monotonic() + 15
    last_error = "QEMU did not start"
    while time.monotonic() < deadline:
        try:
            require_running(state)
            return
        except VMError as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise VMError(last_error + f"; inspect journalctl --user -u {unit(state)}")


def start(name, network=None):
    with lock(name):
        state = load(name)
        if network is not None and network != state["network"]:
            if unit_active(state):
                raise VMError("Stop the VM before changing its network policy.")
            if network not in ("nat", "isolated"):
                raise VMError("Network must be nat or isolated.")
            state["network"] = network
            save(name, state)
        report = preflight()
        if not report["ready"]:
            raise VMError("Preflight failed: " + json.dumps(report))
        _start(state)
    return status(name)


def create(name, *, cpus=None, memory_mib=None, disk_gib=None, network, permission_mode="standard"):
    validate_name(name)
    if network != "nat":
        raise VMError("Fresh provisioning requires explicit --network nat for Debian packages and Cua. Use start --network isolated after provisioning.")
    if permission_mode not in ("standard", "unrestricted"):
        raise VMError("Unsupported guest permission mode.")
    report = preflight()
    if not report["ready"]:
        raise VMError("Preflight failed: " + json.dumps(report))
    config = settings()
    state = {
        "schema": 1, "id": str(uuid.uuid4()), "name": name,
        "owner_root": str(root().resolve()),
        "cpus": cpus if cpus is not None else config.get("cpus", 4),
        "memory_mib": memory_mib if memory_mib is not None else config.get("memory_mib", 4096),
        "disk_gib": disk_gib if disk_gib is not None else config.get("disk_gib", 32),
        "network": network, "permission_mode": permission_mode, "phase": "preparing",
        "image_url": IMAGE_URL, "image_sha512": IMAGE_SHA512,
    }
    for key, low, high in (("cpus", 1, 256), ("memory_mib", 1024, 1048576), ("disk_gib", 8, 4096)):
        if type(state[key]) is not int or not low <= state[key] <= high:
            raise VMError(f"{key} must be an integer between {low} and {high}.")
    with lock(name):
        # Serialize even the empty reservation with removal/recovery.
        path = reserve(name)
        with socket.socket() as port:
            port.bind(("127.0.0.1", 0))
            state["ssh_port"] = port.getsockname()[1]
        save(name, state)
        try:
            image = download_image()
            secrets = private_dir(path / "secrets")
            for key in ("identity", "host-key"):
                run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "hermes-managed-guest", "-f", secrets / key])
            public = (secrets / "identity.pub").read_text().strip()
            host_public = (secrets / "host-key.pub").read_text().strip()
            # Cloud-init receives ONLY newly generated guest keys, not the user's SSH files.
            from .guest import build_cloud_config

            user_data = build_cloud_config(public, (secrets / "host-key").read_text(), host_public, permission_mode=permission_mode)
            write_private(secrets / "user-data", user_data)
            write_private(secrets / "meta-data", f"instance-id: {state['id']}\nlocal-hostname: hermes-{name}\n")
            write_private(secrets / "known_hosts", f"hermes-vm-{state['id']} {host_public}\n")
            run(["cloud-localds", path / "seed.iso", secrets / "user-data", secrets / "meta-data"])
            (path / "seed.iso").chmod(0o600)
            # The seed contains the host key; source plaintext is not needed after image creation.
            for key in ("user-data", "meta-data", "host-key", "host-key.pub"):
                (secrets / key).unlink()
            code, variables = firmware(config)
            shutil.copyfile(code, path / "uefi-code.fd")
            shutil.copyfile(variables, path / "uefi-vars.fd")
            run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", image, path / "disk.qcow2", f"{state['disk_gib']}G"])
            state["phase"] = "provisioning"
            save(name, state)
            _start(state)
        except (VMError, OSError) as exc:
            state["phase"] = "failed"
            save(name, state)
            raise VMError(f"VM {name!r} preparation failed; state retained for diagnostics/removal: {exc}") from exc
    return status(name)


def stop(name, timeout=120):
    with lock(name):
        state = load(name)
        if not unit_active(state):
            return status(name)
        require_running(state)
        qmp(state, "system_powerdown")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not unit_active(state):
                return status(name)
            time.sleep(1)
        raise VMError(f"Orderly shutdown timed out for {name!r}; VM was NOT force-killed. Inspect desktop-vm doctor {name}.")


def remove(name, confirm):
    if confirm != name:
        raise VMError("Removal requires --confirm with the exact VM name.")
    with lock(name):
        path = vm_dir(name)
        if _unprovisioned(path):
            shutil.rmtree(path)
            return {"removed": name, "unprovisioned": True, "image_cache_retained": True}
        state = load(name)
        if unit_active(state):
            raise VMError("Stop the VM before removing its disk, keys, and artifacts.")
        shutil.rmtree(vm_dir(name))
        runtime = runtime_dir(state)
        if runtime.exists():
            shutil.rmtree(runtime)
    return {"removed": name, "image_cache_retained": True}
