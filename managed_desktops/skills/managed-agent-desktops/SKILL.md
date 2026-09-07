---
name: managed-agent-desktops
description: Operate dedicated Linux virtual desktops for agents.
version: 1.0.0
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

Use named Debian VMs for native-app work without operating the user's own desktop.
The CLI manages Linux/KVM guests, not existing machines or session-bound Desktop viewers.
Two screens are work areas in one guest, not separate security tenants.

## When to Use

Use for isolated GUI dogfooding, persistent native-app tasks, screenshots and continuous recordings.
Do not use the normal `computer_use` tool assuming it targets a guest: this feature never retargets it.

## Prerequisites

The machine running `terminal` needs Linux x86_64, accessible KVM, a user systemd session,
QEMU, OVMF, cloud-image-utils and OpenSSH. Run preflight through `terminal`; it reports
missing dependencies without installing packages or changing the host.

Enable the standalone `managed-desktops` plugin in the intended Hermes profile.
It requires the **unpublished generic core profile-resource prerequisite**; stock Hermes
without `hermes_cli.profile_resources.resource_root` and `reserve_resource` is unsupported.
There is no official optional-skill install. Load these read-only packaged instructions with
`skill_view(name="managed-desktops:managed-agent-desktops")`. VM state belongs to that
profile's `managed-resources/managed-desktops/` directory. Never copy provider keys, real browser profiles, host SSH keys or home directories
into a guest. Provisioning needs explicit NAT access; NAT can reach the host/LAN as well
as the internet. This is not a malware-analysis sandbox.

For unpublished local changes, copy the checkout's `__init__.py`, `plugin.yaml`, `LICENSE` and
`managed_desktops/` into `<profile home>/plugins/managed-desktops/`, then run
`hermes plugins enable managed-desktops --no-allow-tool-override` in that profile. `hermes plugins install /path`
is unsupported; `file://` installs clone committed history, not uncommitted files.
See the repository README for isolated staging and wheel installation.

## How to Run

Invoke the CLI through `terminal`:

```text
hermes desktop-vm preflight
hermes desktop-vm create demo --network nat
hermes desktop-vm wait demo
hermes desktop-vm doctor demo
```

Creation is explicit and refuses existing names. Use a unique disposable name for tests;
never remove or reprovision an existing user VM to make a test pass.

## Quick Reference

| Command | Purpose |
|---|---|
| `hermes desktop-vm list` | This profile's inventory |
| `hermes desktop-vm status demo` | Process identity and recorded configuration |
| `hermes desktop-vm start demo` | Idempotent start, retaining recorded network policy |
| `hermes desktop-vm stop demo` | Orderly shutdown, without force-killing |
| `hermes desktop-vm start demo --network isolated` | Boot with QEMU restricted networking |
| `hermes desktop-vm app --screen 2 demo -- mousepad --disable-server` | Launch a guest native app |
| `hermes desktop-vm cua demo --screen 2 call list_windows '{}'` | Discover windows on the selected guest screen |
| `hermes desktop-vm cua demo --screen 2 describe click` | Retrieve the installed driver's schema |
| `hermes desktop-vm capture demo --screen 2` | Save a PNG and full JSON snapshot |
| `hermes desktop-vm view demo --screen 2` | Hold a local viewer tunnel; Ctrl-C closes it |
| `hermes desktop-vm record demo --screen 2 start task-demo` | Start continuous recording |
| `hermes desktop-vm record demo --screen 2 stop` | Finalize the recording |
| `hermes desktop-vm remove demo --confirm demo` | Explicitly delete a stopped VM |

For `app` and display-scoped `exec`, put `--screen` before the VM name;
arguments after `--` belong to the guest command. Use `--help` for sizing and timeout flags.

## Procedure

1. Run preflight and inventory. Confirm the VM name, profile and assigned screen.
   Creation accepts sizing flags; defaults come from
   `plugins.entries.managed-desktops.settings` in config.yaml, read through PluginContext.
   For example: `hermes config set plugins.entries.managed-desktops.settings.cpus 6`.
   Guest Cua defaults to standard permissions. Only use `create --permission-mode unrestricted`
   when the user explicitly accepts unrestricted input inside that guest.
2. Create or start the named VM, then run `wait`. Process-active is not desktop-ready.
   On failure, use `doctor`; never fall back to host desktop automation.
3. After provisioning, stop and start with `--network isolated` when outbound networking is
   unnecessary. It disables guest-initiated networking through QEMU while retaining the
   explicitly forwarded SSH management connection. NAT remains the recorded policy until changed.
4. Transfer individual files explicitly. Guest paths must be literal absolute file paths.
   Use the guest workspace (the `agent` user's `workspace` directory); use `exec demo -- pwd`
   or `exec demo -- printenv HOME` to discover guest paths. Upload/download take NAME SOURCE
   DESTINATION; neither performs recursive host-home sharing. Download artifacts to a new local path.
5. Launch a native app on the assigned screen, discover windows, and inspect the current Cua
   `describe` schemas. Capture a window with both `--pid` and `--window-id` when accessibility
   and window-specific input are needed. Use `read_file` on the saved JSON and `vision_analyze`
   on the saved PNG; terminal truncation is not the complete accessibility tree.
6. Use fresh element tokens and the capture's session label. Start with background input;
   re-capture after uncertain delivery before retrying. Escalate only after a structured
   refusal or verified no-op, and only on the assigned guest screen. Never type shell/code
   into an app as a substitute for file transfer and `exec`.
7. Start a uniquely named continuous recording before the feature workflow. Stop it to
   finalize MP4; explicitly download the returned guest artifact. Inspect decoded video
   frames, not only separate screenshots. A reboot ends recording; it cannot record the
   guest's powered-off interval.
8. Verify saved files across an orderly stop/start/wait. Fetch artifacts before an explicitly
   requested removal; removal deletes guest data, keys and locally stored VM captures.

## Pitfalls

- Both screens share the `agent` account, filesystem and privileges. Do not assign mutually
  untrusted tenants to different screens or let concurrent workers fight over one screen.
- A missing VM, SSH host-key mismatch, unavailable screen or stopped Cua daemon is an error,
  not permission to start another target. The managed CLI does not change host Cua settings.
- Image bytes and the Cua release are pinned and verified. Debian packages come from signed
  repositories at provisioning time; the complete guest is not a bit-for-bit frozen image.
- VM disk overlays depend on their profile's cached Debian backing image. Never move just
  the overlay, copy a live disk, or delete the backing image while any VM depends on it.
- There is no autochat/session binding or tablet viewer. The viewer binds only on
  the machine running the CLI. Do not publish it to LAN, internet or a pre-existing Webapp route as a workaround.
- Core excludes managed resources from profile clone/export and refuses profile rename/delete
  while a resource leaf remains, even if this plugin is disabled or removed. Stop and remove
  each VM explicitly before deleting its profile; uninstalling code does not clean up VMs.
- A failed bootstrap retains state for diagnostics. Reusing `create` does not silently wipe it.
- GUI Close and abrupt process termination have different persistence guarantees. Report
  crash-durability failures separately from orderly-restart results.

## Verification

Through `terminal`, run `hermes desktop-vm doctor demo` after `wait` and inspect its
actual guest readiness result. For a provisioner change, additionally demonstrate fresh-VM
input/readback, both screens, file round-trip, orderly-restart persistence, decoded recording
frames, and stopped-target refusal; mocks or an existing-VM smoke test are insufficient.
