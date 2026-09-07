#!/usr/bin/env python3
"""Verify a staged/installed plugin in explicit empty test profiles; no VM work."""

import argparse
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("user", "entrypoint"), required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--other-home", type=Path, required=True)
    args = parser.parse_args()
    # No implicit profile, no non-empty inventory: the caller must provision two
    # disposable profiles with different plugin settings and explicitly enable it.
    if Path(os.environ.get("HERMES_HOME", "")).resolve() != args.home.resolve():
        parser.error("HERMES_HOME must explicitly match --home")

    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
    from hermes_cli.plugins import get_plugin_manager
    from hermes_cli.main import _attach_plugin_cli_command
    from tools.skills_tool import skill_view
    from tools.skill_manager_tool import skill_manage

    @contextmanager
    def scoped(home):
        token = set_hermes_home_override(home)
        try:
            yield
        finally:
            reset_hermes_home_override(token)

    managers = []
    for home in (args.home, args.other_home):
        with scoped(home):
            manager = get_plugin_manager()
            manager.discover_and_load()
            loaded = manager._plugins["managed-desktops"]
            assert loaded.enabled, loaded.error
            assert loaded.manifest.source == args.source
            managers.append(manager)
    module = managers[0]._plugins["managed-desktops"].module
    package = module.__name__ + (".managed_desktops" if args.source == "user" else "")
    # Validate packaged assets through their actual runtime owner, not source paths.
    guest = importlib.import_module(package + ".guest")
    asset_root = Path(guest.__file__).parent / "assets"
    assert (asset_root / "bootstrap.py").is_file()
    assert (asset_root / "hermes-vm-cua@.service").is_file()
    qualified = "managed-desktops:managed-agent-desktops"
    with scoped(args.home):
        viewed = json.loads(skill_view(qualified))
        assert viewed["success"], viewed
        path = managers[0].find_plugin_skill(qualified)
        before = path.read_bytes()
        refused = json.loads(skill_manage("delete", qualified))
        assert not refused.get("success"), refused
        assert path.read_bytes() == before
    probes = []
    for manager in managers:
        module = manager._plugins["managed-desktops"].module
        package = module.__name__ + (".managed_desktops" if args.source == "user" else "")
        cli = importlib.import_module(package + ".cli")
        config = importlib.import_module(package + ".config")
        saved = cli._dispatch
        # Probe the registration wrapper, not QEMU or any VM lifecycle function.
        cli._dispatch = lambda _args: {"home": str(get_hermes_home()), "settings": config.settings()}
        try:
            command_parser = argparse.ArgumentParser()
            _attach_plugin_cli_command(command_parser.add_subparsers(), manager._cli_commands["desktop-vm"])
            command = command_parser.parse_args(["desktop-vm", "list"])
            with scoped(args.other_home if manager is managers[0] else args.home):
                probes.append(command.func(command))
            assert config.settings() == dict(config.DEFAULTS)
        finally:
            cli._dispatch = saved
    assert probes[0]["home"] == str(args.home.resolve())
    assert probes[1]["home"] == str(args.other_home.resolve())
    assert probes[0]["settings"]["cpus"] != probes[1]["settings"]["cpus"]
    for home in (args.home, args.other_home):
        env = {**os.environ, "HERMES_HOME": str(home)}
        result = subprocess.run([sys.executable, "-m", "hermes_cli.main", "desktop-vm", "list"],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == [], "Use empty disposable profiles"
    assert managers[0].unload("managed-desktops")
    assert managers[0].find_plugin_skill(qualified) is None
    assert "desktop-vm" not in managers[0]._cli_commands
    assert managers[1].find_plugin_skill(qualified).is_file()
    print(json.dumps({"source": args.source, "module_file": module.__file__,
                      "skill_path": str(path), "assets": str(asset_root),
                      "profile_probes": probes, "skill_readonly": True,
                      "unload_isolated": True, "real_cli_empty_inventories": True}, indent=2))


if __name__ == "__main__":
    main()
