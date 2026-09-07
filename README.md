# Managed Desktops for Hermes

Named Debian/KVM desktops with an independent CLI and optional native Hermes
integration. `hermes-managed-desktops` (or `python -m managed_desktops`) works
without Hermes installed. Enabling the plugin adds `hermes desktop-vm` and the
explicitly loaded, read-only skill `managed-desktops:managed-agent-desktops`.
It adds no model tool, patches no core files, and never retargets normal
`computer_use`, binds autochat, or provides a tablet viewer.

> **0.2.0 is an unreleased local-source preview, not a PyPI release.** The public
> `main` still contains 0.1.0; a remote Git/pip install of `main` does not install
> this independent lifecycle. Use the local 0.2.0 checkout or a wheel built from it.

Source and issues: [BearHuddleston/hermes-managed-desktops](https://github.com/BearHuddleston/hermes-managed-desktops).

## Compatibility

Python 3.11–3.13 is supported. The standalone package depends directly on PyYAML
and psutil, not Hermes. VM operations require Linux x86_64, accessible KVM, QEMU,
OVMF, cloud-image-utils, OpenSSH and a user systemd session. Profile binding also
requires a filesystem with stable directory birth time and a compatible `stat`
command; it fails closed otherwise. Explicit operator `--global` management
remains usable without profile binding. Imports and help do not probe VM dependencies;
`preflight` reports missing host dependencies without installing anything.

Native integration uses stock Hermes's documented plugin APIs, verified against
[`6178e9f4eed8d99f4fc550add939d58c7bed6206`](https://github.com/NousResearch/hermes-agent/tree/6178e9f4eed8d99f4fc550add939d58c7bed6206).
No profile-resource API, lifecycle guard, or core argv patch is required.
Stock Hermes preprocesses some arguments before plugins receive them. Therefore
native `exec`, `app`, and `cua` are **refusal-only routes**: use the standalone CLI
for those commands, including guest `-c`, `-r`, or `--resume` arguments after `--`.
Native management, capture, transfers, recording and loopback viewing are supported.
See the official [plugin contract](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
and [profile guide](https://hermes-agent.nousresearch.com/docs/user-guide/profiles).

## Install from local source

Use a disposable venv for development, not the running agent's environment. Choose
absolute paths; these examples do not install or update Hermes:

```bash
PLUGIN=/absolute/path/to/local-0.2.0-checkout
SANDBOX=/absolute/path/to/new-disposable-artifacts
python3 -m venv "$SANDBOX/venv"
PY="$SANDBOX/venv/bin/python"
"$PY" -m pip install 'build==1.2.2.post1'
"$PY" -m build "$PLUGIN" --outdir "$SANDBOX/dist"
"$PY" -m pip install "$SANDBOX/dist/hermes_managed_desktops-0.2.0-py3-none-any.whl"
"$SANDBOX/venv/bin/hermes-managed-desktops" --help
```

The build produces an sdist and a wheel from that sdist. Installing the wheel
resolves its declared dependencies. Do not use `--no-deps` unless they are already
installed. An editable install, `"$PY" -m pip install -e "$PLUGIN"`, is also a
local-source option. The wheel includes guest assets, the single bundled skill,
the standalone console script and the `hermes_agent.plugins` module entry point.

For optional native integration, install that local wheel into the intended
Hermes interpreter, then enable it in the explicitly selected profile:

```bash
HERMES_PY=/absolute/path/to/disposable-hermes-environment/bin/python
PROFILE_HOME=/absolute/path/to/disposable-profile
"$HERMES_PY" -m pip install "$SANDBOX/dist/hermes_managed_desktops-0.2.0-py3-none-any.whl"
HERMES_HOME="$PROFILE_HOME" "$HERMES_PY" -m hermes_cli.main plugins enable managed-desktops --no-allow-tool-override
HERMES_HOME="$PROFILE_HOME" "$HERMES_PY" -m hermes_cli.main desktop-vm --help
```

That interpreter must already have Hermes and its dependencies available. A venv
created from another venv does not inherit its packages. If using a stock source
checkout instead of an installed Hermes package, give native commands an explicit
`PYTHONPATH=/absolute/path/to/stock-hermes-checkout`. Standalone commands need no
Hermes import path. Enabling trusted native plugins grants same-UID host privileges;
it is not a security sandbox.

### Directory staging instead of a wheel

For uncommitted files, use the existing copy helper:

```bash
"$HERMES_PY" "$PLUGIN/scripts/stage_plugin.py" "$PROFILE_HOME"
HERMES_HOME="$PROFILE_HOME" "$HERMES_PY" -m hermes_cli.main plugins enable managed-desktops --no-allow-tool-override
```

It copies `__init__.py`, `plugin.yaml`, `LICENSE` and `managed_desktops/` into
`<profile home>/plugins/managed-desktops/`, refuses overwrites, and does not enable
the plugin itself. Install PyYAML/psutil in the interpreter used to run the copied
package. Use directory staging **or** wheel discovery in a profile, not both.
`hermes plugins install /path` is unsupported; `file://` installs committed Git
history, not uncommitted working files.

A directory copy does not create a console script. To preserve arbitrary guest
argv, use an installed standalone CLI or run the copied module **from its containing
directory** (not from inside `managed_desktops/`):

```bash
(cd "$PROFILE_HOME/plugins/managed-desktops" && \
  "$HERMES_PY" -m managed_desktops --profile-home "$PROFILE_HOME" list)
```

Alternatively set `PYTHONPATH` to that containing directory for `python -m
managed_desktops`. Keep a standalone installation outside the profile if you need
recovery after the profile or plugin directory is deleted.

## Explicit scope and configuration

Every standalone action requires `--profile-home PATH` **or** `--global`, before
the action. There is no ambient `HERMES_HOME` selection or automatic global fallback.
Normal agent work must always use explicit `--profile-home`:

```bash
PROFILE_HOME=/absolute/path/to/intended-existing-profile
hermes-managed-desktops --profile-home "$PROFILE_HOME" list
hermes-managed-desktops --profile-home "$PROFILE_HOME" preflight
```

The profile must exist, belong to the current user, and contain a regular, valid
`config.yaml` mapping. Symlinks, malformed YAML, nonregular files and invalid
settings are refused without reading unrelated secret files. Scoped standalone
commands read only `plugins.entries.managed-desktops.settings`:

```yaml
plugins:
  enabled: [managed-desktops]
  entries:
    managed-desktops:
      settings:
        cpus: 4
        memory_mib: 4096
        disk_gib: 32
        ready_timeout: 1200
        ovmf_code: ""
        ovmf_vars: ""
```

For a disposable standalone-only profile, an existing directory with a regular
`config.yaml` containing `{}` suffices; Hermes need not be installed. Native
registration captures `get_hermes_home()` and binds callbacks to that home while
reading through `ctx.get_config`. Explicit sizing overrides creation defaults;
settings are fresh and invocation-local, not live resizing or global mutable state.

Load `skill_view(name="managed-desktops:managed-agent-desktops")` in Hermes.
There is no official optional-skill copy. The packaged skill is read-only to
`skill_manage` and is unloaded with the plugin.

## Independent state and lifecycle

State lives in `$XDG_STATE_HOME/hermes-managed-desktops`, or
`<OS account home>/.local/state/hermes-managed-desktops` when unset. The fallback
uses the Linux account database, not ambient `HOME` (Hermes can isolate subprocess
HOME inside a profile). Choose an absolute XDG state base outside profiles and known
Hermes roots, with no overlapping roots; use that same store for recovery. VM names
are unique **across profiles within the selected store**, not per-profile. Disks, credentials, artifacts and
shared image cache stay outside Hermes homes.

Schema-2 metadata optionally binds a VM to a canonical profile directory path,
device, inode and birth time. No authority token is written into profiles:
clone/export/import or copying a profile carries no authority over the original
VM. Scoped commands only see/use matching bindings. Binding identifies the physical
directory, not its contents: an in-place restore that preserves that directory
retains access. Before repurposing/restoring a profile slot for a different intended
owner, the operator should stop and unbind its VMs. The same OS user remains the
real owner; this is not a separate-user security boundary.

**Stock Hermes permits profile rename/delete.** These operations do not stop or
remove external VMs; the guest may still be running and its binding becomes stale.
Plugin disable/uninstall likewise removes discovery surfaces, not VMs, disks or
systemd units. New scoped calls refuse stale bindings. Active in-flight operations
are not synchronously revoked. These are cooperative targeting safeguards, not
protection against malicious native plugins or another process with the same UID.

Explicit operator `--global` can inspect, stop, rebind, unbind or remove a VM even
without Hermes or the plugin installed, provided the standalone package remains
available. It is a deliberate recovery scope requiring user intent, **never an
agent's automatic retry after scoped refusal**. Global creation makes an unbound
VM using package defaults. Rebind/unbind require a stopped VM and `--confirm` with
its exact immutable UUID; removal retains exact **NAME** confirmation. See the
[workflow](docs/workflow.md#profile-lifecycle-and-operator-recovery) for commands.

**Legacy 0.1.0:** existing profile-owned
`managed-resources/managed-desktops/resources/` data is untouched. A nonempty legacy
root blocks profile binding. There is no automatic migration/adoption: stop/remove
old VMs using 0.1.0 and its old prerequisite harness, or retain that version until
an explicit migration exists. Do not delete live disks to bypass the refusal.

## Guest safety and development

Follow [the workflow](docs/workflow.md) for provisioning, explicit VM/screen
targeting, readiness, transfer, capture, recording, viewing and removal. NAT creation
requires explicit acceptance of internet and possible host/LAN access. After
provisioning, a stopped VM can switch to QEMU `restrict=on` isolated networking.
Two screens share one guest account/filesystem, not two security tenants. No host
mounts, provider keys, SSH agents or browser profiles are copied. Failures never
fall back to host desktop control; no host desktop settings are changed.

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the canonical suite against stock Hermes:

```bash
HERMES_PYTHON=/absolute/path/to/test-venv/bin/python \
  scripts/run_tests.sh /absolute/path/to/stock-hermes-checkout --file-retries 0 -q
```

CI builds distributions, runs this suite and verifies directory/wheel discovery
and Hermes-free standalone use on Python 3.11–3.13 against the exact stock SHA
above. SHA-pinned actions have read-only repository permissions; no release or
registry publishing is configured. CI does **not** start KVM guests or establish
fresh-VM acceptance. That requires separately authorized disposable resources.
