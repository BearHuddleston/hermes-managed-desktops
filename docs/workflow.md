# Managed Agent Desktops

`hermes desktop-vm` creates named Debian virtual machines for native-app work without
using your own desktop. The first implementation supports **Linux x86_64 with KVM
and a user systemd session**. It is a standalone native plugin with a CLI and a
read-only packaged skill, not a new model tool or an in-app VM manager.

**Prerequisite: an unpublished generic Hermes core profile-resource change.** The core
must provide `hermes_cli.profile_resources.resource_root` and `reserve_resource`,
exclude managed resources from profile clone/export, and refuse profile rename/delete
while resources remain, even without this plugin. There is no released Hermes version
claimed to contain that change. See [installation and isolated staging](../README.md).
Imports and CLI help remain usable on unsupported core; state operations fail closed.

It does **not** change the target of the normal `computer_use` tool, copy your
Hermes configuration into the guest, or use unmerged remote-Cua/session-viewer APIs.
The VM must be named explicitly; desktop operations also require a screen.

## Host setup

Run `hermes desktop-vm preflight`. It reports missing programs and permissions
without installing anything. On Debian/Ubuntu the host packages are
`qemu-system-x86`, `qemu-utils`, `ovmf`, `cloud-image-utils`, and `openssh-client`.
The current user needs read/write access to `/dev/kvm` and a reachable user systemd
manager. Other architectures, macOS, Windows, libvirt and software emulation are
not supported by this initial provisioner.

Enable the plugin with `hermes plugins enable managed-desktops --no-allow-tool-override` in the intended
profile. Load its packaged instructions explicitly with
`skill_view(name="managed-desktops:managed-agent-desktops")`; they are not installed
from the official optional-skills catalog.

For uncommitted local changes, copy the directory-discovery shim `__init__.py`,
`plugin.yaml`, `LICENSE`, and `managed_desktops/` into `<profile home>/plugins/managed-desktops/`
before enabling. `hermes plugins install /path` is unsupported; a `file://` install
clones committed history only. Do not use it to test uncommitted working files.

## Create and operate

```text
hermes desktop-vm preflight
hermes desktop-vm create demo --network nat --cpus 4 --memory-mib 4096 --disk-gib 32
hermes desktop-vm wait demo
hermes desktop-vm doctor demo
hermes desktop-vm app --screen 1 demo -- mousepad --disable-server
hermes desktop-vm cua demo --screen 1 call list_windows '{}'
hermes desktop-vm capture demo --screen 1
```

Creation downloads and verifies a pinned Debian 13 cloud image, creates a writable
qcow2 overlay and private UEFI variable store, generates fresh guest-only SSH keys,
and bootstraps two 1440×900 X11 desktops with Cua and recording/viewing services.
Debian image and Cua release bytes are digest-pinned; apt packages are fetched from
Debian's signed repositories at provisioning time, **not a fully frozen package
snapshot**. Image verification uses a pinned digest sourced from Debian's HTTPS
checksum list, not a claim of independently validated GPG signatures.

`create` never overwrites an existing name. `start` is idempotent. `status` reports
QEMU process identity and stored configuration; `wait` also checks provisioning
completion and both desktop/Cua/view services. Failed preparation retains the
instance for diagnosis and explicit removal.

An interruption before the first metadata write is listed as `phase: incomplete`
with `active: null` (unknown, not a claim that a guest is stopped). Use
`remove NAME --confirm NAME` to recover a reservation that is empty or contains
only the initial temporary metadata file. A directory containing disks, keys,
unknown files or invalid published metadata is not automatically deleted: retain
it for diagnosis rather than guessing process ownership. Creation and this
recovery share the same per-VM lock.

Guest Cua defaults to `standard` permissions. `create --permission-mode unrestricted`
is an explicit acceptance of unrestricted input inside that guest, not a change to
host Hermes approvals. Use the driver's `describe` command to get its current input
schema rather than translating the host tool's parameter names.

```text
hermes desktop-vm cua demo --screen 1 describe click
hermes desktop-vm capture demo --screen 1 --pid 123 --window-id 456
```

Replace the illustrative PID/window ID with discovery results. Captures save the
PNG and complete JSON accessibility snapshot, including the session label to reuse.
Do not reuse stale element tokens after UI changes.

## Network and trust boundary

- **Creation requires `--network nat`.** Provisioning downloads packages. NAT allows
  internet and potential host/LAN access; it is not network isolation.
- After provisioning, `stop`, then `start --network isolated` enables QEMU's
  `restrict=on` user network. Guest-initiated networking is disabled; the explicit
  loopback SSH management forwarding remains. IPv6 is disabled in both modes.
- `start` without a network flag retains the recorded policy. Network changes require
  a stopped VM. No host firewall, bridge, Tailscale route or global Cua setting changes.
- Both screens share one guest user and filesystem. They are **not security tenants**.
- No automatic host filesystem mounts, provider keys, SSH agents, browser profiles,
  X11 forwarding or inherited SSH configuration. The guest's SSH server key is pinned
  before first connection. Target failures never fall back to the host desktop.
- VM isolation is not a guarantee against hypervisor vulnerabilities and does not
  make this a malware-analysis sandbox.

```text
hermes desktop-vm stop demo
hermes desktop-vm start demo --network isolated
hermes desktop-vm wait demo
```

## Files, viewing and recording

The persistent guest workspace is `/home/agent/workspace`; recordings are in
`/home/agent/recordings`. Transfers are explicit single files, not recursive mounts.
Destinations must be new file paths.

```text
hermes desktop-vm upload demo ./notes.txt /home/agent/workspace/notes.txt
hermes desktop-vm download demo /home/agent/workspace/notes.txt ./returned-notes.txt
hermes desktop-vm exec demo -- uname -a
hermes desktop-vm exec --screen 2 demo -- printenv DISPLAY
hermes desktop-vm view demo --screen 2
hermes desktop-vm record demo --screen 2 start walkthrough
hermes desktop-vm record demo --screen 2 status
hermes desktop-vm record demo --screen 2 stop
```

The viewer prints a loopback URL on the CLI host and holds an SSH tunnel until
Ctrl-C. It does not open, control or retarget your normal desktop browser. Local
host users can reach the viewer while it is active. For a remote CLI host, localhost
means that host, not the tablet/browser client. Remote viewer publishing and in-app
session binding are not provided. There is no autochat binding or tablet viewer.

Recording runs continuously inside the guest, independently of SSH invocation or
app restarts. Stop finalizes MP4; download the returned guest file explicitly. Use
unique recording names. Review decoded video frames when reporting UI evidence.
A powered-off VM cannot record its own downtime.

## Configuration and state

Defaults are resolved through `PluginContext.get_config` in the originating profile.
For example: `hermes config set plugins.entries.managed-desktops.settings.cpus 6`.

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
        ovmf_code: "" # empty uses /usr/share/OVMF/OVMF_CODE_4M.fd
        ovmf_vars: "" # empty uses /usr/share/OVMF/OVMF_VARS_4M.fd
```

These are creation defaults, not live resizing. Per-instance resources, UUID,
network choice and provisioning phase live in `<profile home>/managed-resources/managed-desktops/resources/NAME/instance.json`.
Generated credentials live in the instance's private `secrets/` directory; the
private cloud-init seed also contains a newly generated guest host key. Never
include these files or disk images in logs, Git, or support bundles.

Profile cloning and exports exclude VM disks and credentials. A manually copied VM manifest
cannot control its original guest. Deleting or renaming a profile that owns named
VMs is refused even while the plugin is disabled/uninstalled: explicitly stop and
remove those VMs first. Disabling the plugin unloads its CLI and skill, not its
resources or systemd units; re-enable it to inspect and clean up. Moving profile/disk
directories manually is unsupported because ownership and backing paths are absolute.

The profile also owns its image cache. Stop before copying a VM; preserve its
backing image, overlay, metadata and UEFI variables together. Do not move an overlay
alone: its backing path is absolute. Automatic backup/migration and autostart on
host reboot are not provided.

## Shutdown, diagnostics and removal

`stop` requests orderly ACPI shutdown and waits. It does not force-kill on timeout.
Inspect `doctor NAME` and the exact systemd unit shown in `status NAME` if startup
or shutdown fails. There is no implicit destructive recovery or recreate operation.

```text
hermes desktop-vm list
hermes desktop-vm stop demo
hermes desktop-vm remove demo --confirm demo
```

Removal requires the exact name and a stopped VM. It deletes that VM's disk, keys
and captures; download anything you need first. The shared image cache is retained.
