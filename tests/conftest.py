"""Run external plugin tests with isolated profiles and unmodified Hermes."""

from pathlib import Path
import shutil
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def stage_plugin(profile, *, settings=None):
    plugin = profile / "plugins" / "managed-desktops"
    plugin.mkdir(parents=True)
    for name in ("__init__.py", "plugin.yaml", "LICENSE"):
        shutil.copy2(ROOT / name, plugin / name)
    shutil.copytree(ROOT / "managed_desktops", plugin / "managed_desktops",
                    ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"))
    (profile / "config.yaml").write_text(yaml.safe_dump({
        "plugins": {
            "enabled": ["managed-desktops"],
            "entries": {"managed-desktops": {"settings": settings or {}}},
        },
    }), encoding="utf-8")
    return profile


@pytest.fixture(autouse=True)
def isolated_profile(_hermetic_environment, tmp_path, monkeypatch):
    home = tmp_path / "user"
    profile = home / "profile"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setenv("HERMES_TEST_ISOLATION", str(profile))
    return stage_plugin(profile)


@pytest.fixture
def profile_factory():
    return stage_plugin


def pytest_collection_modifyitems(items):
    for item in items:
        for marker, platform in (("linux_only", "linux"), ("macos_only", "darwin"), ("windows_only", "win32")):
            if item.get_closest_marker(marker) and sys.platform != platform:
                item.add_marker(pytest.mark.skip(reason=f"{marker} on {sys.platform}"))
