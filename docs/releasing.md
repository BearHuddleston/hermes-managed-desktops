# Preparing an experimental GitHub release

Releases are maintainer-triggered **draft prereleases**. There is no automatic
publication, tag-push trigger, PyPI upload, signing service or deployment secret.
A draft is visible only to repository collaborators until a maintainer explicitly
publishes it. Publishing a draft or changing an existing release is a separate
operator decision, not a CI side effect.

## Before dispatch

1. Merge the intended changes through normal review. The release workflow must
   exist on `main` before GitHub can dispatch it; a PR containing the workflow does
   not activate it on the default branch.
2. Keep `pyproject.toml` and `plugin.yaml` versions identical. Use a numeric package
   version such as `0.2.0`, a matching tag `v0.2.0`, and version-specific notes at
   `docs/releases/0.2.0.md`. GitHub's prerelease flag is separate from that version.
3. Check the exact source commit, compatibility pin and test results. Existing
   tags/releases are not replaced. Inspect an existing draft instead of rerunning
   preparation to overwrite it.
4. Keep VM images, keys, runtime state and recordings outside the repository and
   release directory. Release preparation does not start or update any guest.

## Dispatch the workflow

With repository write access and the workflow merged:

```bash
gh workflow run release.yml --repo BearHuddleston/hermes-managed-desktops \
  --ref main -f tag=v0.2.0
```

The dispatch is pinned to the `main` commit selected for that run. The workflow:

- Refuses a non-`main` ref, mismatching version, missing notes or existing tag.
  Authenticated release-list pagination also rejects existing drafts, which can
  have a pending tag without creating a Git ref. It repeats that check before the POST.
- Calls the same read-only CI workflow as PRs: Python/workflow lint plus the full
  Python 3.11–3.13 stock-Hermes integration and packaging matrix.
- Downloads the tested Python 3.11 artifact from **that same workflow run**.
  It does not select a moving "latest successful" build or rebuild under the write token.
- Verifies source SHA, package/manifest/archive versions, required license notices,
  exact bundle contents and checksums.
- Grants `contents: write` only to the final draft job, which calls GitHub with
  explicit draft/prerelease flags and the full tested source SHA.
- Reads the newly created release by its returned ID, never an ambiguous tag
  lookup. It downloads assets by ID and compares their bytes with the verified
  local bundle, checking the draft's source SHA, flags, notes and inventory too.

A successful workflow means a verified draft was prepared, **not published**.
If it fails after creation, inspect the existing draft and run logs; do not delete
or recreate a release blindly. The source pin remains recorded even if `main`
advances while the checks run.

Workflow concurrency serializes preparation for a tag. GitHub does not offer an
atomic unique-draft reservation: do not create the same version concurrently by
other means. The helper never retries a creation POST or removes a partial draft.

## Bundle contents

| Asset | Purpose |
|---|---|
| `hermes_managed_desktops-0.2.0-py3-none-any.whl` | Installable package |
| `hermes_managed_desktops-0.2.0.tar.gz` | Source distribution used to build the wheel |
| `BUILD_INFO.json` | Package/tag, source Git SHA, pinned Hermes SHA and distribution hashes |
| `SHA256SUMS` | Checksums for both distributions and `BUILD_INFO.json` |

Names use the selected version. Build provenance here is **unsigned metadata**,
not a cryptographic identity attestation. Checksums detect different bytes; they
are not independent proof of who produced them. CI artifact retention is 14 days;
release assets are attached separately to the draft.

For a locally built bundle, use a clean committed checkout and an otherwise empty
output directory outside runtime state. With build tooling already installed in
a disposable Python 3.11–3.13 environment:

```bash
DIST=/absolute/path/to/empty-release-dist
python -m build --outdir "$DIST"
python scripts/release_bundle.py prepare --dist "$DIST"
python scripts/release_bundle.py verify --dist "$DIST" --tag v0.2.0
```

`prepare` checks the build artifacts and writes metadata; it does not run tests or
claim an arbitrary local build passed CI. `--source /absolute/path/to/checkout`
lets maintainers verify an explicitly selected clean source snapshot. In CI, the
bundle is prepared only after all integration steps in its matrix job pass, and
release creation waits for the entire matrix and lint job.

Before public publication, review the exact draft target, all asset checksums,
release notes and CI evidence, and any separately authorized live-VM acceptance.
Do not reuse a version tag for different source or bytes. No publication command
is automated by this repository.
