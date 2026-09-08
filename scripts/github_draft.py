#!/usr/bin/env python3
"""Create a draft only; refuse existing releases and verify by immutable API IDs.

Requires authenticated gh. No POST is retried and failed/partial drafts are never
removed automatically. Repository maintainers must not prepare the same version
concurrently outside the workflow's concurrency group.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

import release_bundle as bundle


def api(endpoint, *, payload=None, upload=None):
    command = ['gh', 'api', '--hostname', 'github.com', endpoint]
    data = None
    if payload is not None:
        command += ['--method', 'POST', '--input', '-']
        data = json.dumps(payload).encode()
    elif upload is not None:
        command += ['--method', 'POST', '--input', str(upload),
                    '--header', 'Content-Type: application/octet-stream']
    result = subprocess.run(command, input=data, capture_output=True, check=True, timeout=120)
    return json.loads(result.stdout)


def download(endpoint):
    return subprocess.run(
        ['gh', 'api', '--hostname', 'github.com', endpoint,
         '--header', 'Accept: application/octet-stream'],
        capture_output=True, check=True, timeout=120,
    ).stdout


def require_available(repo, tag, source):
    seen, page = set(), 1
    while True:
        records = api(f'repos/{repo}/releases?per_page=100&page={page}')
        if not isinstance(records, list):
            raise ValueError('Invalid release listing')
        for record in records:
            if (not isinstance(record, dict) or type(record.get('id')) is not int
                    or record['id'] <= 0 or record['id'] in seen
                    or not isinstance(record.get('tag_name'), str)):
                raise ValueError('Invalid or repeated release listing')
            seen.add(record['id'])
            if record['tag_name'] == tag:
                raise ValueError(f"Refusing existing release {record['id']} for {tag}, including drafts")
        if len(records) < 100:
            break
        page += 1
    if bundle.git(source, 'ls-remote', f'https://github.com/{repo}.git',
                  f'refs/tags/{tag}', f'refs/tags/{tag}^{{}}'):
        raise ValueError(f'Refusing existing tag {tag}')


def create(repo, tag, source, dist, notes):
    version = bundle.source_version(source)
    bundle.check_tag(tag, version)
    info = bundle.verify(source, dist, version)
    hashes = {path.name: bundle.sha256(path) for path in dist.iterdir()}
    body = bundle.regular_file(notes).read_text(encoding='utf-8')
    if not body.strip():
        raise ValueError('Release notes must not be empty')
    payload = {
        'tag_name': tag, 'target_commitish': info['source_sha'],
        'name': f'{tag} — experimental', 'body': body,
        'draft': True, 'prerelease': True, 'make_latest': 'false',
        'generate_release_notes': False,
    }
    # Repeat the authenticated lookup immediately before the only creation POST.
    require_available(repo, tag, source)
    created = api(f'repos/{repo}/releases', payload=payload)
    if not isinstance(created, dict):
        raise ValueError('Invalid creation response; inspect GitHub before retrying')
    release_id = created.get('id')
    if type(release_id) is not int or release_id <= 0:
        raise ValueError('Creation did not return a valid release ID; inspect GitHub before retrying')
    print(f"Created draft ID {release_id}: {created.get('html_url')}; verifying uploads", file=sys.stderr, flush=True)
    expected_ids = {}
    for name in sorted(hashes):
        asset = api(f'https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={quote(name)}',
                    upload=dist / name)
        if (not isinstance(asset, dict) or type(asset.get('id')) is not int or asset['id'] <= 0 or asset.get('name') != name
                or asset['id'] in expected_ids.values()):
            raise ValueError(f'Invalid upload response on release {release_id}')
        expected_ids[name] = asset['id']
    # Never resolve a draft by tag: multiple pending releases can share a tag.
    record = api(f'repos/{repo}/releases/{release_id}')
    if not isinstance(record, dict):
        raise ValueError(f'Invalid release {release_id} readback')
    for field in ('tag_name', 'target_commitish', 'name', 'body'):
        if record.get(field) != payload[field]:
            raise ValueError(f'Release {release_id} {field} changed')
    if record.get('id') != release_id or record.get('draft') is not True or record.get('prerelease') is not True:
        raise ValueError(f'Release {release_id} identity or draft flags changed')
    assets = record.get('assets')
    if not isinstance(assets, list) or len(assets) != len(hashes):
        raise ValueError(f'Release {release_id} asset inventory changed')
    seen = set()
    for asset in assets:
        if (not isinstance(asset, dict) or asset.get('name') not in expected_ids
                or asset['name'] in seen or type(asset.get('id')) is not int
                or asset['id'] != expected_ids[asset['name']]):
            raise ValueError(f'Release {release_id} asset identity changed')
        seen.add(asset['name'])
        content = download(f"repos/{repo}/releases/assets/{asset['id']}")
        if hashlib.sha256(content).hexdigest() != hashes[asset['name']]:
            raise ValueError(f"Release {release_id} uploaded bytes differ: {asset['name']}")
    return {'id': release_id, 'url': record['html_url'], 'source_sha': info['source_sha'],
            'tag': tag, 'draft': True, 'prerelease': True, 'assets': sorted(seen)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check-available', 'create'))
    parser.add_argument('--repo', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--dist', type=Path)
    parser.add_argument('--notes', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*', args.repo):
        parser.error('Expected literal GitHub OWNER/REPO')
    if args.command == 'create' and (args.dist is None or args.notes is None):
        parser.error('create requires --dist and --notes')
    try:
        source = args.source.resolve(strict=True)
        bundle.check_tag(args.tag, bundle.source_version(source))
        if args.command == 'check-available':
            require_available(args.repo, args.tag, source)
            result = {'repository': args.repo, 'available_tag': args.tag}
        else:
            result = create(args.repo, args.tag, source, args.dist, args.notes)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'github_draft: {exc}; inspect any existing draft before retrying', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
