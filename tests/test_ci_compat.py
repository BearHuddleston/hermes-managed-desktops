"""Migration checks fail closed while tooling exists, retire explicitly afterward."""
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "managed_desktops_ci_check", Path(__file__).resolve().parents[1] / "scripts/verify_ci.py")
assert SPEC is not None and SPEC.loader is not None
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


def tooling(root, entries=None):
    module = root / "hermes_cli/plugin_compat.py"
    module.parent.mkdir(parents=True)
    module.write_text("# Temporary scanner fixture\n")
    manifest = root / "compat_manifest.json"
    manifest.write_text(json.dumps({"entries": entries if entries is not None else [
        {"facade": "old_module", "name": "function", "target": "new_module"},
    ]}))
    return manifest


def test_available_migration_tooling_requires_a_real_manifest(tmp_path):
    tooling(tmp_path)
    assert ci.compat_available(tmp_path) is True


def test_retired_migration_tooling_is_not_reported_as_a_clean_scan(tmp_path):
    assert ci.compat_available(tmp_path) is False


@pytest.mark.parametrize("damage", ["missing", "empty", "malformed", "missing_fields", "orphan"])
def test_partial_or_invalid_tooling_fails_closed(tmp_path, damage):
    manifest = tooling(tmp_path)
    if damage == "missing":
        manifest.unlink()
    elif damage == "empty":
        manifest.write_text('{"entries": []}')
    elif damage == "malformed":
        manifest.write_text("not JSON")
    elif damage == "missing_fields":
        manifest.write_text('{"entries": [{}]}')
    else:
        (tmp_path / "hermes_cli/plugin_compat.py").unlink()
    with pytest.raises(RuntimeError, match="compatibility"):
        ci.compat_available(tmp_path)


def test_official_report_requires_explicit_empty_hits():
    assert ci.check_compat_report('{"plugins": {}, "removal_date": "2026-09-14"}') == {
        "plugins": {}, "removal_date": "2026-09-14",
    }


@pytest.mark.parametrize("report", ['{}', '{"plugins": []}', '{"plugins": {"example": [{}]}}'])
def test_missing_or_affected_report_is_not_accepted(report):
    with pytest.raises(RuntimeError, match="compatibility"):
        ci.check_compat_report(report)
