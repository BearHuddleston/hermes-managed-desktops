#!/usr/bin/env python3
"""Verify directory/wheel discovery on exact stock Hermes in disposable profiles."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


STOCK_HERMES_SHA = "8aa219ef60ae16bd130e4775a1513e122880654e"


def compat_available(core):
    """Distinguish retired migration tooling from a broken, false-clean scan."""
    module = core / "hermes_cli/plugin_compat.py"
    manifest = core / "compat_manifest.json"
    if not module.exists() and not manifest.exists():
        return False
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))["entries"]
        valid = module.is_file() and isinstance(entries, list) and bool(entries) and all(
            isinstance(entry, dict) and all(
                isinstance(entry.get(key), str) and entry[key] for key in ("facade", "name")
            ) for entry in entries
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Invalid or incomplete compatibility migration tooling") from exc
    if not valid:
        raise RuntimeError("Invalid or incomplete compatibility migration tooling")
    return True


def check_compat_report(output):
    report = json.loads(output)
    if not isinstance(report, dict) or report.get("plugins") != {}:
        raise RuntimeError(f"Plugin compatibility check did not report zero hits: {report}")
    return report


def run(argv, *, cwd, env):
    result = subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env,
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {argv}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--source", choices=("user", "entrypoint"), required=True)
    args = parser.parse_args()
    core = args.core.resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    if core.is_relative_to(root):
        parser.error("Keep the stock Hermes checkout outside the plugin source tree")
    # Pin both history and worktree contents; never accept a patched harness.
    head = run(["git", "rev-parse", "HEAD"], cwd=core, env=os.environ).strip()
    if head != STOCK_HERMES_SHA:
        parser.error(f"Expected stock Hermes {STOCK_HERMES_SHA}, got {head}")
    if run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=core, env=os.environ).strip():
        parser.error("The stock Hermes verification checkout must be clean")
    has_compat = compat_available(core)

    with tempfile.TemporaryDirectory(prefix="managed-desktops-install-") as temporary:
        sandbox = Path(temporary)
        state_home = sandbox / "state"
        state_home.mkdir()
        host_home = sandbox / "user"
        host_home.mkdir()
        homes = [sandbox / "first", sandbox / "second"]
        # Do not inherit credentials, project-plugin opt-ins or an ambient profile.
        env = {
            "PATH": os.environ.get("PATH", os.defpath), "HOME": str(host_home),
            "PYTHONPATH": str(core), "PYTHONUTF8": "1", "PYTHONHASHSEED": "0",
            "XDG_STATE_HOME": str(state_home), "TZ": "UTC", "LANG": "C.UTF-8",
        }
        native = [sys.executable, "-m", "hermes_cli.main"]
        compatibility: dict[str, object] = {"available": has_compat}
        if has_compat:
            env["PYTHONWARNINGS"] = "error::hermes_cli.plugin_compat.HermesPluginCompatWarning"
            env["HERMES_HOME"] = str(sandbox / "scan-profile")
            # The core scanner returns no hits for a missing/invalid manifest.
            # Check its parsed catalog too, not just the source JSON's shape.
            run([sys.executable, "-c", "from hermes_cli.plugin_compat import load_manifest; "
                 "assert load_manifest(), 'Empty compatibility catalog'"], cwd=sandbox, env=env)
            compatibility["source"] = check_compat_report(run(
                [*native, "plugins", "compat", root, "--json"], cwd=sandbox, env=env))
        for home, cpus in zip(homes, (3, 7)):
            home.mkdir()
            (home / "config.yaml").write_text("{}\n", encoding="utf-8")
            env["HERMES_HOME"] = str(home)
            if args.source == "user":
                run([sys.executable, root / "scripts/stage_plugin.py", home], cwd=sandbox, env=env)
            run([*native, "plugins", "enable", "managed-desktops", "--no-allow-tool-override"],
                cwd=sandbox, env=env)
            run([*native, "config", "set", "plugins.entries.managed-desktops.settings.cpus", cpus],
                cwd=sandbox, env=env)
        env["HERMES_HOME"] = str(homes[0])
        if has_compat:
            compatibility["installed"] = check_compat_report(run(
                [*native, "plugins", "compat", "--json"], cwd=sandbox, env=env))
        output = run([
            sys.executable, root / "scripts/verify_install.py", "--source", args.source,
            "--home", homes[0], "--other-home", homes[1], "--state-home", state_home,
        ], cwd=sandbox, env=env)
        report = json.loads(output)
        if args.source == "entrypoint" and Path(report["module_file"]).resolve().is_relative_to(root):
            raise RuntimeError("Wheel verification imported the source checkout instead of the installed artifact")
        print(json.dumps({"stock_hermes_sha": head, "compatibility": compatibility, **report}, indent=2))


if __name__ == "__main__":
    main()
