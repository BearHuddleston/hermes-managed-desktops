"""External ownership survives profile mutation without conferring copied access."""

from pathlib import Path
import shutil
import sys
import tarfile
import uuid

import pytest

from managed_desktops import state, vm

pytestmark = pytest.mark.linux_only


def stored_vm(profile=None, name="demo"):
    state.reserve(name)
    data = {
        "schema": 2, "id": str(uuid.uuid4()), "name": name,
        "owner_root": str(state.root()),
        "binding": state.profile_identity(profile) if profile else None,
        "cpus": 2, "memory_mib": 2048, "disk_gib": 16, "ssh_port": 22888,
        "network": "isolated", "phase": "ready",
    }
    state.save(name, data)
    (state.vm_dir(name) / "disk.qcow2").write_bytes(b"inert persistent disk")
    return data


def test_storage_and_empty_inventory_need_no_core_resource_api(monkeypatch, isolated_profile):
    monkeypatch.setitem(sys.modules, "hermes_cli.profile_resources", None)
    assert not state.root().is_relative_to(isolated_profile)
    assert vm.list_vms() == []
    assert not state.root().exists()  # discovery/inventory does not create a store


def test_profile_binding_cannot_be_copied_and_rebinding_is_explicit(
    isolated_profile, tmp_path, monkeypatch,
):
    data = stored_vm(isolated_profile)
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    clone = tmp_path / "cloned"
    shutil.copytree(isolated_profile, clone)
    with state.profile_scope(isolated_profile):
        assert state.load("demo")["id"] == data["id"]
    with state.profile_scope(clone):
        assert vm.list_vms() == []
        with pytest.raises(state.VMError, match="bound"):
            state.load("demo")
        with pytest.raises(state.VMError, match="global"):
            vm.bind_vm("demo", clone, data["id"])
    with pytest.raises(state.VMError, match="UUID"):
        vm.bind_vm("demo", clone, str(uuid.uuid4()))
    monkeypatch.setattr(vm, "unit_active", lambda _: True)
    with pytest.raises(state.VMError, match="Stop"):
        vm.bind_vm("demo", clone, data["id"])
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    vm.bind_vm("demo", clone, data["id"])
    with state.profile_scope(isolated_profile):
        with pytest.raises(state.VMError, match="bound"):
            state.load("demo")
    with state.profile_scope(clone):
        assert state.load("demo")["id"] == data["id"]


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_stock_profile_mutation_preserves_recoverable_vm(
    operation, tmp_path, monkeypatch,
):
    from hermes_cli import profiles

    profile = profiles.get_profile_dir("bound-owner")
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("{}\n")
    data = stored_vm(profile)
    disk = state.vm_dir("demo") / "disk.qcow2"
    monkeypatch.setattr(profiles, "_check_gateway_running", lambda *_: False)
    # Isolated profile never owns these services; do not scan/control real backends.
    monkeypatch.setattr(profiles, "_cleanup_gateway_service", lambda *_: None)
    monkeypatch.setattr(profiles, "_stop_profile_backends", lambda *_: None)
    if operation == "delete":
        profiles.delete_profile("bound-owner", yes=True)
    else:
        profiles.rename_profile("bound-owner", "renamed-owner")
    assert disk.read_bytes() == b"inert persistent disk"
    assert state.load("demo")["id"] == data["id"]
    assert state.binding_status(data)["status"] == "stale"
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    monkeypatch.setattr(vm, "runtime_dir", lambda _: tmp_path / "unused-runtime")
    assert vm.list_vms()[0]["binding_status"]["status"] == "stale"
    with state.profile_scope(profile):
        with pytest.raises(state.VMError):
            state.load("demo")
    vm.remove("demo", "demo")
    assert not disk.exists()


def test_same_path_recreation_is_not_automatic_recovery(isolated_profile, monkeypatch):
    data = stored_vm(isolated_profile)
    old = state.profile_identity(isolated_profile)
    renamed = isolated_profile.with_name("previous-profile")
    isolated_profile.rename(renamed)
    isolated_profile.mkdir()
    assert state.profile_identity(isolated_profile) != old
    with state.profile_scope(isolated_profile):
        with pytest.raises(state.VMError, match="bound"):
            state.load("demo")
    # Ordinary edits must not expire a valid filesystem binding.
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    vm.bind_vm("demo", isolated_profile, data["id"])
    (isolated_profile / "config.yaml").write_text("{}\n")
    (isolated_profile / "sessions").mkdir()
    with state.profile_scope(isolated_profile):
        assert state.load("demo")["id"] == data["id"]


def test_real_clone_and_export_do_not_contain_external_ownership(
    isolated_profile, tmp_path, monkeypatch,
):
    from hermes_cli import profiles

    owner = profiles.get_profile_dir("export-owner")
    owner.mkdir(parents=True)
    (owner / "config.yaml").write_text("{}\n")
    (owner / "SOUL.md").write_text("ordinary profile document")
    data = stored_vm(owner)
    monkeypatch.setenv("HERMES_HOME", str(owner))
    clone = profiles.create_profile("cloned-owner", clone_all=True, no_alias=True)
    assert (clone / "SOUL.md").read_text() == "ordinary profile document"
    with state.profile_scope(clone):
        with pytest.raises(state.VMError, match="bound"):
            state.load("demo")
    archive = profiles.export_profile("export-owner", str(tmp_path / "export.tar.gz"))
    with tarfile.open(archive) as stream:
        names = stream.getnames()
    assert "export-owner/SOUL.md" in names
    assert not any(Path(name).name in {"instance.json", "disk.qcow2"} for name in names)
    assert state.load("demo")["id"] == data["id"]


def test_store_cannot_overlap_selected_profile(isolated_profile, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(isolated_profile / "inside"))
    with state.profile_scope(isolated_profile):
        with pytest.raises(state.VMError, match="outside"):
            state.reserve("demo")
    assert not (isolated_profile / "inside").exists()


def test_unbinding_retains_vm_but_revokes_profile_access(isolated_profile, monkeypatch):
    data = stored_vm(isolated_profile)
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    vm.unbind_vm("demo", data["id"])
    assert state.binding_status(state.load("demo"))["status"] == "unbound"
    with state.profile_scope(isolated_profile):
        assert vm.list_vms() == []
        with pytest.raises(state.VMError, match="bound"):
            state.load("demo")
    assert (state.vm_dir("demo") / "disk.qcow2").read_bytes() == b"inert persistent disk"


def test_corrupt_or_legacy_metadata_is_not_adopted(isolated_profile):
    state.reserve("legacy")
    state.save("legacy", {"schema": 1, "name": "legacy"})
    with pytest.raises(state.VMError, match="schema|legacy"):
        state.load("legacy")
    legacy = isolated_profile / "managed-resources/managed-desktops/resources/old"
    legacy.mkdir(parents=True)
    (legacy / "disk.qcow2").write_bytes(b"legacy fixture")
    with state.profile_scope(isolated_profile):
        with pytest.raises(state.VMError, match="legacy|0.1.0"):
            vm.list_vms()
    assert (legacy / "disk.qcow2").read_bytes() == b"legacy fixture"


def test_binding_tracks_directory_not_config_contents(tmp_path):
    profile = tmp_path / "owner"
    profile.mkdir()
    config = profile / "config.yaml"
    config.write_text("{}\n")
    data = stored_vm(profile)
    config.unlink()
    config.write_text("plugins: {}\n")
    (profile / "new-session.txt").write_text("inert session")
    with state.profile_scope(profile):
        assert state.load("demo")["binding"] == data["binding"]


def test_profile_without_birth_time_fails_closed_but_global_recovery_works(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    profile = tmp_path / "owner"
    profile.mkdir()
    data = stored_vm(profile)
    info = profile.stat()
    monkeypatch.setattr(state, "run", lambda argv: CompletedProcess(argv, 0, f"{info.st_dev}:{info.st_ino}:-", ""))
    with state.profile_scope(profile):
        with pytest.raises(state.BindingError, match="birth time"):
            state.load("demo")
    assert state.load("demo")["id"] == data["id"]
    assert state.binding_status(data)["status"] == "stale"


def test_scoped_inventory_and_removal_cannot_claim_unknown_reservations(tmp_path, monkeypatch):
    profile = tmp_path / "owner"
    profile.mkdir()
    stored_vm(profile)
    state.reserve("interrupted")
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    with state.profile_scope(profile):
        assert [row["name"] for row in vm.list_vms()] == ["demo"]
        with pytest.raises(state.BindingError, match="--global"):
            vm.remove("interrupted", "interrupted")
    assert vm.remove("interrupted", "interrupted")["unprovisioned"]


def test_malformed_inventory_entry_does_not_hide_recoverable_vm(tmp_path, monkeypatch):
    stored_vm()
    bad = state.vm_dir("demo").parent / "Invalid Name"
    bad.mkdir()
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    entries = vm.list_vms()
    assert next(row for row in entries if row["name"] == "demo")["id"]
    assert next(row for row in entries if row["name"] == bad.name)["phase"] == "incomplete"


def test_default_store_uses_os_account_not_profile_isolated_home(tmp_path, monkeypatch):
    import pwd
    from types import SimpleNamespace

    account = tmp_path / "account"
    profile = account / ".hermes/profiles/owner"
    nested_home = profile / "home"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(nested_home))
    monkeypatch.setattr(Path, "home", lambda: nested_home)
    monkeypatch.setattr(pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(account)))
    with state.profile_scope(profile):
        assert state.root() == account / ".local/state/hermes-managed-desktops"
    assert not account.exists()


def test_store_cannot_overlap_known_default_hermes_root(tmp_path, monkeypatch):
    import pwd
    from types import SimpleNamespace

    account = tmp_path / "account"
    monkeypatch.setattr(pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(account)))
    monkeypatch.setenv("XDG_STATE_HOME", str(account / ".hermes/profiles/other/state"))
    with pytest.raises(state.VMError, match="outside"):
        state.root()


def test_store_selection_is_frozen_for_each_invocation(tmp_path, monkeypatch):
    before = state.root()
    with state.profile_scope(None):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "different"))
        assert state.root() == before
    assert state.root() != before


def test_global_bind_rejects_store_inside_targets_custom_hermes_root(tmp_path, monkeypatch):
    from managed_desktops import cli

    custom_root = tmp_path / "custom-hermes"
    target = custom_root / "profiles/owner"
    target.mkdir(parents=True)
    (target / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("XDG_STATE_HOME", str(custom_root / "profiles/other/state"))
    data = stored_vm()
    metadata = state.vm_dir("demo") / "instance.json"
    before = metadata.read_bytes()
    monkeypatch.setattr(vm, "unit_active", lambda _: False)

    assert cli.main([
        "--global", "bind", "demo", "--profile-home", str(target), "--confirm", data["id"],
    ]) == 1
    assert metadata.read_bytes() == before
    # The bad layout is refused for binding, not hidden from operator recovery.
    assert state.load("demo")["binding"] is None
    assert vm.list_vms()[0]["id"] == data["id"]
    with state.profile_scope(target):
        with pytest.raises(state.VMError, match="outside"):
            state.root()


@pytest.mark.parametrize("invalid_id", [42, True, {}, [], None, "", "ABCDEFAB-1234-1234-1234-123456789012"])
def test_invalid_uuid_does_not_hide_healthy_inventory(invalid_id, monkeypatch):
    healthy = stored_vm(name="healthy")
    broken = stored_vm(name="broken")
    broken["id"] = invalid_id
    state.save("broken", broken)
    monkeypatch.setattr(vm, "unit_active", lambda _: False)

    rows = {row["name"]: row for row in vm.list_vms()}
    assert rows["healthy"]["id"] == healthy["id"]
    assert rows["broken"]["phase"] == "incomplete"
    assert rows["broken"]["removable_unprovisioned"] is False
    with pytest.raises(state.VMError, match="Invalid.*metadata"):
        vm.remove("broken", "broken")
    assert (state.vm_dir("broken") / "disk.qcow2").exists()
