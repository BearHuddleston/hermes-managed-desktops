# Managed Desktops for Hermes

A standalone native plugin for named Debian/KVM desktops. It adds
`hermes desktop-vm` and the explicitly loaded, read-only skill
`managed-desktops:managed-agent-desktops`. It does not add a model tool, change core
files, retarget normal `computer_use`, bind a VM to autochat, or provide a tablet viewer.

> **Experimental source preview, not a supported release.** VM operations require
> two unpublished Hermes core changes described below. Installing this repository
> alone is not enough to use it safely with stock Hermes.

Source and issue tracking: [BearHuddleston/hermes-managed-desktops](https://github.com/BearHuddleston/hermes-managed-desktops).

## Required core prerequisites

**This plugin currently requires an unpublished generic Hermes core change.**
The Hermes interpreter must provide `hermes_cli.profile_resources.resource_root`
and `reserve_resource`, exclude `managed-resources/` from profile clone/export,
and refuse profile rename/delete while any resource leaf exists. Those protections
must work even when this plugin is disabled or uninstalled. No released Hermes
version is claimed to satisfy this prerequisite. The plugin source is public, but
there is no supported package release or published prerequisite checkout yet.
The core changes are maintained separately for upstream contribution; this plugin
does not patch or upgrade Hermes. Until those changes are available, the commands
below are for contributors who already have the prerequisite checkout.

For literal guest arguments after `exec NAME --`, the core CLI must also honor
that `--` boundary when processing session flags. Local integration verification
uses a separate CLI fix; without it, guest Python `-c` or other
`-r`/`--resume` arguments can be merged incorrectly. This is not repaired inside
the plugin.

Python 3.11–3.13 is supported. Actual VM operations require Linux x86_64, accessible
KVM, QEMU, OVMF, cloud-image-utils, OpenSSH and a user systemd session. Imports and
CLI help do not probe those dependencies. Resource operations fail closed when the
core prerequisite is absent. `preflight` reports missing host dependencies without
installing anything.

## Install or stage locally

Use the Python interpreter of the prerequisite Hermes environment, not a random
system Python. For development verification, create a disposable home and venv;
never install into the running agent's environment/profile. The shell variables
below are illustrative paths you must choose explicitly:

```bash
CORE=/absolute/path/to/prerequisite-hermes-checkout
PLUGIN=/absolute/path/to/this-checkout
PY=/absolute/path/to/prerequisite-environment/bin/python
SANDBOX=/absolute/path/to/disposable-artifacts
mkdir -p "$SANDBOX"
export HERMES_HOME="$SANDBOX/directory-profile"
export PYTHONPATH="$CORE"
"$PY" "$PLUGIN/scripts/stage_plugin.py" "$HERMES_HOME"
"$PY" -m hermes_cli.main plugins enable managed-desktops --no-allow-tool-override
"$PY" -m hermes_cli.main plugins list
"$PY" -m hermes_cli.main desktop-vm --help
"$PY" -m hermes_cli.main desktop-vm list
```

The staging helper **copies** the working tree's `__init__.py`, `plugin.yaml`, `LICENSE`
and `managed_desktops/` into `<profile home>/plugins/managed-desktops/`. It refuses
to overwrite an existing installation and never enables the plugin itself.
`hermes plugins install /path` is unsupported. A `file://` Git install clones
committed history only and cannot test an unborn repository or uncommitted files.

Alternatively build a local wheel/sdist and install the wheel into a **disposable**
venv with access to the prerequisite Hermes environment/dependencies:

```bash
"$PY" -m venv --system-site-packages "$SANDBOX/wheel-venv"
export HERMES_HOME="$SANDBOX/wheel-profile"
"$SANDBOX/wheel-venv/bin/python" -m pip install 'build==1.2.2.post1'
"$SANDBOX/wheel-venv/bin/python" -m build "$PLUGIN" --outdir "$SANDBOX/dist"
"$SANDBOX/wheel-venv/bin/python" -m pip install --no-deps "$SANDBOX/dist/hermes_managed_desktops-0.1.0-py3-none-any.whl"
"$SANDBOX/wheel-venv/bin/python" -m hermes_cli.main plugins enable managed-desktops --no-allow-tool-override
"$SANDBOX/wheel-venv/bin/python" -m hermes_cli.main desktop-vm --help
```

`--system-site-packages` exposes the **base interpreter's** packages, not necessarily
another venv's dependencies. If they are not available, install Hermes's declared
dependencies in this disposable venv before invoking its CLI. The `hermes_agent.plugins`
entry point targets the `managed_desktops` module; the wheel includes all guest assets
and the single skill. Use either directory staging or the wheel in a profile, not both.

## Configure and load instructions

Use the intended profile and namespaced settings:

```bash
hermes config set plugins.entries.managed-desktops.settings.cpus 6
```

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

These are defaults, not live resizing. Registration uses `PluginContext.get_config`
inside a callback bound to its originating profile. Explicit CLI sizing wins over
configuration. The VM and I/O modules share fresh invocation-local settings, not a
mutable process-global configuration snapshot.

Ask the agent to load `skill_view(name="managed-desktops:managed-agent-desktops")`.
Do not install an official optional skill; the plugin ships its own instructions.
They are read-only to `skill_manage` and disappear when the plugin is disabled/unloaded.

## Safety and lifecycle

Follow [the workflow](docs/workflow.md) for provisioning, explicit VM/screen targeting,
readiness, guest file transfer, capture, recording, loopback viewing, and removal.
Creation requires explicit NAT acceptance: NAT permits internet and possible host/LAN
access. Screens share one guest account/filesystem; they are not security tenants.
No provider keys, host SSH agents, browser profiles or automatic mounts are copied.
Failures never fall back to host desktop control.

VM state lives under `<profile home>/managed-resources/managed-desktops/`, separate
from plugin code. Disable/uninstall removes discovery surfaces, **not VMs or disks**.
Stop and explicitly remove VMs before deleting their profile. If disabled, re-enable
the plugin for cleanup. Do not move resource directories or delete live disks manually.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). Run tests through the prerequisite checkout's
canonical runner, with its collection-time isolation explicitly loaded:

```bash
HERMES_PYTHON=/absolute/path/to/test-venv/bin/python \
  scripts/run_tests.sh /absolute/path/to/prerequisite-hermes-checkout -q
```

No VM is started by the automated packaging/discovery tests. Fresh-VM acceptance
requires separate authorization and is not replaced by unit tests or a wheel build.

GitHub Actions currently builds the wheel and sdist on Python 3.11–3.13. That
packaging check does **not** run the Hermes-dependent test suite or VM acceptance.
Run the suite above against a checkout containing both core prerequisites. CI
artifacts are development builds, not supported releases; no automatic release or
package-registry publication is configured.
