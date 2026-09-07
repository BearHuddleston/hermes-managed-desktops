# Contributing

Managed Desktops is an independently distributed Linux/KVM CLI with optional native
Hermes integration. Follow the documented [plugin compatibility contract](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins),
Hermes prompt-cache invariants and explicit targeting. Use one focused change per
branch; Conventional Commits use `type(scope): subject`. Read the stock checkout's
AGENTS.md, CONTRIBUTING.md, plugin and skill policies before editing. Python
3.11–3.13 is supported. Do not install host packages, configure the host or operate
live VMs as a side effect of development. Commit/publish only when authorized.

## Independence and scope contracts

- No core patches, monkeypatches or unpublished profile-resource/lifecycle APIs.
  Native registration uses public `PluginContext` methods and captures
  `get_hermes_home()`. Bind callbacks to that home and use `ctx.get_config`; never
  cache mutable settings process-wide or reach into `ctx._manager` in runtime code.
- Standalone `hermes-managed-desktops` / `python -m managed_desktops` must not import
  Hermes. Require explicit `--profile-home PATH` or `--global` before every action;
  never infer global authority or switch scope after refusal. Normal agent skill
  instructions always use explicit profile scope. Global scope requires deliberate
  operator intent and creates unbound guests with package defaults.
- Scoped standalone calls read only namespaced `config.yaml` settings in an existing
  profile. Refuse symlinks, malformed/nonregular configs and unsupported directory
  birth time without exposing unrelated config or secrets.
- Store VM state outside profiles at `$XDG_STATE_HOME/hermes-managed-desktops` or
  `<OS account home>/.local/state/hermes-managed-desktops`. Resolve fallback home
  from the Linux account database, never ambient `HOME` (Hermes can isolate that
  inside a profile). Explicit XDG state must be absolute and outside profiles and
  known Hermes roots; recovery must use the same selected store. Names are unique
  across that store. Schema-2 metadata binds an optional physical profile directory identity
  (canonical path/device/inode/birth time); never copy an authority token into profiles.
- Stock profile clone/import into new directories, rename and deletion/recreation
  must not inherit bindings. An in-place restore that preserves the directory
  **retains access**; directory identity is not content identity. Operators should
  stop/unbind before repurposing/restoring a profile slot for a different owner.
- Stock Hermes permits profile rename/delete. External VMs survive and may still
  run; binding becomes stale. Disable/uninstall removes discovery surfaces, not VMs.
  Explicit standalone global recovery remains possible without Hermes. New scoped
  calls fail closed, but in-flight operations are not synchronously revoked. The
  same-UID OS user is the real owner, not a sandboxed native plugin/profile.
- Rebind/unbind require a stopped VM and exact immutable UUID confirmation. Removal
  retains exact NAME confirmation. Invalid inventory data must not authorize cleanup.
  Legacy 0.1.0 profile-owned roots (including imports) remain untouched; nonempty
  roots block binding. No automatic adoption/migration; retain the old version and
  old harness for cleanup until an explicit migration is provided.
- Stock Hermes preprocesses guest argv. Native `exec`, `app`, `cua` must remain
  refusal-only and direct callers to scoped standalone commands. Do not claim
  opaque argv is supported natively or patch core during installation. Native
  management/capture/transfers/recording/viewing remain supported.

## Canonical tests on exact stock Hermes

The pinned compatibility target is unmodified stock Hermes
`6178e9f4eed8d99f4fc550add939d58c7bed6206`. Keep its checkout **outside this plugin
source tree**; nesting it here changes skill/package discovery. No KVM or guest
image download is needed for the automated suite.

```bash
HERMES_PYTHON=/absolute/path/to/test-venv/bin/python \
  scripts/run_tests.sh /absolute/path/to/stock-hermes-checkout --file-retries 0 -q
```

The wrapper delegates to the stock canonical `scripts/run_tests.sh`, with `-c` and
`--rootdir` pointing to core, explicit `-p tests.conftest` for collection-time home
and credential isolation, and `--import-mode=importlib`. Core's `tests` package
must remain importable before external collection. Never use raw pytest or add
`tests/__init__.py` here: it can shadow core fixtures or the directory shim.

The stock runner prefers its `.venv`, then `venv`, then the shared
`$HOME/.hermes/hermes-agent/venv` when pytest is present, before `HERMES_PYTHON`.
Check the interpreter in runner output; a shell variable does not override a
usable earlier venv. CI uses a fresh runner; for local minimal-dependency verification,
use a disposable HOME and a stock checkout without local venvs rather than altering
live installations.

Observed minimal dependencies for this pinned harness are `pytest==9.1.1`,
`python-dotenv==1.2.2`, `rich==14.3.3`, plus plugin dependencies PyYAML and psutil.
CI pins PyYAML 6.0.3 and psutil 7.2.2. The full Hermes dev/provider stack is not
needed; optional unrelated plugins can log missing-dependency diagnostics during
discovery. Do not hide failures of **this** plugin behind those diagnostics.
Reassess dependencies and behavior when deliberately changing the stock SHA.

Tests use real PluginManager discovery, CLI parser attachment and temporary state,
not source-text assertions. Collection/import/help must do no VM work. No automated
test or CI verification may start a VM, download images or control live systemd
units. Fresh-VM acceptance is separate and requires authorized disposable resources.

## Installed-artifact verification

Build sdist and wheel from sdist in a disposable venv using
`python -m build --outdir /absolute/path/to/disposable-dist`. Do not install into
the running Hermes environment. 0.2.0 is local and unreleased; public `main` still
has 0.1.0 and there is no PyPI release. Use a local source install or built wheel,
not a remote-main command presented as installing this code.

Run the CI helper **before installing the wheel** to verify directory discovery:

```bash
/absolute/path/to/test-venv/bin/python scripts/verify_ci.py \
  --core /absolute/path/to/stock-hermes-checkout --source user
```

It verifies the exact clean SHA, creates disposable profiles and external state,
stages the directory, and enables/configures through the real stock CLI. After
installing the local wheel into that disposable interpreter, run it again with
`--source entrypoint`. A directory copy and wheel entry point must not compete in
the directory verification environment. Both runs use neutral working directories
and explicit import paths, not source imports disguised as installed-artifact checks.

For manual profiles, `scripts/verify_install.py` requires distinct `--home` and
`--other-home` with different namespaced CPU settings, an absent VM store under
`--state-home`, and matching `HERMES_HOME` / `XDG_STATE_HOME` in the environment.
Enable the plugin first via `hermes plugins enable managed-desktops
--no-allow-tool-override`. Set `--source user` for copied directories or
`--source entrypoint` for a wheel. It checks actual guest assets, the qualified
read-only skill, originating profile/config/store binding, native opaque-command
refusals, standalone execution with Hermes imports trapped, explicit-scope
refusal, empty inventories, no VM state creation, and isolated unload.

Finally, install **only the wheel and its dependencies** into a fresh Hermes-free
venv and run that interpreter on `scripts/verify_standalone_install.py`. It checks
both console/module launchers without the source tree or Hermes on the import path,
explicit scoped/global empty inventories, missing-scope/target refusals and no VM
state creation. This is packaging/CLI verification, not a KVM runtime claim.

For uncommitted staging, the helper copies files then the CLI enables them;
`hermes plugins install /path` is unsupported and `file://` copies committed history.
Directory-only standalone use needs its containing directory on the import path.
Keep the skill once in `managed_desktops/skills/`, not an official-skill duplicate.
No autochat binding or tablet viewer is part of this package.

## CI and releases

`.github/workflows/package.yml` builds distributions and runs the canonical suite,
directory/wheel discovery and Hermes-free standalone verification on Python
3.11–3.13 against the exact stock SHA. Actions are SHA-pinned, repository permissions
are read-only, and no package publishing/release credentials are used. It starts no
VMs and does not replace fresh-VM acceptance. Report actual local results and pending
remote CI honestly; merely writing the workflow is not a remote pass.

Keep `plugin.yaml` and `pyproject.toml` package versions aligned and supported Python
versions explicit. Publish tags, releases or registry packages only as a separately
authorized step after documenting reproducible installation and verification.
Keep disks, images, seeds, keys and recordings outside Git. Package source/guest
assets only; retain the MIT license and contributor attribution.
