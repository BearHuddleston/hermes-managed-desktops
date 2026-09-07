"""Independent recovery CLI contracts; no guest or host service is started."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def invoke(*argv):
    return subprocess.run(
        [sys.executable, "-m", "managed_desktops", *map(str, argv)],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("argv", [("list",), ("--global", "--profile-home", "/missing", "list")])
def test_scope_selection_is_required_and_mutually_exclusive(argv):
    result = invoke(*argv)
    assert result.returncode == 2, result.stderr
    assert "--profile-home" in result.stderr
    assert "--global" in result.stderr


def test_help_is_available_without_a_scope_and_has_no_state_side_effects():
    from managed_desktops import state

    for argv in [("--help",), ("exec", "--help"), ("bind", "--help")]:
        result = invoke(*argv)
        assert result.returncode == 0, result.stderr
    assert not state.root().exists()


@pytest.mark.linux_only
@pytest.mark.parametrize("action", ["exec", "app", "cua"])
@pytest.mark.parametrize("leading", [[], ["--"]])
def test_standalone_preserves_literal_guest_argv(action, leading, monkeypatch, capsys):
    from managed_desktops import cli, io

    guest = [*leading, "python3", "-c", "print('a b')", "-r", "literal", "--profile-home", "/guest", "--global", "--", "", "--help"]
    calls = []
    monkeypatch.setattr(io, "execute", lambda name, argv, **kw: calls.append((name, argv, kw)) or 0)
    prefix = [action, "--screen", "2", "demo"] if action != "cua" else ["cua", "demo", "--screen", "2", "call"]
    assert cli.main(["--global", *prefix, "--", *guest]) == 0
    name, actual, options = calls.pop()
    assert name == "demo"
    assert actual[-len(guest):] == guest
    if action == "exec":
        assert actual == guest
        assert options["screen"] == 2
    capsys.readouterr()


@pytest.mark.linux_only
def test_explicit_profile_settings_and_global_defaults_are_isolated(
    isolated_profile, profile_factory, tmp_path, monkeypatch, capsys,
):
    from managed_desktops import cli, config, state, vm

    explicit = profile_factory(tmp_path / "explicit", settings={"cpus": 9})
    before = (explicit / "config.yaml").read_bytes()
    monkeypatch.setattr(vm, "list_vms", lambda: {
        "profile": str(state.current_profile()) if state.current_profile() else None,
        "settings": config.settings(),
    })
    assert cli.main(["--profile-home", str(explicit), "list"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["profile"] == str(explicit)
    assert result["settings"]["cpus"] == 9
    assert cli.main(["--global", "list"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"profile": None, "settings": dict(config.DEFAULTS)}
    assert os.environ["HERMES_HOME"] == str(isolated_profile)
    assert config.settings() == dict(config.DEFAULTS)
    assert (explicit / "config.yaml").read_bytes() == before


@pytest.mark.linux_only
@pytest.mark.parametrize("content", [None, "", "[invalid", "[]", "plugins: []", "plugins: {entries: {managed-desktops: {settings: []}}}", "plugins: {entries: {managed-desktops: {settings: {cpus: false}}}}"])
def test_invalid_profile_fails_closed_even_for_inventory(content, tmp_path, monkeypatch):
    from managed_desktops import cli, vm

    profile = tmp_path / "invalid"
    if content is not None:
        profile.mkdir()
        (profile / "config.yaml").write_text(content, encoding="utf-8")
    monkeypatch.setattr(vm, "list_vms", lambda: pytest.fail("invalid profile dispatched"))
    assert cli.main(["--profile-home", str(profile), "list"]) == 1
    if content is None:
        assert not profile.exists()


@pytest.mark.linux_only
def test_module_runs_with_hermes_imports_blocked(tmp_path, isolated_profile):
    # Isolated import paths contain only this package and its declared dependencies.
    import psutil
    import yaml

    script = """
import importlib.abc
import runpy
import sys
class NoHermes(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'hermes_constants', 'hermes_cli', 'tools', 'agent', 'gateway', 'plugins'}:
            raise ModuleNotFoundError('Hermes is unavailable: ' + fullname)
sys.meta_path.insert(0, NoHermes())
sys.path[:0] = __import__('json').loads(sys.argv[1])
sys.argv = ['managed_desktops', *sys.argv[2:]]
runpy.run_module('managed_desktops', run_name='__main__')
"""
    paths = [str(ROOT), str(Path(yaml.__file__).parents[1]), str(Path(psutil.__file__).parents[1])]
    for argv in [("--global", "list"), ("--profile-home", str(isolated_profile), "list")]:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", script, json.dumps(paths), *argv],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == []


@pytest.mark.linux_only
def test_profile_settings_never_follow_config_links_or_read_secrets(tmp_path, monkeypatch):
    from managed_desktops import cli, vm

    profile = tmp_path / "profile"
    profile.mkdir()
    secret = profile / ".env"
    secret.write_text("{}", encoding="utf-8")
    (profile / "config.yaml").symlink_to(secret)
    monkeypatch.setattr(vm, "list_vms", lambda: pytest.fail("followed a config symlink"))
    assert cli.main(["--profile-home", str(profile), "list"]) == 1


@pytest.mark.linux_only
def test_explicit_binding_cli_confirms_identity_and_enforces_profile_access(
    isolated_profile, profile_factory, tmp_path, monkeypatch, capsys,
):
    import uuid
    from managed_desktops import cli, state, vm

    identifier = str(uuid.uuid4())
    data = {
        "schema": 2, "id": identifier, "name": "demo", "binding": None,
        "owner_root": str(state.root()), "cpus": 2, "memory_mib": 2048,
        "disk_gib": 16, "ssh_port": 22888, "network": "isolated", "phase": "ready",
    }
    state.reserve("demo")
    state.save("demo", data)
    monkeypatch.setattr(vm, "unit_active", lambda _: False)
    bind = ["bind", "demo", "--profile-home", str(isolated_profile), "--confirm", identifier]
    assert cli.main(["--profile-home", str(isolated_profile), "list"]) == 0
    assert json.loads(capsys.readouterr().out) == []  # unbound VMs are global-only
    assert cli.main(["--global", *bind]) == 0
    assert state.load("demo")["binding"] == state.profile_identity(isolated_profile)
    assert state.load("demo")["id"] == identifier
    capsys.readouterr()
    assert cli.main(["--profile-home", str(isolated_profile), "list"]) == 0
    assert [entry["id"] for entry in json.loads(capsys.readouterr().out)] == [identifier]
    other = profile_factory(tmp_path / "other")
    assert cli.main(["--profile-home", str(other), "list"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert cli.main(["--profile-home", str(other), "status", "demo"]) == 1
    assert cli.main(["--global", "unbind", "demo", "--confirm", str(uuid.uuid4())]) == 1
    assert state.load("demo")["binding"] is not None
    assert cli.main(["--global", "unbind", "demo", "--confirm", identifier]) == 0
    assert state.load("demo")["binding"] is None
    assert state.load("demo")["id"] == identifier
    capsys.readouterr()


@pytest.mark.linux_only
@pytest.mark.parametrize("action", ["list", "bind"])
def test_existing_directory_without_profile_config_is_not_a_profile(action, tmp_path, monkeypatch):
    from managed_desktops import cli, vm

    profile = tmp_path / "not-a-profile"
    profile.mkdir()
    monkeypatch.setattr(vm, "list_vms", lambda: pytest.fail("invalid profile dispatched"))
    monkeypatch.setattr(vm, "bind_vm", lambda *_: pytest.fail("invalid profile binding"))
    command = ["--profile-home", str(profile), "list"] if action == "list" else [
        "--global", "bind", "demo", "--profile-home", str(profile), "--confirm", "literal",
    ]
    assert cli.main(command) == 1
    assert not (profile / "config.yaml").exists()


@pytest.mark.parametrize("action", ["bind", "unbind"])
def test_binding_changes_require_global_scope(action, isolated_profile):
    from managed_desktops.cli import main

    command = [action, "demo", "--confirm", "unchanged-literal"]
    if action == "bind":
        command += ["--profile-home", str(isolated_profile)]
    with pytest.raises(SystemExit) as result:
        main(["--profile-home", str(isolated_profile), *command])
    assert result.value.code == 2


@pytest.mark.parametrize("command", [
    ["create", "demo"], ["app", "demo", "--", "mousepad"],
    ["cua", "demo", "call", "list_windows", "{}"], ["remove", "demo"],
    ["bind", "demo", "--profile-home", "/explicit"], ["unbind", "demo"],
])
def test_standalone_requires_explicit_network_screen_and_confirmation(command):
    from managed_desktops.cli import standalone_parser

    with pytest.raises(SystemExit) as result:
        standalone_parser().parse_args(["--global", *command])
    assert result.value.code == 2


def test_packaging_exposes_independent_cli_with_bounded_runtime_dependencies():
    import importlib
    import tomllib
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    module, function = project["scripts"]["hermes-managed-desktops"].split(":")
    assert callable(getattr(importlib.import_module(module), function))
    dependencies = {requirement.name.lower(): requirement for text in project["dependencies"] for requirement in [Requirement(text)]}
    assert {"pyyaml", "psutil"} <= dependencies.keys()
    assert not any("hermes" in name for name in dependencies)
    for name in ("pyyaml", "psutil"):
        assert any(spec.operator == ">=" for spec in dependencies[name].specifier)
        assert any(spec.operator == "<" for spec in dependencies[name].specifier)
    supported = SpecifierSet(project["requires-python"])
    assert all(version in supported for version in ("3.11", "3.12", "3.13"))
