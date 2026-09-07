# Contributing

Managed Desktops is an optional Linux/KVM native Hermes plugin, distributed independently
of Hermes Agent. Match Hermes's documented plugin compatibility contract and maintain
its prompt-cache, profile isolation and explicit targeting invariants.

Use one focused change per branch. Conventional Commits use `type(scope): subject`.
Read the prerequisite Hermes checkout's AGENTS.md, CONTRIBUTING.md, plugin policy
and skill authoring policy before editing. Python 3.11–3.13 is supported. Do not assume
system packages or configure the host automatically. Use fail-closed checks and
explicit diagnostics.

## Core prerequisite and isolation

This experimental plugin requires the unpublished generic core profile-resource API
(`hermes_cli.profile_resources.resource_root` / `reserve_resource`) and profile
lifecycle guards. Do not add plugin-specific core imports, monkeypatch core, or
substitute plugin callbacks for guards that must survive disable/uninstall.
Registration uses public `PluginContext` methods. Bind callbacks to the registering
home; read namespaced settings through `ctx.get_config`. Never cache mutable config
process-wide or use `ctx._manager` in plugin implementation.

The top-level Hermes CLI must also preserve opaque arguments after `--`; that fix
is a separate core prerequisite. Keep both core changes out of plugin installation
code. Do not claim stock-Hermes compatibility until both are available and verified.

Run `HERMES_PYTHON=/path/to/test-venv/bin/python scripts/run_tests.sh
/path/to/prerequisite-checkout -q` (one shell command). The wrapper delegates to
that checkout's canonical `scripts/run_tests.sh`, with explicit `-c` and `--rootdir`
pointing at the core checkout, `-p tests.conftest`, and `--import-mode=importlib`.
The core tests package must remain importable before external collection. Do not add
`tests/__init__.py`: it can shadow core fixtures or the native directory shim.

Discovery tests use real PluginManager loading and the actual CLI parser attachment,
not source-text assertions. Keep collection/import/help free of VM work. No automated
test may start a VM, download guest images, or control live systemd units.
VM acceptance is separate, on authorized disposable resources.

Build only in disposable environments and use a temporary `HERMES_HOME`. Local
uncommitted staging copies files then enables through the CLI; `hermes plugins
install /path` is not supported, and `file://` installs committed history only.
Build wheel and sdist; verify the pip module entry point as well as the directory
shim, guest assets, and `managed-desktops:managed-agent-desktops` skill. The skill
lives once inside `managed_desktops/skills/`; do not add an official-skill copy.
No autochat binding or tablet viewer is part of this package.

For installed-artifact verification, create two empty disposable profiles, enable
the plugin via `hermes plugins enable managed-desktops --no-allow-tool-override`,
and configure different `plugins.entries.managed-desktops.settings.cpus` values.
Run `scripts/verify_install.py --source entrypoint --home /path/to/first
--other-home /path/to/second` with the wheel's interpreter and `HERMES_HOME` set to
the first profile (one shell command). Use `--source user` for copied directories.
It verifies real discovery, skill read-only behavior, profile-bound settings,
read-only CLI inventories and scoped unload without touching a VM.

Keep generated disks, images, cloud-init seeds, keys and recordings outside this repository.
Package source and guest assets only. Retain the MIT license and contributor attribution.

## CI and releases

The packaging workflow builds wheel and sdist artifacts on Python 3.11–3.13 with
read-only repository permissions. It does not run the Hermes-dependent test suite
or provision a VM. Full functional CI needs a publicly available, pinned Hermes
revision containing both prerequisites; do not silently substitute stock Hermes
or label a packaging-only check as a passing functional suite.

Keep `plugin.yaml` and `pyproject.toml` versions aligned. Publish tags, GitHub
releases, or registry packages only as a separate, authorized release step after
documenting a reproducible supported installation and its verification.
