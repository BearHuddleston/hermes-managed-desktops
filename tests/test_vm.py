"""Safety and persistence contracts at the real filesystem/QMP boundaries."""

import io

import json
from pathlib import Path
import shlex
import socket
import shutil
import subprocess
import threading
import sys
import tarfile
import uuid
from unittest.mock import Mock

import pytest

from managed_desktops import vm, io as guest, state

pytestmark = pytest.mark.linux_only


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-a"))
    (tmp_path / "profile-a").mkdir()
    state.reserve("demo")
    data = {
        "schema": 2, "id": str(uuid.uuid4()), "name": "demo", "cpus": 2,
        "owner_root": str(state.root().resolve()),
        "binding": None,
        "memory_mib": 2048, "disk_gib": 16, "ssh_port": 22888,
        "network": "isolated", "phase": "ready",
    }
    state.save("demo", data)
    return data


def test_vm_paths_cannot_escape_or_follow_symlinks(instance):
    assert state.load("demo") == instance
    original = state.vm_dir("demo")
    for name in ("../demo", "/tmp/demo", "Demo", "a/b", "a\n", "-x"):
        with pytest.raises(state.VMError):
            state.vm_dir(name)
    moved = original.with_name("moved")
    original.rename(moved)
    original.symlink_to(moved)
    with pytest.raises(state.VMError, match="symlink"):
        state.load("demo")


def test_network_argv_and_ssh_identity_never_inherit_host(instance):
    secrets = state.private_dir(state.vm_dir("demo") / "secrets")
    # Inert placeholders: this test builds argv, it does not authenticate.
    (secrets / "identity").touch()
    (secrets / "known_hosts").touch()
    options = guest.ssh_options(instance)
    assert options[options.index("-F") + 1] == "/dev/null"
    for contract in ("IdentityAgent=none", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes", "ForwardAgent=no", "ForwardX11=no", "ControlPath=none"):
        assert contract in options
    argv = vm.qemu_argv(instance)
    network = argv[argv.index("-netdev") + 1]
    assert "restrict=on" in network
    assert f"hostfwd=tcp:127.0.0.1:{instance['ssh_port']}-:22" in network
    instance["network"] = "nat"
    assert "restrict=off" in vm.qemu_argv(instance)[argv.index("-netdev") + 1]
    assert "-virtfs" not in argv and "-fsdev" not in argv


def test_bad_image_is_not_published_or_reused(monkeypatch):
    class Response(io.BytesIO):
        url = state.IMAGE_URL

    monkeypatch.setattr(state.urllib.request, "urlopen", lambda *a, **k: Response(b"not a Debian image"))
    with pytest.raises(state.VMError, match="checksum mismatch"):
        state.download_image()
    assert not tuple((state.root() / ".images").iterdir())


def test_qmp_mismatched_identity_refuses_action(instance, monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(vm, "runtime_dir", lambda _: runtime)
    commands = []
    errors = []
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(runtime / "qmp"))
        server.listen()
        server.settimeout(5)

        def serve():
            try:
                conn, _ = server.accept()
                with conn, conn.makefile("rwb") as stream:
                    stream.write(b'{"QMP": {}}\n')
                    stream.flush()
                    for response in ({}, {"UUID": str(uuid.uuid4())}):
                        commands.append(json.loads(stream.readline())["execute"])
                        stream.write(json.dumps({"return": response}).encode() + b"\n")
                        stream.flush()
                    assert stream.readline() == b""
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=serve)
        worker.start()
        with pytest.raises(state.VMError, match="identity mismatch"):
            vm.qmp(instance, "system_powerdown")
        worker.join(timeout=5)
    assert not worker.is_alive() and not errors
    assert commands == ["qmp_capabilities", "query-uuid"]


def test_stopped_target_never_invokes_ssh_or_host_cua(instance, monkeypatch):
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    subprocess_run = Mock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(guest, "run", subprocess_run)
    with pytest.raises(state.VMError, match="no host fallback"):
        guest.cua("demo", 1, "call", ["list_windows", "{}"])
    subprocess_run.assert_not_called()
    with pytest.raises(state.VMError, match="explicit guest screen"):
        guest.cua("demo", None, "call", ["list_windows", "{}"])


def test_remove_refuses_running_and_wrong_confirmation(instance, monkeypatch):
    disk = state.vm_dir("demo") / "disk.qcow2"
    disk.write_bytes(b"persistent workspace")
    monkeypatch.setattr(vm, "unit_active", lambda _: True)
    with pytest.raises(state.VMError, match="exact VM name"):
        vm.remove("demo", "other")
    with pytest.raises(state.VMError, match="Stop the VM"):
        vm.remove("demo", "demo")
    assert disk.read_bytes() == b"persistent workspace"
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    monkeypatch.setattr(vm, "runtime_dir", lambda _: disk.parent / "runtime")
    assert vm.remove("demo", "demo")["removed"] == "demo"
    assert not disk.parent.exists()


def test_upload_does_not_clobber_files_symlinks_or_directories(instance, monkeypatch, tmp_path):
    monkeypatch.setattr(guest, "require_running", lambda _: None)
    monkeypatch.setattr(guest, "ssh_options", lambda _: [])
    monkeypatch.setattr(guest, "ssh", lambda _state, argv, **kwargs: state.run(argv, **kwargs))

    def scp(argv, **kwargs):
        shutil.copyfile(argv[-2], argv[-1].split(":", 1)[1])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(guest, "run", scp)
    source = tmp_path / "source"
    source.write_text("new content")
    target = tmp_path / "target"
    target.write_text("keep me")
    link = tmp_path / "dangling"
    link.symlink_to(tmp_path / "absent")
    directory = tmp_path / "directory"
    directory.mkdir()
    for existing in (target, link, directory):
        with pytest.raises(state.VMError):
            guest.transfer("demo", str(source), str(existing), upload=True)
    assert target.read_text() == "keep me"
    assert link.is_symlink() and not link.exists()
    assert not tuple(directory.iterdir())
    destination = tmp_path / "new-target"
    receipt = guest.transfer("demo", str(source), str(destination), upload=True)
    assert destination.read_bytes() == source.read_bytes()
    assert receipt["guest"] == str(destination)
    assert not tuple(tmp_path.glob(".hermes-upload-*"))


def test_copied_store_cannot_reuse_original_vm_identity(instance, monkeypatch, tmp_path):
    source = state.root()
    new_base = tmp_path / "copied-store"
    shutil.copytree(source, new_base / source.name)
    monkeypatch.setenv("XDG_STATE_HOME", str(new_base))
    with pytest.raises(state.VMError, match="owner"):
        state.load("demo")


def test_readiness_does_not_hold_the_lifecycle_lock(instance, monkeypatch):
    import fcntl

    monkeypatch.setattr(guest, "require_running", lambda data: None)

    def ready(data):
        directory = state.private_dir(state.root() / ".locks")
        with (directory / "vm-demo.lock").open("a") as handle:
            # A second CLI must be able to stop an indefinitely booting guest.
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return {"ready": True}

    monkeypatch.setattr(guest, "readiness", ready)
    assert guest.wait("demo", timeout=1)["ready"]


@pytest.mark.parametrize("operation", ["execute", "upload", "download"])
def test_guest_transport_leaves_lifecycle_control_available(instance, monkeypatch, tmp_path, operation):
    import fcntl

    monkeypatch.setattr(guest, "require_running", lambda data: None)
    monkeypatch.setattr(guest, "ssh_options", lambda data: [])
    transport_calls = []

    def transport(argv, **kwargs):
        directory = state.private_dir(state.root() / ".locks")
        with (directory / "vm-demo.lock").open("a") as handle:
            # This is the real lock taken by another CLI's stop command.
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        transport_calls.append(argv[0])
        if argv[0] == "scp":
            source, destination = [str(path).removeprefix("agent@127.0.0.1:") for path in argv[-2:]]
            shutil.copyfile(source, destination)
            return subprocess.CompletedProcess(argv, 0, "", "")
        # Exercise the real SSH argv construction, but run only the test's
        # local filesystem commands instead of connecting to any guest.
        return state.run(shlex.split(argv[-1]), **kwargs)

    monkeypatch.setattr(guest, "run", transport)
    source = tmp_path / "source"
    source.write_text("transport fixture", encoding="utf-8")
    destination = tmp_path / "destination"
    if operation == "execute":
        assert guest.execute("demo", ["printf", "%s", "executed"]).stdout == "executed"
    else:
        guest.transfer("demo", str(source), str(destination), upload=operation == "upload")
        assert destination.read_bytes() == source.read_bytes()
        assert "scp" in transport_calls
    assert "ssh" in transport_calls


def test_guest_command_rejects_replaced_instance_after_transport(instance, monkeypatch):
    monkeypatch.setattr(guest, "require_running", lambda data: None)
    monkeypatch.setattr(guest, "ssh_options", lambda data: [])

    def transport(argv, **kwargs):
        # The original command finishes after its VM name has been reused.
        state.save("demo", {**instance, "id": str(uuid.uuid4())})
        return subprocess.CompletedProcess(argv, 0, "old guest result", "")

    monkeypatch.setattr(guest, "run", transport)
    with pytest.raises(state.VMError, match="replaced"):
        guest.execute("demo", ["true"])


def test_viewer_never_reports_an_unrelated_listener(instance, monkeypatch, capsys):
    secrets = state.private_dir(state.vm_dir("demo") / "secrets")
    for name in ("identity", "known_hosts"):
        state.write_private(secrets / name, "fixture")
    monkeypatch.setattr(guest, "require_running", lambda data: None)
    monkeypatch.setattr(guest, "ssh", lambda *a, **kw: subprocess.CompletedProcess([], 0))
    real_popen = subprocess.Popen
    monkeypatch.setattr(guest.subprocess, "Popen", lambda *a, **kw: real_popen(
        [sys.executable, "-c", "import time; time.sleep(0.2); raise SystemExit(1)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    ))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(state.VMError, match="tunnel failed"):
            guest.view("demo", 1, listener.getsockname()[1])
    assert not capsys.readouterr().out


def test_ready_marker_does_not_hide_cloud_init_errors(instance, monkeypatch):
    def probe(data, argv, **kwargs):
        if argv[0] == "test":
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv == ["cloud-init", "status", "--format", "json"]
        return subprocess.CompletedProcess(argv, 2, '{"status":"done","extended_status":"degraded done"}', "")

    monkeypatch.setattr(guest, "ssh", probe)
    result = guest.readiness(instance)
    assert not result["ready"] and "Cloud-init" in result["reason"]


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_profile_mutation_preserves_external_guest_disks(monkeypatch, tmp_path, operation):
    from hermes_cli import profiles

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profile = profiles.get_profile_dir("vm-owner")
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    disk = state.reserve("guest") / "disk.qcow2"
    disk.write_bytes(b"disk fixture")
    # No real services belong to this disposable profile.
    monkeypatch.setattr(profiles, "_check_gateway_running", lambda *_: False)
    monkeypatch.setattr(profiles, "_cleanup_gateway_service", lambda *_: None)
    monkeypatch.setattr(profiles, "_stop_profile_backends", lambda *_: None)
    if operation == "delete":
        profiles.delete_profile("vm-owner", yes=True)
    else:
        profiles.rename_profile("vm-owner", "other")
    assert disk.read_bytes() == b"disk fixture"


def test_named_profile_export_omits_vm_keys_seed_and_disks(monkeypatch, tmp_path):
    from hermes_cli import profiles

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profile = profiles.get_profile_dir("vm-owner")
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    resource = state.reserve("demo")
    runtime = state.root()
    for path in (resource / "secrets/identity", resource / "seed.iso",
                 resource / "disk.qcow2", runtime / ".images/base.qcow2",
                 profile / "workspace/desktop-vms/notes.txt", profile / "SOUL.md"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("inert export fixture", encoding="utf-8")
    archive = profiles.export_profile("vm-owner", str(tmp_path / "shared.tar.gz"))
    with tarfile.open(archive) as stream:
        names = stream.getnames()
    assert "vm-owner/SOUL.md" in names
    assert "vm-owner/workspace/desktop-vms/notes.txt" in names
    assert not any(Path(name).name in {"identity", "seed.iso", "disk.qcow2", "base.qcow2"} for name in names)
    assert (resource / "secrets/identity").is_file()


def test_live_readiness_recovers_after_an_individual_daemon_restart(instance, monkeypatch):
    def probe(data, argv, **kwargs):
        if argv[0] == "test":
            # Stopping a required daemon clears the oneshot ready marker. It
            # does not undo installation; readiness must probe the recovered stack.
            return subprocess.CompletedProcess(argv, 0 if argv[-1].endswith("/installed") else 1, "", "")
        output = '{"status":"done"}' if argv[0] == "cloud-init" else ""
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(guest, "ssh", probe)
    assert guest.readiness(instance)["ready"]


def test_profile_rename_does_not_move_independent_cache(tmp_path, monkeypatch):
    from hermes_cli import profiles

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profile = profiles.get_profile_dir("cache-owner")
    monkeypatch.setenv("HERMES_HOME", str(profile))
    profile.mkdir(parents=True)
    cache = state.root() / ".images"
    cache.mkdir(parents=True)
    (cache / "base.qcow2").write_bytes(b"inert cached image fixture")
    profiles.rename_profile("cache-owner", "renamed-cache")
    assert (cache / "base.qcow2").read_bytes() == b"inert cached image fixture"


def test_creation_publishes_recoverable_state_before_external_work(tmp_path, monkeypatch):
    from hermes_cli import profiles

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profile = profiles.get_profile_dir("vm-owner")
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setattr(vm, "preflight", lambda: {"ready": True})
    run = Mock(side_effect=AssertionError("No guest launch or key generation before download"))
    monkeypatch.setattr(vm, "run", run)

    def download():
        assert state.load("demo")["phase"] == "preparing"
        shutil.rmtree(profile)
        raise state.VMError("fixture download failure")

    monkeypatch.setattr(vm, "download_image", download)
    with state.profile_scope(profile):
        with pytest.raises(state.VMError, match="fixture download failure"):
            vm.create("demo", network="nat")
    run.assert_not_called()
    assert state.load("demo")["phase"] == "failed"
    assert state.binding_status(state.load("demo"))["status"] == "stale"
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    monkeypatch.setattr(vm, "runtime_dir", lambda _: tmp_path / "runtime")
    vm.remove("demo", "demo")
    assert not state.vm_dir("demo").exists()


def test_missing_core_resource_api_is_irrelevant(monkeypatch, isolated_profile):
    before = set(isolated_profile.iterdir())
    monkeypatch.setitem(sys.modules, "hermes_cli.profile_resources", None)
    assert vm.list_vms() == []
    assert set(isolated_profile.iterdir()) == before


def test_rebinding_during_io_cannot_report_success(instance, monkeypatch):
    monkeypatch.setattr(vm, "unit_active", lambda _: False)

    def transport(*_args, **_kwargs):
        vm.unbind_vm("demo", instance["id"])
        return subprocess.CompletedProcess([], 0, "old operation", "")

    monkeypatch.setattr(guest, "ssh", transport)
    with pytest.raises(state.VMError, match="rebound"):
        guest.execute("demo", ["true"])


def test_capture_keeps_original_target_across_its_steps(instance, monkeypatch, tmp_path):
    seen = []

    def transport(snapshot, *_args, **_kwargs):
        seen.append(snapshot["id"])
        replacement = {**instance, "id": str(uuid.uuid4())}
        state.save("demo", replacement)
        return subprocess.CompletedProcess([], 0, "{}", "")

    monkeypatch.setattr(guest, "ssh", transport)
    transfer = Mock(side_effect=AssertionError("Do not download from replacement"))
    monkeypatch.setattr(guest, "transfer", transfer)
    with pytest.raises(state.VMError, match="replaced"):
        guest.capture("demo", 1, str(tmp_path / "shot.png"))
    assert seen and set(seen) == {instance["id"]}
    transfer.assert_not_called()


def test_metadata_identity_is_synced_before_external_provisioning(instance, monkeypatch):
    import os

    synced = []
    monkeypatch.setattr(state.os, "fsync", lambda fd: synced.append(Path(os.readlink(f"/proc/self/fd/{fd}"))))
    state.save("demo", instance)
    path = state.vm_dir("demo")
    assert synced == [path / "instance.json.tmp", path, path.parent, path.parent.parent]


@pytest.mark.parametrize("leftover", [None, "instance.json.tmp", "disk.qcow2"])
def test_initial_metadata_interruption_is_visible_and_recovers_only_unprovisioned_leaves(
    monkeypatch, isolated_profile, leftover,
):
    monkeypatch.setattr(vm, "preflight", lambda: {"ready": True})
    external = Mock(side_effect=AssertionError("No external resource was started"))
    monkeypatch.setattr(vm, "run", external)
    monkeypatch.setattr(vm, "unit_active", external)

    def interrupted(name, metadata):
        if leftover:
            (state.vm_dir(name) / leftover).write_bytes(b"incomplete fixture")
        raise KeyboardInterrupt

    monkeypatch.setattr(vm, "save", interrupted)
    with pytest.raises(KeyboardInterrupt):
        vm.create("partial", network="nat")
    leaf = state.vm_dir("partial")
    entries = vm.list_vms()
    assert entries[0]["name"] == "partial"
    assert entries[0]["phase"] == "incomplete"
    assert entries[0]["active"] is None  # unknown is not a claimed stopped guest
    if leftover == "disk.qcow2":
        with pytest.raises(state.VMError, match="not found"):
            vm.remove("partial", "partial")
        assert (leaf / leftover).read_bytes() == b"incomplete fixture"
    else:
        assert vm.remove("partial", "partial")["removed"] == "partial"
        assert not leaf.exists()
    external.assert_not_called()


def test_reservation_cannot_be_removed_before_initial_metadata(monkeypatch, isolated_profile):
    import fcntl

    monkeypatch.setattr(vm, "preflight", lambda: {"ready": True})
    original_reserve = vm.reserve

    def reserve(name):
        with (state.root() / ".locks" / f"vm-{name}.lock").open("a") as handle:
            with pytest.raises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return original_reserve(name)

    monkeypatch.setattr(vm, "reserve", reserve)
    monkeypatch.setattr(vm, "download_image", Mock(side_effect=state.VMError("fixture stopped before download")))
    with pytest.raises(state.VMError, match="fixture stopped before download"):
        vm.create("partial", network="nat")
