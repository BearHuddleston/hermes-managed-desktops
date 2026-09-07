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
    if sys.flags.optimize:
        raise RuntimeError("Run verification without Python -O; assertions are required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("user", "entrypoint"), required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--other-home", type=Path, required=True)
    parser.add_argument("--state-home", type=Path, required=True,
                        help="Explicit disposable XDG_STATE_HOME outside both profiles")
    args = parser.parse_args()
    args.home = args.home.resolve(strict=True)
    args.other_home = args.other_home.resolve(strict=True)
    args.state_home = args.state_home.resolve(strict=True)
    # No implicit profile/store: the caller provisions two disposable profiles
    # with different settings, explicitly enables the plugin, and isolates state.
    if not os.environ.get("HERMES_HOME") or Path(os.environ["HERMES_HOME"]).resolve() != args.home:
        parser.error("HERMES_HOME must explicitly match --home")
    if not os.environ.get("XDG_STATE_HOME") or Path(os.environ["XDG_STATE_HOME"]).resolve() != args.state_home:
        parser.error("XDG_STATE_HOME must explicitly match --state-home")
    if args.home == args.other_home:
        parser.error("Use two distinct disposable profiles")
    store = args.state_home / "hermes-managed-desktops"
    if store.exists() or store.is_symlink():
        parser.error("Use a fresh disposable state home with no VM store")
    for home in (args.home, args.other_home):
        if store.is_relative_to(home) or home.is_relative_to(store):
            parser.error("VM store and profile roots must not overlap")
        config_path = home / "config.yaml"
        if config_path.is_symlink() or not config_path.is_file():
            parser.error("Each disposable profile needs a regular config.yaml")

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
        state = importlib.import_module(package + ".state")
        saved = cli._dispatch
        # Probe the registration wrapper, not QEMU or any VM lifecycle function.
        cli._dispatch = lambda _args: {
            "home": str(get_hermes_home()), "settings": config.settings(),
            "profile_scope": str(state.current_profile()), "store": str(state.root()),
        }
        try:
            command_parser = argparse.ArgumentParser()
            _attach_plugin_cli_command(command_parser.add_subparsers(), manager._cli_commands["desktop-vm"])
            command = command_parser.parse_args(["desktop-vm", "list"])
            with scoped(args.other_home if manager is managers[0] else args.home):
                probes.append(command.func(command))
            assert config.settings() == dict(config.DEFAULTS)
            assert state.current_profile() is None
        finally:
            cli._dispatch = saved
    assert probes[0]["home"] == str(args.home.resolve())
    assert probes[1]["home"] == str(args.other_home.resolve())
    for probe in probes:
        assert probe["profile_scope"] == probe["home"]
        assert probe["store"] == str(store)
    assert probes[0]["settings"]["cpus"] != probes[1]["settings"]["cpus"]
    for home in (args.home, args.other_home):
        env = {**os.environ, "HERMES_HOME": str(home)}
        result = subprocess.run([sys.executable, "-m", "hermes_cli.main", "desktop-vm", "list"],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == [], "Use empty disposable profiles"
        for action in ("exec", "app", "cua"):
            refused = subprocess.run(
                [sys.executable, "-m", "hermes_cli.main", "desktop-vm", action],
                env=env, capture_output=True, text=True, timeout=30,
            )
            assert refused.returncode == 2, refused.stderr
            assert "hermes-managed-desktops --profile-home" in refused.stderr

    # Exercise the installed/copied standalone package from a neutral directory,
    # not this repository. Trap any attempted Hermes import, even if installed.
    standalone_env = {**os.environ, "PYTHONPATH": ""}
    expected_module = asset_root.parent / "__init__.py"
    if args.source == "user":
        standalone_env["PYTHONPATH"] = str(expected_module.parent.parent)
    standalone_probe = """
import importlib.abc
from pathlib import Path
import runpy
import sys

class NoHermes(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('hermes_cli', 'hermes_constants')):
            raise AssertionError('Standalone attempted Hermes import: ' + fullname)

sys.meta_path.insert(0, NoHermes())
import managed_desktops
assert Path(managed_desktops.__file__).resolve() == Path(sys.argv.pop(1)).resolve()
sys.argv[0] = 'managed_desktops'
runpy.run_module('managed_desktops', run_name='__main__')
"""
    for scope in (("--profile-home", str(args.home)),
                  ("--profile-home", str(args.other_home)), ("--global",)):
        result = subprocess.run(
            [sys.executable, "-c", standalone_probe, str(expected_module), *scope, "list"],
            cwd=args.state_home, env=standalone_env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == []
    refused = subprocess.run(
        [sys.executable, "-m", "managed_desktops", "list"], cwd=args.state_home,
        env=standalone_env, capture_output=True, text=True, timeout=30,
    )
    assert refused.returncode == 2, refused.stderr
    assert "--profile-home" in refused.stderr and "--global" in refused.stderr
    assert not store.exists(), "Read-only verification must not create VM state"
    assert not any((home / "managed-resources").exists() for home in (args.home, args.other_home))
    assert managers[0].unload("managed-desktops")
    assert managers[0].find_plugin_skill(qualified) is None
    assert "desktop-vm" not in managers[0]._cli_commands
    assert managers[1].find_plugin_skill(qualified).is_file()
    print(json.dumps({"source": args.source, "module_file": module.__file__,
                      "skill_path": str(path), "assets": str(asset_root),
                      "profile_probes": probes, "skill_readonly": True,
                      "unload_isolated": True, "real_cli_empty_inventories": True,
                      "native_opaque_routes_refused": True, "standalone_no_hermes_imports": True,
                      "explicit_scope_required": True, "vm_store_created": False}, indent=2))


if __name__ == "__main__":
    main()
