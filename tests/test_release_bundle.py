"""Release CLI contracts using synthetic archives and disposable Git repositories.

These fixtures are not built distributions or release evidence. No network, live
profile, installed plugin, VM, or repository-under-development Git writes occur.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release_bundle.py"
VERSION = "1.2.3"  # Deliberately independent of the project's current version.
TAG = f"v{VERSION}"
STEM = f"hermes_managed_desktops-{VERSION}"
WHEEL = f"{STEM}-py3-none-any.whl"
SDIST = f"{STEM}.tar.gz"
ASSETS = {WHEEL, SDIST, "BUILD_INFO.json", "SHA256SUMS"}
HERMES_SHA = "1234567890abcdef" * 2 + "12345678"  # Fixture identity, not real stock.


def git(source, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-c", "user.name=Release fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=source, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit_fixture(source):
    git(source, "add", ".")
    git(source, "commit", "-qm", "Temporary release test fixture")
    return git(source, "rev-parse", "HEAD")


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "hermes-managed-desktops"\nversion = "{VERSION}"\n',
        encoding="utf-8",
    )
    (root / "plugin.yaml").write_text(f'name: managed-desktops\nversion: "{VERSION}"\n', encoding="utf-8")
    # Importing/executing this file must fail; reading its AST literal is safe.
    (root / "scripts/verify_ci.py").write_text(
        f'STOCK_HERMES_SHA = "{HERMES_SHA}"\nraise RuntimeError("must not execute")\n',
        encoding="utf-8",
    )
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    git(root, "init", "-q")
    commit_fixture(root)
    return root


def metadata(version=VERSION, name="hermes-managed-desktops"):
    return f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\nFixture only.\n".encode()


def write_wheel(dist, *, version=VERSION, name="hermes-managed-desktops", license=True):
    with zipfile.ZipFile(dist / WHEEL, "w") as archive:
        archive.writestr(f"{STEM}.dist-info/METADATA", metadata(version, name))
        if license:
            archive.writestr(f"{STEM}.dist-info/licenses/LICENSE", "Fixture license\n")


def write_sdist(dist, *, version=VERSION, name="hermes-managed-desktops", license=True):
    with tarfile.open(dist / SDIST, "w:gz") as archive:
        files = {f"{STEM}/PKG-INFO": metadata(version, name)}
        if license:
            files[f"{STEM}/LICENSE"] = b"Fixture license\n"
        for path, content in files.items():
            member = tarfile.TarInfo(path)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    write_wheel(root)
    write_sdist(root)
    return root


def invoke(source, command, *args, script=SCRIPT):
    return subprocess.run(
        [sys.executable, "-I", str(script), command, "--source", str(source), *map(str, args)],
        cwd=source.parent, capture_output=True, text=True, timeout=20,
    )


def succeeded(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def refused(result, message):
    assert result.returncode == 1, result.stderr
    assert message.lower() in result.stderr.lower()
    assert "Traceback" not in result.stderr


def prepare(source, dist):
    return succeeded(invoke(source, "prepare", "--dist", dist))


def verify(source, dist, *args):
    return invoke(source, "verify", "--dist", dist, "--tag", TAG, *args)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_sums(dist):
    """Model an attacker updating unsigned sums; identity still must be checked."""
    (dist / "SHA256SUMS").write_text(
        "".join(f"{digest(dist / name)}  {name}\n" for name in sorted(ASSETS - {"SHA256SUMS"})),
        encoding="utf-8",
    )


def release_record(source):
    return {
        "tagName": TAG, "targetCommitish": git(source, "rev-parse", "HEAD"),
        "isDraft": True, "isPrerelease": True,
        "url": f"https://example.invalid/releases/tag/{TAG}",
        "assets": [{"name": name} for name in sorted(ASSETS)],
    }


def test_release_bundle_round_trip_records_real_git_identity_and_hashes(source, dist, tmp_path):
    assert succeeded(invoke(source, "check-tag", "--tag", TAG)) == {"version": VERSION, "tag": TAG}
    report = prepare(source, dist)
    build = json.loads((dist / "BUILD_INFO.json").read_text(encoding="utf-8"))
    assert report == build
    assert build == {
        "schema": 1, "version": VERSION, "tag": TAG,
        "source_sha": git(source, "rev-parse", "HEAD"), "hermes_sha": HERMES_SHA,
        "distributions": {name: digest(dist / name) for name in (WHEEL, SDIST)},
    }
    assert {item.name for item in dist.iterdir()} == ASSETS
    sums = (dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert dict(line.split("  ", 1)[::-1] for line in sums) == {
        name: digest(dist / name) for name in ASSETS - {"SHA256SUMS"}
    }
    before = {path.name: path.read_bytes() for path in dist.iterdir()}
    assert succeeded(verify(source, dist)) == build
    record = tmp_path / "release.json"
    record.write_text(json.dumps(release_record(source)), encoding="utf-8")
    assert succeeded(verify(source, dist, "--release-record", record)) == build
    assert {path.name: path.read_bytes() for path in dist.iterdir()} == before
    assert git(source, "status", "--porcelain", "--untracked-files=all") == ""


@pytest.mark.parametrize("tag", ["1.2.3", "V1.2.3", "v1.2", "v01.2.3", "v1.2.3rc1", "v1.2.3+local", "v1.2.3\n", "v1.2.4"])
def test_release_bundle_tag_is_literal_and_exact(source, tag):
    refused(invoke(source, "check-tag", "--tag", tag), "tag")


@pytest.mark.parametrize("version", ["1.2.4", "01.2.3", "1.2", "1.2.3rc1"])
def test_release_bundle_source_versions_must_agree_without_normalization(source, dist, version):
    (source / "plugin.yaml").write_text(f'version: "{version}"\n', encoding="utf-8")
    commit_fixture(source)
    refused(invoke(source, "prepare", "--dist", dist), "version")
    assert not (dist / "BUILD_INFO.json").exists()


@pytest.mark.parametrize("writer", [write_wheel, write_sdist])
@pytest.mark.parametrize("changes,diagnostic", [({"version": "1.2.4"}, "version"), ({"name": "other-project"}, "name"), ({"license": False}, "LICENSE")])
def test_release_bundle_validates_packaged_metadata_and_license(source, dist, writer, changes, diagnostic):
    writer(dist, **changes)
    refused(invoke(source, "prepare", "--dist", dist), diagnostic)
    assert not (dist / "BUILD_INFO.json").exists()


@pytest.mark.parametrize("asset", sorted(ASSETS))
@pytest.mark.parametrize("change", ["tamper", "missing", "symlink", "directory"])
def test_release_bundle_rejects_altered_or_nonregular_artifacts(source, dist, tmp_path, asset, change):
    prepare(source, dist)
    target = dist / asset
    if change == "tamper":
        target.write_bytes(target.read_bytes() + b"tampered")
    elif change == "missing":
        target.unlink()
    elif change == "symlink":
        saved = tmp_path / "saved-artifact"
        target.rename(saved)
        target.symlink_to(saved)
    else:
        target.unlink()
        target.mkdir()
    result = verify(source, dist)
    refused(result, "")


@pytest.mark.parametrize("command", ["prepare", "verify"])
def test_release_bundle_rejects_unexpected_dist_entries(source, dist, command):
    if command == "verify":
        prepare(source, dist)
    (dist / "extra.whl").write_text("unexpected", encoding="utf-8")
    args = ("--tag", TAG) if command == "verify" else ()
    refused(invoke(source, command, "--dist", dist, *args), "unexpected")


@pytest.mark.parametrize("command", ["prepare", "verify"])
@pytest.mark.parametrize("change", ["tracked", "staged", "untracked"])
def test_release_bundle_requires_clean_source_including_untracked(source, dist, command, change):
    if command == "verify":
        prepare(source, dist)
    if change == "tracked":
        (source / ".gitignore").write_text("# changed\n", encoding="utf-8")
    else:
        (source / "untracked.txt").write_text("new", encoding="utf-8")
        if change == "staged":
            git(source, "add", "untracked.txt")
    args = ("--tag", TAG) if command == "verify" else ()
    refused(invoke(source, command, "--dist", dist, *args), "clean")


def test_release_bundle_ignored_files_do_not_make_source_dirty(source, dist):
    (source / "ignored").mkdir()
    (source / "ignored/output").write_text("ignored", encoding="utf-8")
    prepare(source, dist)
    succeeded(verify(source, dist))


def test_release_bundle_checks_current_head_not_just_version(source, dist):
    prepare(source, dist)
    (source / "new.txt").write_text("different source commit", encoding="utf-8")
    commit_fixture(source)
    refused(verify(source, dist), "BUILD_INFO")


@pytest.mark.parametrize("field,value", [("source_sha", "0" * 40), ("hermes_sha", "f" * 40), ("version", "1.2.4"), ("tag", "v1.2.4"), ("schema", 2), ("schema", True)])
def test_release_bundle_rechecks_identity_even_with_updated_checksums(source, dist, field, value):
    prepare(source, dist)
    path = dist / "BUILD_INFO.json"
    build = json.loads(path.read_text(encoding="utf-8"))
    build[field] = value
    path.write_text(json.dumps(build), encoding="utf-8")
    refresh_sums(dist)
    refused(verify(source, dist), "BUILD_INFO")


@pytest.mark.parametrize("field,value", [
    ("tagName", "v1.2.4"), ("targetCommitish", "main"), ("targetCommitish", "0" * 40),
    ("isDraft", False), ("isPrerelease", False), ("isDraft", 1), ("isPrerelease", "true"),
    ("assets", []), ("assets", [{"name": name} for name in sorted(ASSETS)] * 2),
    ("assets", [{"name": name} for name in sorted(ASSETS | {"extra.txt"})]),
])
def test_release_bundle_release_record_requires_exact_draft_prerelease(source, dist, tmp_path, field, value):
    prepare(source, dist)
    record = release_record(source)
    record[field] = value
    path = tmp_path / "release.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    refused(verify(source, dist, "--release-record", path), field)


def test_release_bundle_source_default_is_script_parent_not_cwd(source):
    staged_script = source / "scripts/release_bundle.py"
    shutil.copy2(SCRIPT, staged_script)
    result = subprocess.run(
        [sys.executable, "-I", str(staged_script), "check-tag", "--tag", TAG],
        cwd=source.parent, capture_output=True, text=True, timeout=20,
    )
    assert succeeded(result) == {"version": VERSION, "tag": TAG}


@pytest.mark.parametrize("declaration", [
    "", 'STOCK_HERMES_SHA = "short"', 'STOCK_HERMES_SHA = "a" * 40',
    "STOCK_HERMES_SHA: str", f'STOCK_HERMES_SHA = "{HERMES_SHA}"\nSTOCK_HERMES_SHA = "{HERMES_SHA}"',
])
def test_release_bundle_stock_pin_requires_one_full_literal(source, dist, declaration):
    (source / "scripts/verify_ci.py").write_text(declaration + "\n", encoding="utf-8")
    commit_fixture(source)
    refused(invoke(source, "prepare", "--dist", dist), "STOCK_HERMES_SHA")


def test_release_bundle_annotated_literal_pin_is_supported_without_execution(source, dist):
    (source / "scripts/verify_ci.py").write_text(
        f'STOCK_HERMES_SHA: str = "{HERMES_SHA}"\nraise RuntimeError("not executable")\n',
        encoding="utf-8",
    )
    commit_fixture(source)
    assert prepare(source, dist)["hermes_sha"] == HERMES_SHA


@pytest.mark.parametrize("writer,asset", [(write_wheel, WHEEL), (write_sdist, SDIST)])
def test_release_bundle_revalidates_metadata_even_if_inventory_and_sums_are_updated(source, dist, writer, asset):
    prepare(source, dist)
    writer(dist, version="1.2.4")
    path = dist / "BUILD_INFO.json"
    build = json.loads(path.read_text(encoding="utf-8"))
    build["distributions"][asset] = digest(dist / asset)
    path.write_text(json.dumps(build), encoding="utf-8")
    refresh_sums(dist)
    refused(verify(source, dist), "version")


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra"])
def test_release_bundle_checksums_must_cover_exactly_three_files(source, dist, change):
    prepare(source, dist)
    path = dist / "SHA256SUMS"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if change == "missing":
        lines.pop()
    elif change == "duplicate":
        lines.append(lines[0])
    else:
        lines.append("0" * 64 + "  extra.txt\n")
    path.write_text("".join(lines), encoding="utf-8")
    refused(verify(source, dist), "SHA256SUMS")


def test_release_bundle_prepare_does_not_overwrite_an_existing_inventory(source, dist):
    prepare(source, dist)
    before = {path.name: path.read_bytes() for path in dist.iterdir()}
    refused(invoke(source, "prepare", "--dist", dist), "unexpected")
    assert {path.name: path.read_bytes() for path in dist.iterdir()} == before


def test_release_bundle_does_not_follow_dist_directory_symlink(source, dist, tmp_path):
    linked = tmp_path / "linked"
    linked.symlink_to(dist, target_is_directory=True)
    refused(invoke(source, "prepare", "--dist", linked), "symlink")
    assert not (dist / "BUILD_INFO.json").exists()


def test_release_bundle_uses_actual_source_despite_ambient_git_override(source, dist, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-repository"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    assert prepare(source, dist)["source_sha"] == git(source, "rev-parse", "HEAD")


@pytest.mark.parametrize("archive_type", ["wheel", "sdist"])
@pytest.mark.parametrize("change", ["symlink", "traversal"])
def test_release_bundle_refuses_unsafe_archive_members_without_extraction(source, dist, tmp_path, archive_type, change):
    if archive_type == "wheel":
        with zipfile.ZipFile(dist / WHEEL, "a") as archive:
            member = zipfile.ZipInfo("linked" if change == "symlink" else "../escaped")
            member.create_system = 3
            member.external_attr = (0o120777 if change == "symlink" else 0o100644) << 16
            archive.writestr(member, "outside")
    else:
        # Rewrite only this synthetic fixture; tar.gz has no append mode.
        with tarfile.open(dist / SDIST, "w:gz") as archive:
            for name, content in ((f"{STEM}/PKG-INFO", metadata()), (f"{STEM}/LICENSE", b"fixture")):
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            member = tarfile.TarInfo(f"{STEM}/linked" if change == "symlink" else "../escaped")
            if change == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
            archive.addfile(member)
    refused(invoke(source, "prepare", "--dist", dist), "member")
    assert not (tmp_path / "escaped").exists()
    assert not (dist / STEM).exists()


@pytest.mark.parametrize("field", ["tagName", "targetCommitish", "isDraft", "isPrerelease", "assets"])
def test_release_bundle_missing_release_record_fields_fail_closed(source, dist, tmp_path, field):
    prepare(source, dist)
    record = release_record(source)
    del record[field]
    path = tmp_path / "release.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    refused(verify(source, dist, "--release-record", path), field)
