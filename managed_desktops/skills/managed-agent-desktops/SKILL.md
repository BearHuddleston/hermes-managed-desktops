---
name: managed-agent-desktops
description: Operate dedicated Linux virtual desktops for agents.
version: 2.0.0
author: BearHuddleston (BearHuddleston), Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [virtual-machines, computer-use, KVM, recording]
    category: autonomous-ai-agents
    related_skills: [computer-use]
---

# Managed Agent Desktops Skill

Use named Debian VMs for native-app work without operating the user's desktop.
The standalone CLI manages Linux/KVM guests; optional stock-Hermes integration adds
`hermes desktop-vm`, not a model tool or session-bound Desktop viewer.

## When to Use

Use for isolated GUI dogfooding, persistent native-app tasks, screenshots and continuous
recordings. Do not assume normal `computer_use` targets a guest: it is never retargeted.
Two screens are work areas in one guest, not separate security tenants.

## Prerequisites

The machine running `terminal` needs Linux x86_64, accessible KVM, a user systemd session,
QEMU, OVMF, cloud-image-utils and OpenSSH. `preflight` diagnoses without installing or
changing host settings. Profile binding needs stable directory birth time and a compatible
`stat`; unsupported filesystems fail closed. Do not change scope to evade this refusal.

Use the installed `hermes-managed-desktops` CLI or `python -m managed_desktops`, both
independent of Hermes. The experimental source is on `main`; there is no PyPI
publication. Follow the repository README to build a wheel, or use a published
GitHub release after verifying its checksums. A draft release is maintainer-only,
not a public installation source. Use Python 3.11–3.13 and keep standalone recovery
outside profiles.
For a directory-only copy, run the module from `<profile home>/plugins/managed-desktops/`
or set `PYTHONPATH` to that containing directory. Copying does not install a console script.

Native integration uses documented stock plugin APIs, with no profile-resource or argv
patch. Enable in the intended profile and load these read-only instructions with
`skill_view(name="managed-desktops:managed-agent-desktops")`; there is no official
optional-skill copy. Native `exec`, `app`, and `cua` refuse operation because stock Hermes
preprocesses arbitrary guest argv. Use standalone commands for these, not a core workaround.
Native management/capture/transfers/recording/viewing remain available.

Never copy provider keys, browser profiles, host SSH keys/agents or home directories into
a guest. NAT provisioning requires explicit acceptance of internet and possible host/LAN
access. Native plugins have same-UID host privileges; this is not a malware-analysis sandbox.

## How to Run

Through `terminal`, select the intended profile path explicitly on **every** normal agent
invocation. The profile must exist, belong to the user and have a regular, valid `config.yaml`
mapping; symlinks, malformed YAML and nonregular configs are refused. Confirm the intended
profile with the user if it is not known. Never infer global authority from ambient env.

```bash
PROFILE_HOME=/absolute/path/to/intended-existing-profile
hermes-managed-desktops --profile-home "$PROFILE_HOME" preflight
hermes-managed-desktops --profile-home "$PROFILE_HOME" list
hermes-managed-desktops --profile-home "$PROFILE_HOME" create demo --network nat
hermes-managed-desktops --profile-home "$PROFILE_HOME" wait demo
hermes-managed-desktops --profile-home "$PROFILE_HOME" doctor demo
```

`--profile-home PATH` must precede the action. Creation refuses existing names; names are
unique across all profiles in the selected external store. Use a unique disposable name
for tests, never remove/reprovision an existing user VM to make a test pass.

**On scoped refusal, stop and report. Never retry with `--global`.** Global scope is a
deliberate operator recovery/management choice requiring explicit user intent, not normal
agent operation. Both standalone entry points require an explicit scope even for inventory.

## Quick Reference

Every command below retains the explicit profile scope:

| Command | Purpose |
|---|---|
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" status demo` | Process identity and configuration |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" start demo` | Idempotent start, retaining network policy |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" stop demo` | Orderly shutdown, no force-kill |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" start demo --network isolated` | QEMU restricted networking, while stopped |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" app --screen 2 demo -- mousepad --disable-server` | Launch guest app |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" cua demo --screen 2 call list_windows '{}'` | Discover guest windows |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" cua demo --screen 2 describe click` | Installed driver schema |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" capture demo --screen 2` | Save PNG and full JSON snapshot |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" exec --screen 2 demo -- printenv DISPLAY` | Literal guest argv |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" view demo --screen 2` | Loopback viewer tunnel, Ctrl-C closes |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" record demo --screen 2 start task-demo` | Continuous recording |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" record demo --screen 2 stop` | Finalize MP4 |
| `hermes-managed-desktops --profile-home "$PROFILE_HOME" remove demo --confirm demo` | Delete stopped VM, exact name confirmation |

For `app` and display-scoped `exec`, place `--screen` before NAME. Arguments after `--`
belong to the guest, including literal `-c`, `-r`, and `--resume`. Use `--help` for sizing
and timeout flags. `python -m managed_desktops` is equivalent with a usable import path.

## Procedure

1. Run scoped preflight and inventory. Confirm VM name, profile and assigned screen.
   Standalone scoped defaults come only from `plugins.entries.managed-desktops.settings`
   in that profile's `config.yaml`; explicit sizing wins. Native callbacks capture their
   registering home and read through `ctx.get_config`. No mutable global config is shared.
   Cua uses standard permissions unless the user explicitly accepts unrestricted guest input.
2. Create/start the named VM, then `wait`. Process-active is not desktop-ready. On failure,
   use scoped `doctor`; never fall back to host desktop automation or another scope.
3. After provisioning, stop and start with `--network isolated` if outbound networking is
   unnecessary. QEMU `restrict=on` blocks guest-initiated traffic while preserving explicit
   loopback SSH management forwarding. NAT remains recorded until changed.
4. Transfer individual files explicitly with scoped `upload NAME SOURCE DESTINATION` and
   `download NAME SOURCE DESTINATION`. Guest paths are literal absolute paths; use
   `/home/agent/workspace`. Destinations must be new files, not recursive host-home sharing.
5. Launch on the assigned screen, discover windows, and inspect Cua `describe` schemas.
   Capture with both `--pid` and `--window-id` for window-specific access. Use `read_file`
   on the complete JSON and `vision_analyze` on the PNG; terminal truncation is not the tree.
6. Use fresh element tokens and the capture's session label. Start with background input;
   re-capture after uncertain delivery. Escalate only after structured refusal or verified
   no-op, only on the assigned guest screen. Never type shell/code into an app instead of
   file transfer and scoped `exec`.
7. Start a unique continuous recording before the workflow. Stop to finalize MP4, explicitly
   download the returned artifact and inspect decoded frames. Recording survives SSH/app
   restarts, not guest shutdown; it cannot record powered-off intervals.
8. Verify files across orderly stop/start/wait. Fetch artifacts before requested removal;
   removal deletes guest data, keys and locally stored captures. Verify scoped inventory after.

## Pitfalls

- Both screens share the `agent` account, filesystem and privileges. Do not assign mutually
  untrusted tenants or let concurrent workers fight over one screen.
- Missing VM, stale binding, SSH host-key mismatch, unavailable screen or stopped Cua is an
  error, not permission to change targets. No host Cua or desktop settings are changed.
- VM state and shared image cache live in `$XDG_STATE_HOME/hermes-managed-desktops`, or
  `<OS account home>/.local/state/hermes-managed-desktops`. Fallback uses the Linux
  account database, not ambient `HOME`; Hermes subprocess HOME isolation does not select
  another store. Explicit XDG state must be absolute and outside profiles and known Hermes
  roots. Remember the selected store for recovery; never move only an overlay or delete
  its backing image.
- Schema-2 bindings record physical profile directory path/device/inode/birth time outside
  profiles; there is no clonable token. Clone/import into a new directory, rename, and
  deletion/recreation do not inherit authority. **In-place restore retaining the directory
  retains access.** Before repurposing/restoring a slot for a different intended owner, the
  operator should stop and unbind VMs. Same-UID host users remain the real owners.
- Stock Hermes allows profile rename/delete, even without this plugin. External VMs survive
  and may still run; bindings become stale. Disable/uninstall removes discovery, not VMs.
  New scoped calls refuse; active in-flight operations are not synchronously revoked.
- Only with explicit user intent may an operator use standalone `--global` to inspect/stop/
  rebind/unbind/remove retained VMs without Hermes. `bind`/`unbind` need a stopped VM and
  `--confirm` its exact immutable UUID. Removal still requires the exact NAME. Global creation
  is unbound and uses package defaults. Never use global as automatic scoped-error recovery.
- Nonempty legacy 0.1.0 profile-owned resource roots block binding and are left untouched,
  including legacy imports. Use the old version/harness for cleanup or retain it pending
  explicit migration; never delete live disks to bypass refusal. No automatic adoption exists.
- Debian image/Cua bytes are verified and pinned; apt uses signed live repositories, not a
  fully frozen guest snapshot. Failed bootstrap retains state; create does not silently wipe it.
- Viewer access is loopback on the CLI host, accessible to local host users. Do not publish
  it to LAN/internet or a pre-existing Webapp route. No autochat binding or tablet viewer exists.
- GUI Close and abrupt process termination have different persistence guarantees; report
  crash-durability separately from orderly-restart results.

## Verification

Through `terminal`, run `hermes-managed-desktops --profile-home "$PROFILE_HOME" doctor demo`
after `wait` and inspect actual readiness. Provisioner changes additionally need fresh-VM
input/readback, both screens, file round-trip, orderly-restart persistence, decoded recording
frames and stopped-target refusal. Mocks, wheel builds and no-KVM CI do not establish that.
