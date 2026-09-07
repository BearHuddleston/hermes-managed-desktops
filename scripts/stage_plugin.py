#!/usr/bin/env python3
"""Copy an uncommitted plugin checkout without Git install or live defaults."""

import argparse
from pathlib import Path
import shutil


def stage(home):
    source = Path(__file__).resolve().parents[1]
    target = Path(home).expanduser().resolve() / "plugins" / "managed-desktops"
    target.mkdir(parents=True, exist_ok=False)
    for name in ("__init__.py", "plugin.yaml", "LICENSE"):
        shutil.copy2(source / name, target / name)
    shutil.copytree(source / "managed_desktops", target / "managed_desktops",
                    ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("home", type=Path, help="Explicit destination Hermes profile home")
    args = parser.parse_args()
    print(stage(args.home))
