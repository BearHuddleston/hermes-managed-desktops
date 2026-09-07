"""Public CLI targeting contracts, exercised without a real VM."""

import json
import subprocess
import sys

import pytest


def invoke(*args):
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "desktop-vm", *args],
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.linux_only
def test_empty_profile_inventory_and_missing_target_fail_closed():
    inventory = invoke("list")
    assert inventory.returncode == 0, inventory.stderr
    assert json.loads(inventory.stdout) == []
    missing = invoke("status", "does-not-exist")
    assert missing.returncode != 0
    assert "does-not-exist" in missing.stderr
    assert "not found" in missing.stderr.lower()


@pytest.mark.parametrize("args", [
    ("create", "demo"),  # Provisioning egress must be explicitly accepted.
    ("capture", "demo"),  # No implicit screen.
    ("remove", "demo"),  # No implicit destructive confirmation.
])
def test_unsafe_implicit_defaults_are_rejected(args):
    result = invoke(*args)
    assert result.returncode == 2
    assert "required" in result.stderr


def test_documented_screen_targeting_reaches_the_guest_parser():
    from managed_desktops.cli import standalone_parser

    parser = standalone_parser()
    cases = (
        ("app", "--screen", "2", "demo", "--", "mousepad", "--disable-server"),
        ("exec", "--screen", "2", "demo", "--", "printenv", "DISPLAY"),
        ("cua", "demo", "--screen", "2", "call", "list_windows", "{}"),
        ("record", "demo", "--screen", "2", "start", "walkthrough"),
        ("capture", "demo", "--screen", "2", "--pid", "123", "--window-id", "456"),
    )
    for command in cases:
        parsed = parser.parse_args(["--global", *command])
        assert parsed.name == "demo"
        assert parsed.screen == 2
        assert parsed.vm_action == command[0]


@pytest.mark.linux_only
def test_vm_defaults_are_configurable_through_real_cli(monkeypatch, capsys):
    import argparse
    import importlib
    from hermes_cli.plugins import get_plugin_manager
    from hermes_cli.main import _attach_plugin_cli_command

    manager = get_plugin_manager()
    manager.discover_and_load()
    module = manager._plugins["managed-desktops"].module
    config = importlib.import_module(module.__name__ + ".managed_desktops.config")
    vm = importlib.import_module(module.__name__ + ".managed_desktops.vm")
    monkeypatch.setattr(vm, "list_vms", config.settings)
    parser = argparse.ArgumentParser()
    _attach_plugin_cli_command(parser.add_subparsers(), manager._cli_commands["desktop-vm"])
    args = parser.parse_args(["desktop-vm", "list"])

    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "config", "set", "plugins.entries.managed-desktops.settings.cpus", "3"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert args.func(args) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["cpus"] == 3
    assert resolved["memory_mib"] == config.DEFAULTS["memory_mib"]
    # No last-used profile settings may remain in the shared config module.
    assert config.settings() == dict(config.DEFAULTS)


def test_uncommitted_staging_helper_and_cli_enable(tmp_path, monkeypatch):
    from pathlib import Path
    import yaml

    home = tmp_path / "staged-profile"
    script = Path(__file__).resolve().parents[1] / "scripts" / "stage_plugin.py"
    staged = subprocess.run([sys.executable, str(script), str(home)],
                            capture_output=True, text=True, timeout=30)
    assert staged.returncode == 0, staged.stderr
    plugin = home / "plugins" / "managed-desktops"
    assert (plugin / "__init__.py").is_file()
    assert (plugin / "LICENSE").read_bytes() == (script.parents[1] / "LICENSE").read_bytes()
    assert (plugin / "managed_desktops" / "skills" / "managed-agent-desktops" / "SKILL.md").is_file()
    assert not (home / "config.yaml").exists()  # copying is not enabling
    refused = subprocess.run([sys.executable, str(script), str(home)],
                             capture_output=True, text=True, timeout=30)
    assert refused.returncode != 0
    monkeypatch.setenv("HERMES_HOME", str(home))
    enabled = subprocess.run([sys.executable, "-m", "hermes_cli.main", "plugins", "enable", "managed-desktops", "--no-allow-tool-override"],
                             capture_output=True, text=True, timeout=30)
    assert enabled.returncode == 0, enabled.stderr
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert "managed-desktops" in config["plugins"]["enabled"]
    help_result = invoke("--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "preflight" in help_result.stdout
    assert not (home / "managed-resources").exists()


@pytest.mark.parametrize("action", ["exec", "app", "cua"])
def test_native_arbitrary_argv_is_rejected_with_standalone_guidance(action, monkeypatch, capsys):
    import argparse
    import importlib
    from hermes_cli.plugins import get_plugin_manager
    from hermes_cli.main import _attach_plugin_cli_command

    manager = get_plugin_manager()
    manager.discover_and_load()
    module = manager._plugins["managed-desktops"].module
    io = importlib.import_module(module.__name__ + ".managed_desktops.io")
    monkeypatch.setattr(io, "execute", lambda *_a, **_kw: pytest.fail("native guest execution"))
    parser = argparse.ArgumentParser()
    _attach_plugin_cli_command(parser.add_subparsers(), manager._cli_commands["desktop-vm"])
    args = parser.parse_args(["desktop-vm", action, "demo", "--", "python3", "-c", "print(1)", "-r", "literal"])
    assert args.func(args) == 2
    assert "hermes-managed-desktops --profile-home" in capsys.readouterr().err
    # Full stock CLI path also refuses, rather than advertising unsafe forwarding.
    result = invoke(action, "demo")
    assert result.returncode == 2
    assert "hermes-managed-desktops --profile-home" in result.stderr
