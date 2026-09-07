#!/usr/bin/env python3
"""Verify the installed wheel in a venv with no Hermes, without starting a VM."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    if sys.flags.optimize:
        raise RuntimeError("Run verification without Python -O; assertions are required")
    with tempfile.TemporaryDirectory(prefix="managed-desktops-standalone-") as temporary:
        sandbox = Path(temporary)
        home = sandbox / "profile"
        home.mkdir()
        (home / "config.yaml").write_text("{}\n", encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", os.defpath), "HOME": str(sandbox),
            "HERMES_HOME": str(home), "XDG_STATE_HOME": str(sandbox / "state"),
            "LANG": "C.UTF-8", "TZ": "UTC",
        }

        def run(argv, expected=0):
            result = subprocess.run([str(arg) for arg in argv], cwd=sandbox, env=env,
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode == expected, (argv, result.stdout, result.stderr)
            return result

        # -I removes cwd/PYTHONPATH/user-site leakage. This is an installed-artifact
        # check, not an import made to pass by exposing the source checkout.
        probe = run([sys.executable, "-I", "-c", """
import importlib.metadata
import importlib.util
import json
import managed_desktops
assert importlib.util.find_spec('hermes_cli') is None, 'Use a Hermes-free venv'
assert importlib.util.find_spec('hermes_constants') is None, 'Use a Hermes-free venv'
print(json.dumps({'module_file': managed_desktops.__file__,
                  'version': importlib.metadata.version('hermes-managed-desktops')}))
"""])
        launchers = [
            [sys.executable, "-I", "-m", "managed_desktops"],
            [str(Path(sys.executable).parent / "hermes-managed-desktops")],
        ]
        for launcher in launchers:
            assert "--profile-home" in run([*launcher, "--help"]).stdout
            for scope in (("--profile-home", str(home)), ("--global",)):
                assert json.loads(run([*launcher, *scope, "list"]).stdout) == []
            refused = run([*launcher, "list"], expected=2)
            assert "--profile-home" in refused.stderr and "--global" in refused.stderr
            missing = run([*launcher, "--profile-home", home, "status", "missing"], expected=1)
            assert "not found" in missing.stderr
        assert not (sandbox / "state/hermes-managed-desktops").exists()
        print(json.dumps({**json.loads(probe.stdout), "hermes_available": False,
                          "console_and_module_verified": True, "explicit_scope_required": True,
                          "vm_store_created": False}, indent=2))


if __name__ == "__main__":
    main()
