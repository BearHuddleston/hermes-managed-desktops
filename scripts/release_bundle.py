#!/usr/bin/env python3
"""Prepare/check an unsigned release inventory; never publish or claim attestation.

Only stdlib and PyYAML are needed. Archives are inspected, never extracted. Use
an external or Git-ignored dist directory: prepare/verify require clean source.
Prepare accepts only the two distributions (no existing sidecars); verify accepts
exactly those distributions plus BUILD_INFO.json and SHA256SUMS.
"""

import argparse
import ast
from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import yaml


PROJECT = "hermes-managed-desktops"
DIST_NAME = "hermes_managed_desktops"
VERSION_PATTERN = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
SHA_PATTERN = r"[0-9a-f]{40}"
SIDECARS = {"BUILD_INFO.json", "SHA256SUMS"}


def regular_file(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Expected regular file, not symlink or special file: {path}")
    return path


def source_version(source):
    project = tomllib.loads(regular_file(source / "pyproject.toml").read_text(encoding="utf-8")).get("project")
    plugin = yaml.safe_load(regular_file(source / "plugin.yaml").read_text(encoding="utf-8"))
    if not isinstance(project, dict) or project.get("name") != PROJECT:
        raise ValueError(f"pyproject.toml project name must be {PROJECT}")
    version = project.get("version")
    if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError("Source version must be literal numeric X.Y.Z without leading zeroes")
    if not isinstance(plugin, dict) or plugin.get("version") != version:
        raise ValueError("pyproject.toml and plugin.yaml version mismatch")
    return version


def check_tag(tag, version):
    if not re.fullmatch("v" + VERSION_PATTERN, tag) or tag != "v" + version:
        raise ValueError(f"Tag must be exactly v{version}; got {tag!r}")


def git(source, *args):
    # Ambient Git overrides must not redirect identity checks to another tree.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(source), *args], env=env,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise ValueError(f"Git source check failed: {result.stderr.strip()}")
    return result.stdout.strip()


def source_identity(source):
    if Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("Source must be the Git worktree root")
    head = git(source, "rev-parse", "--verify", "HEAD^{commit}")
    if not re.fullmatch(SHA_PATTERN, head):
        raise ValueError("Source HEAD must be a full 40-character Git SHA")
    if git(source, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"):
        raise ValueError("Source Git tree must be clean, including nonignored untracked files")

    path = regular_file(source / "scripts/verify_ci.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == "STOCK_HERMES_SHA" for target in targets):
            if node.value is None:
                raise ValueError("STOCK_HERMES_SHA must have a literal value")
            try:
                values.append(ast.literal_eval(node.value))
            except ValueError as exc:
                raise ValueError("STOCK_HERMES_SHA must be a literal full Git SHA") from exc
    if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(SHA_PATTERN, values[0]):
        raise ValueError("verify_ci.py must declare one literal full STOCK_HERMES_SHA")
    return head, values[0]


def distribution_names(version):
    stem = f"{DIST_NAME}-{version}"
    return {f"{stem}-py3-none-any.whl", f"{stem}.tar.gz"}


def check_dist(dist, expected):
    if not stat.S_ISDIR(dist.lstat().st_mode):
        raise ValueError("Dist must be a directory, not a symlink or special file")
    entries = {path.name: path for path in dist.iterdir()}
    if entries.keys() != expected:
        raise ValueError(
            f"Dist files mismatch: missing {sorted(expected - entries.keys())}; "
            f"unexpected {sorted(entries.keys() - expected)}"
        )
    for path in entries.values():
        regular_file(path)


def member_name(name, seen):
    clean = name.rstrip("/")
    if not clean or "\\" in name or "\x00" in name or any(part in ("", ".", "..") for part in clean.split("/")):
        raise ValueError(f"Unsafe archive member: {name!r}")
    if clean in seen:
        raise ValueError(f"Duplicate archive member: {name!r}")
    seen.add(clean)
    return clean


def check_metadata(content, version, label):
    metadata = BytesParser(policy=policy.default).parsebytes(content)
    if metadata.defects:
        raise ValueError(f"Malformed metadata in {label}")
    for field, expected in (("Name", PROJECT), ("Version", version)):
        if metadata.get_all(field) != [expected]:
            raise ValueError(f"{label} {field} must be exactly {expected}")


def check_wheel(path, version):
    prefix = f"{DIST_NAME}-{version}.dist-info/"
    with zipfile.ZipFile(path) as archive:
        files, seen = {}, set()
        for member in archive.infolist():
            name = member_name(member.filename, seen)
            kind = stat.S_IFMT(member.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError(f"Nonregular wheel member: {name}")
            if not member.is_dir():
                if kind == stat.S_IFDIR:
                    raise ValueError(f"Invalid wheel directory: {name}")
                files[name] = member
        metadata_path = prefix + "METADATA"
        metadata_files = {name for name in files if name.endswith(".dist-info/METADATA")}
        if metadata_files != {metadata_path}:
            raise ValueError("Wheel must contain exactly the versioned dist-info/METADATA")
        if not {prefix + "licenses/LICENSE", prefix + "LICENSE"} & files.keys():
            raise ValueError("Wheel is missing packaged LICENSE")
        check_metadata(archive.read(files[metadata_path]), version, "Wheel METADATA")
        if archive.testzip() is not None:
            raise ValueError("Wheel contains corrupt file data")


def check_sdist(path, version):
    prefix = f"{DIST_NAME}-{version}/"
    with tarfile.open(path, "r:gz") as archive:
        files, seen = {}, set()
        for member in archive:
            name = member_name(member.name, seen)
            if not member.isfile() and not member.isdir():
                raise ValueError(f"Nonregular sdist member: {name}")
            if name != prefix.rstrip("/") and not name.startswith(prefix):
                raise ValueError(f"Unexpected sdist root: {name}")
            if member.isfile():
                files[name] = member
        if prefix + "PKG-INFO" not in files:
            raise ValueError("Sdist is missing versioned PKG-INFO")
        if prefix + "LICENSE" not in files:
            raise ValueError("Sdist is missing packaged LICENSE")
        metadata = archive.extractfile(files[prefix + "PKG-INFO"])
        if metadata is None:
            raise ValueError("Sdist PKG-INFO must be a regular file")
        with metadata:
            check_metadata(metadata.read(), version, "Sdist PKG-INFO")


def sha256(path):
    with regular_file(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_info(source, dist, version, *, prepared):
    head, hermes_sha = source_identity(source)
    names = distribution_names(version)
    check_dist(dist, names | SIDECARS if prepared else names)
    check_wheel(dist / f"{DIST_NAME}-{version}-py3-none-any.whl", version)
    check_sdist(dist / f"{DIST_NAME}-{version}.tar.gz", version)
    return {
        "schema": 1, "version": version, "tag": "v" + version,
        "source_sha": head, "hermes_sha": hermes_sha,
        "distributions": {name: sha256(dist / name) for name in sorted(names)},
    }


def checksum_text(hashes):
    return "".join(f"{hashes[name]}  {name}\n" for name in sorted(hashes))


def prepare(source, dist, version):
    info = build_info(source, dist, version, prepared=False)
    # Exclusive creation avoids silently blessing changes in an existing bundle.
    with (dist / "BUILD_INFO.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(info, indent=2, sort_keys=True) + "\n")
    hashes = {**info["distributions"], "BUILD_INFO.json": sha256(dist / "BUILD_INFO.json")}
    with (dist / "SHA256SUMS").open("x", encoding="utf-8") as stream:
        stream.write(checksum_text(hashes))
    return info


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(regular_file(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def check_release_record(path, info):
    record = read_json(path)
    if not isinstance(record, dict):
        raise ValueError("Release record must be a JSON object")
    for field, expected in (("tagName", info["tag"]), ("targetCommitish", info["source_sha"])):
        if record.get(field) != expected:
            raise ValueError(f"Release record {field} must be exactly {expected}")
    for field in ("isDraft", "isPrerelease"):
        if record.get(field) is not True:
            raise ValueError(f"Release record {field} must be true")
    assets = record.get("assets")
    if not isinstance(assets, list) or any(not isinstance(asset, dict) or not isinstance(asset.get("name"), str) for asset in assets):
        raise ValueError("Release record assets must be objects with names")
    names = [asset["name"] for asset in assets]
    expected = distribution_names(info["version"]) | SIDECARS
    if len(names) != len(expected) or set(names) != expected:
        raise ValueError(f"Release record assets must be exactly {sorted(expected)}")


def verify(source, dist, version, release_record=None):
    expected = build_info(source, dist, version, prepared=True)
    actual = read_json(dist / "BUILD_INFO.json")
    if not isinstance(actual, dict) or type(actual.get("schema")) is not int or actual != expected:
        raise ValueError("BUILD_INFO.json does not match current source identity and distribution hashes")
    hashes = {}
    for line in (dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if not match or match[2] in hashes:
            raise ValueError("Invalid or duplicate SHA256SUMS entry")
        hashes[match[2]] = match[1]
    expected_hashes = {**expected["distributions"], "BUILD_INFO.json": sha256(dist / "BUILD_INFO.json")}
    if hashes != expected_hashes:
        raise ValueError("SHA256SUMS does not match the distributions and BUILD_INFO.json")
    if release_record is not None:
        check_release_record(release_record, expected)
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check-tag", "prepare", "verify"):
        sub = commands.add_parser(command)
        sub.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
        if command != "prepare":
            sub.add_argument("--tag", required=True)
        if command != "check-tag":
            sub.add_argument("--dist", type=Path, required=True)
        if command == "verify":
            sub.add_argument("--release-record", type=Path, help="JSON file from gh release view; never fetched or changed here")
    args = parser.parse_args(argv)
    try:
        source = args.source.resolve(strict=True)
        version = source_version(source)
        if args.command != "prepare":
            check_tag(args.tag, version)
        if args.command == "check-tag":
            result = {"version": version, "tag": args.tag}
        elif args.command == "prepare":
            result = prepare(source, args.dist, version)
        else:
            result = verify(source, args.dist, version, args.release_record)
    except (OSError, ValueError, SyntaxError, yaml.YAMLError, tarfile.TarError,
            zipfile.BadZipFile, RuntimeError, EOFError, subprocess.TimeoutExpired) as exc:
        print(f"release_bundle: error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
