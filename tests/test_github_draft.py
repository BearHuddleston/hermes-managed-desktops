"""Draft publication boundary tests with a synthetic GitHub transport, no network."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def draft(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('managed_desktops_github_draft', scripts / 'github_draft.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Do not leak the helper's script-directory import into other test files.
    monkeypatch.delitem(sys.modules, 'release_bundle')
    return module


@pytest.mark.parametrize('is_draft', [True, False])
def test_existing_pending_or_published_tag_on_later_page_is_refused(draft, monkeypatch, tmp_path, is_draft):
    pages = [[{'id': i + 1, 'tag_name': f'v0.0.{i}', 'draft': False} for i in range(100)],
             [{'id': 101, 'tag_name': 'v0.2.0', 'draft': is_draft}]]
    calls = []
    def api(endpoint, **kwargs):
        calls.append(endpoint)
        return pages.pop(0)
    monkeypatch.setattr(draft, 'api', api)
    monkeypatch.setattr(draft.bundle, 'git', lambda *args: '')
    with pytest.raises(ValueError, match='existing release 101'):
        draft.require_available('example/repository', 'v0.2.0', tmp_path)
    assert len(calls) == 2 and calls[1].endswith('page=2')


@pytest.mark.parametrize('page', [{}, [{}], [{'id': 1}], [{'id': True, 'tag_name': 'v1.0.0'}]])
def test_invalid_release_listing_fails_closed(draft, monkeypatch, tmp_path, page):
    monkeypatch.setattr(draft, 'api', lambda *args, **kwargs: page)
    with pytest.raises(ValueError, match='release listing'):
        draft.require_available('example/repository', 'v0.2.0', tmp_path)


def test_existing_git_tag_without_release_is_refused(draft, monkeypatch, tmp_path):
    monkeypatch.setattr(draft, 'api', lambda *args, **kwargs: [])
    monkeypatch.setattr(draft.bundle, 'git', lambda *args: 'fixture-sha\trefs/tags/v0.2.0')
    with pytest.raises(ValueError, match='existing tag'):
        draft.require_available('example/repository', 'v0.2.0', tmp_path)


def test_repeated_release_page_fails_closed(draft, monkeypatch, tmp_path):
    page = [{'id': i + 1, 'tag_name': f'v0.0.{i}'} for i in range(100)]
    monkeypatch.setattr(draft, 'api', lambda *args, **kwargs: page)
    with pytest.raises(ValueError, match='release listing'):
        draft.require_available('example/repository', 'v0.2.0', tmp_path)


@pytest.mark.parametrize('tamper', [None, 'download', 'record', 'upload_id'])
def test_create_binds_readback_to_returned_id_and_original_bytes(draft, monkeypatch, tmp_path, tamper):
    dist = tmp_path / 'dist'
    dist.mkdir()
    for name in ('example.whl', 'example.tar.gz', 'BUILD_INFO.json', 'SHA256SUMS'):
        (dist / name).write_bytes(name.encode())
    info = {'source_sha': '1' * 40, 'tag': 'v0.2.0', 'version': '0.2.0'}
    monkeypatch.setattr(draft.bundle, 'source_version', lambda *args: '0.2.0')
    monkeypatch.setattr(draft.bundle, 'verify', lambda *args, **kwargs: info)
    notes = tmp_path / 'notes.md'
    notes.write_text('Fixture release notes.')
    events = []
    monkeypatch.setattr(draft, 'require_available', lambda *args: events.append('availability'))
    payloads, assets, uploaded = [], [], {}
    def api(endpoint, *, payload=None, upload=None):
        events.append(endpoint)
        if payload is not None:
            payloads.append(payload)
            return {'id': 42, 'html_url': 'https://github.com/example/repository/releases/untagged-fixture'}
        if upload is not None:
            asset = {'id': len(assets) + 1, 'name': upload.name}
            assets.append(asset)
            uploaded[asset['id']] = upload.read_bytes()
            return {**asset, 'id': 999} if tamper == 'upload_id' else asset
        assert endpoint == 'repos/example/repository/releases/42'
        return {'id': 42, **payloads[0], 'assets': assets,
                'html_url': 'https://github.com/example/repository/releases/untagged-fixture',
                'draft': False if tamper == 'record' else True}
    monkeypatch.setattr(draft, 'api', api)
    def download(endpoint):
        events.append(endpoint)
        value = uploaded[int(endpoint.rsplit('/', 1)[1])]
        return value + b'changed' if tamper == 'download' else value
    monkeypatch.setattr(draft, 'download', download)
    if tamper:
        with pytest.raises(ValueError):
            draft.create('example/repository', 'v0.2.0', tmp_path, dist, notes)
    else:
        result = draft.create('example/repository', 'v0.2.0', tmp_path, dist, notes)
        assert result['id'] == 42 and result['source_sha'] == info['source_sha']
        assert len([e for e in events if '/releases/assets/' in e]) == 4
    assert events[0] == 'availability'
    assert payloads == [{'tag_name': 'v0.2.0', 'target_commitish': '1' * 40,
                         'name': 'v0.2.0 — experimental', 'body': 'Fixture release notes.',
                         'draft': True, 'prerelease': True, 'make_latest': 'false',
                         'generate_release_notes': False}]
    assert not any('/tags/' in e for e in events)


def test_transport_never_retries_a_failed_post(draft, monkeypatch):
    calls = []
    def fail(argv, **kwargs):
        calls.append(argv)
        raise draft.subprocess.CalledProcessError(1, argv, stderr=b'HTTP 500')
    monkeypatch.setattr(draft.subprocess, 'run', fail)
    with pytest.raises(draft.subprocess.CalledProcessError):
        draft.api('repos/example/repository/releases', payload={'draft': True})
    assert len(calls) == 1


def test_transport_preserves_explicit_json_booleans(draft, monkeypatch):
    def run(argv, **kwargs):
        assert json.loads(kwargs['input']) == {'draft': True, 'prerelease': True}
        assert argv[:4] == ['gh', 'api', '--hostname', 'github.com']
        assert '--input' in argv and 'POST' in argv
        return draft.subprocess.CompletedProcess(argv, 0, b'{"id": 42}', b'')
    monkeypatch.setattr(draft.subprocess, 'run', run)
    assert draft.api('repos/example/repository/releases', payload={'draft': True, 'prerelease': True}) == {'id': 42}
