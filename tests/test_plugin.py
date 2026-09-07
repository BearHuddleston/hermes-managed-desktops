"""Native discovery and profile contracts; never start a guest."""

import argparse
from contextlib import contextmanager
import importlib
import json
from pathlib import Path

import pytest


@contextmanager
def home_scope(home):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def discover(home):
    from hermes_cli.plugins import get_plugin_manager

    with home_scope(home):
        manager = get_plugin_manager()
        manager.discover_and_load()
    loaded = manager._plugins["managed-desktops"]
    assert loaded.enabled, loaded.error
    return manager, loaded.module


def parsed_command(manager, *argv):
    # This is the actual runtime attachment path, including its default ordering.
    from hermes_cli.main import _attach_plugin_cli_command

    parser = argparse.ArgumentParser()
    _attach_plugin_cli_command(parser.add_subparsers(), manager._cli_commands["desktop-vm"])
    return parser.parse_args(["desktop-vm", *argv])


@pytest.mark.linux_only
@pytest.mark.parametrize("source", ["directory", "entrypoint"])
def test_callbacks_keep_originating_profile_and_settings(
    source, isolated_profile, profile_factory, tmp_path, monkeypatch, capsys,
):
    from hermes_constants import get_hermes_home

    first = profile_factory(tmp_path / "first", settings={"cpus": 3})
    second = profile_factory(tmp_path / "second", settings={"cpus": 7})
    if source == "entrypoint":
        from importlib.metadata import EntryPoint, EntryPoints
        # Real ep.load()/module loading, with discovery metadata supplied locally.
        entries = EntryPoints([EntryPoint(
            name="managed-desktops", value="managed_desktops", group="hermes_agent.plugins",
        )])
        monkeypatch.setattr(importlib.metadata, "entry_points", lambda: entries)
    registrations = [discover(home) for home in (first, second)]
    if source == "directory":
        assert registrations[0][1].__name__ != registrations[1][1].__name__
    else:
        assert registrations[0][1] is registrations[1][1]
    for manager, module in registrations:
        package = module.__name__ + (".managed_desktops" if source == "directory" else "")
        vm = importlib.import_module(package + ".vm")
        monkeypatch.setattr(vm, "list_vms", lambda vm=vm: {
            "home": str(get_hermes_home()), "cpus": vm.settings()["cpus"],
        })
    for index, (home, cpus) in enumerate(((first, 3), (second, 7), (first, 3))):
        manager, _ = registrations[index % 2]
        args = parsed_command(manager, "list")
        with home_scope(isolated_profile):
            assert args.func(args) == 0
            assert get_hermes_home() == isolated_profile
        assert json.loads(capsys.readouterr().out) == {"home": str(home), "cpus": cpus}


@pytest.mark.linux_only
def test_real_discovery_registers_readonly_skill_and_unloads_per_profile(
    isolated_profile, profile_factory, tmp_path,
):
    from tools.skills_tool import skill_view
    from tools.skill_manager_tool import skill_manage

    qualified = "managed-desktops:managed-agent-desktops"
    first, _ = discover(isolated_profile)
    second_home = profile_factory(tmp_path / "second")
    second, _ = discover(second_home)
    skill_path = first.find_plugin_skill(qualified)
    assert skill_path is not None
    before = skill_path.read_bytes()
    with home_scope(isolated_profile):
        viewed = json.loads(skill_view(qualified))
        assert viewed["success"], viewed
        assert viewed["name"] == qualified
        assert "hermes desktop-vm" in viewed["content"]
        assert not json.loads(skill_view("managed-agent-desktops"))["success"]
        refused = json.loads(skill_manage("delete", qualified))
        assert not refused.get("success"), refused
    assert skill_path.read_bytes() == before
    assert not (isolated_profile / "skills" / "managed-agent-desktops").exists()
    assert first.unload("managed-desktops")
    assert "desktop-vm" not in first._cli_commands
    assert first.find_plugin_skill(qualified) is None
    assert second.find_plugin_skill(qualified).is_file()
    with home_scope(isolated_profile):
        assert not json.loads(skill_view(qualified))["success"]
    with home_scope(second_home):
        assert json.loads(skill_view(qualified))["success"]


@pytest.mark.linux_only
def test_disable_through_cli_removes_surfaces_not_resources(isolated_profile):
    import subprocess
    import sys
    from tools.skills_tool import skill_view

    manager, _ = discover(isolated_profile)
    # Opaque owned data, not a running guest: disable must not delete it.
    from hermes_cli.profile_resources import reserve_resource
    resource = reserve_resource("managed-desktops", "retained")
    sentinel = resource / "sentinel"
    sentinel.write_text("owned data", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "plugins", "disable", "managed-desktops"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    manager.discover_and_load(force=True)
    assert not manager._plugins["managed-desktops"].enabled
    assert "desktop-vm" not in manager._cli_commands
    assert not json.loads(skill_view("managed-desktops:managed-agent-desktops"))["success"]
    assert sentinel.read_text(encoding="utf-8") == "owned data"


def test_missing_prerequisite_does_not_break_discovery_or_help(
    isolated_profile, monkeypatch, capsys,
):
    import sys

    monkeypatch.setitem(sys.modules, "hermes_cli.profile_resources", None)
    manager, module = discover(isolated_profile)
    package = module.__name__ + ".managed_desktops"
    assert package + ".vm" not in sys.modules
    assert package + ".io" not in sys.modules
    assert package + ".state" not in sys.modules
    with pytest.raises(SystemExit) as exit_info:
        parsed_command(manager, "--help")
    assert exit_info.value.code == 0
    assert "preflight" in capsys.readouterr().out
    assert package + ".vm" not in sys.modules
    assert not (isolated_profile / "managed-resources").exists()


@pytest.mark.linux_only
def test_resource_cli_fails_closed_without_prerequisite(isolated_profile, monkeypatch, capsys):
    import sys

    monkeypatch.setitem(sys.modules, "hermes_cli.profile_resources", None)
    manager, _ = discover(isolated_profile)
    args = parsed_command(manager, "list")
    assert args.func(args) == 1
    assert "prerequisite" in capsys.readouterr().err
    assert not (isolated_profile / "managed-resources").exists()


def test_public_context_only_registration_restores_binding_on_exception(
    isolated_profile, tmp_path, monkeypatch,
):
    from managed_desktops import cli, config, register
    from hermes_constants import get_hermes_home

    class PublicContext:
        """The documented ctx surface only, deliberately no manager internals."""
        def get_config(self, key, default=None):
            assert get_hermes_home() == isolated_profile
            return 11 if key == "cpus" else default

        def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
            self.setup, self.handler = setup_fn, handler_fn

        def register_skill(self, name, path):
            assert path.is_file()

    ctx = PublicContext()
    register(ctx)
    parser = argparse.ArgumentParser()
    # Support runtime attachment ordering both before and after setup_parser.
    parser.set_defaults(func=ctx.handler)
    ctx.setup(parser)
    args = parser.parse_args(["list"])
    assert args.func is ctx.handler

    def fail(_args):
        assert config.settings()["cpus"] == 11
        raise RuntimeError("callback failed")

    monkeypatch.setattr(cli, "_dispatch", fail)
    other = tmp_path / "ambient"
    with home_scope(other):
        with pytest.raises(RuntimeError, match="callback failed"):
            args.func(args)
        assert get_hermes_home() == other
        assert config.settings() == dict(config.DEFAULTS)


def test_config_bindings_are_nested_concurrent_and_return_fresh_values(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from managed_desktops.config import DEFAULTS, bind, settings
    from hermes_constants import get_hermes_home

    barrier = Barrier(2)

    def worker(cpus):
        home = tmp_path / str(cpus)
        with bind(home, lambda key, default: cpus if key == "cpus" else default):
            barrier.wait(timeout=5)
            assert get_hermes_home() == home
            assert settings()["cpus"] == cpus
            changed = settings()
            changed["cpus"] = -1
            assert settings()["cpus"] == cpus
            with bind(tmp_path / "nested", lambda key, default: default):
                assert settings() == dict(DEFAULTS)
            assert settings()["cpus"] == cpus
        assert settings() == dict(DEFAULTS)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker, (3, 7)))


def test_manifest_defaults_match_config_and_skill_is_packaged_once():
    import yaml
    from managed_desktops.config import DEFAULTS

    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    assert {key: entry["default"] for key, entry in manifest["config_schema"].items()} == dict(DEFAULTS)
    skills = list(root.glob("**/SKILL.md"))
    assert skills == [root / "managed_desktops" / "skills" / "managed-agent-desktops" / "SKILL.md"]
    frontmatter = yaml.safe_load(skills[0].read_text(encoding="utf-8").split("---", 2)[1])
    assert len(frontmatter["description"]) <= 60
    assert frontmatter["description"].endswith(".")
    assert frontmatter["platforms"] == ["linux"]
