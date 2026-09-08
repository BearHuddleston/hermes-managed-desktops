# Managed Agent Desktops

Use named Debian VMs for native-app work without operating the host desktop.
The provisioner supports Linux x86_64/KVM and a user systemd session. The
standalone CLI works without Hermes; optional `hermes desktop-vm` integration
uses stock native plugin APIs. No core patch or new model tool is required.
See [0.2.0 source-preview installation](../README.md#install-the-source-preview)
for the published branch and wheel build. No PyPI or GitHub release is required.

## Choose scope before acting

Normal agent instructions always select the intended existing profile explicitly:

```bash
PROFILE_HOME=/absolute/path/to/intended-profile
hermes-managed-desktops --profile-home "$PROFILE_HOME" list
hermes-managed-desktops --profile-home "$PROFILE_HOME" preflight
```

The profile must belong to the current user, have a stable directory birth time,
and contain a regular, valid `config.yaml` mapping. Symlinked, malformed and
nonregular config files are refused. Missing, stale or mismatched profile bindings
are refusals, not permission to try another profile or `--global`. If the intended
profile cannot be determined, ask the user before operating a VM.

`python -m managed_desktops` accepts the same arguments. Both standalone entry
points require `--profile-home PATH` or deliberate operator `--global` **before
the action**; neither infers scope from ambient `HERMES_HOME`. For a directory-only
install, run the module from `<profile home>/plugins/managed-desktops/` or put that
containing directory on `PYTHONPATH`. A copy alone does not install a console script.

Native `hermes desktop-vm` captures the registering profile home. It supports
management, capture, transfers, recording and viewing, but **refuses `exec`, `app`
and `cua`** because stock Hermes preprocesses opaque guest argv. Use the explicit
scoped standalone commands below, never a patched core or a global-scope workaround.

## Host setup

`preflight` reports dependencies and permissions without installing packages.
Debian/Ubuntu host packages are `qemu-system-x86`, `qemu-utils`, `ovmf`,
`cloud-image-utils`, and `openssh-client`. The user needs read/write `/dev/kvm`
access and a reachable user systemd manager. Profile binding needs a compatible
`stat` and filesystem birth time; unsupported filesystems fail closed for binding,
while explicit global operator scope remains available. Other architectures,
macOS, Windows, libvirt and software emulation are not supported by this provisioner.

For native integration, enable the trusted plugin in the intended profile with
`hermes plugins enable managed-desktops --no-allow-tool-override`, then load
`skill_view(name="managed-desktops:managed-agent-desktops")`. The packaged skill
is read-only and is not an official optional-skill install. A native plugin runs
with the host user's privileges, not inside the guest's isolation boundary.

## Create and operate

```bash
hermes-managed-desktops --profile-home "$PROFILE_HOME" create demo --network nat --cpus 4 --memory-mib 4096 --disk-gib 32
hermes-managed-desktops --profile-home "$PROFILE_HOME" wait demo
hermes-managed-desktops --profile-home "$PROFILE_HOME" doctor demo
hermes-managed-desktops --profile-home "$PROFILE_HOME" app --screen 1 demo -- mousepad --disable-server
hermes-managed-desktops --profile-home "$PROFILE_HOME" cua demo --screen 1 call list_windows '{}'
hermes-managed-desktops --profile-home "$PROFILE_HOME" capture demo --screen 1
```

Creation downloads and verifies a pinned Debian 13 cloud image, creates a writable
qcow2 overlay and private UEFI variable store, generates fresh guest-only SSH keys,
and bootstraps two 1440×900 X11 desktops with Cua and recording/viewing services.
Debian image and Cua release bytes are digest-pinned. Apt packages are fetched from
Debian's signed repositories at provisioning time, **not a frozen package snapshot**.
Image verification uses a pinned digest from Debian's HTTPS checksum list, not an
independent GPG-signature verification claim.

Names are unique within the selected external store, including across profiles.
`create` never overwrites a name; `start` is idempotent. `status` reports process
identity and stored configuration, not desktop readiness. `wait` checks provisioning
and both desktop/Cua/view services. Failed preparation retains the instance for
diagnosis and explicit removal.

An interrupted initial reservation can be shown in global inventory as
`phase: incomplete` with `active: null` (unknown, not stopped). It has no established
profile binding. Only deliberate operator recovery may remove it using exact name
confirmation, and only if empty or containing just the initial temporary metadata
file. Disks, keys, unknown files or invalid published metadata are not automatically
deleted. Retain them for diagnosis rather than guessing process ownership.

Guest Cua defaults to `standard` permissions. `create --permission-mode unrestricted`
explicitly accepts unrestricted input inside that guest, not a change to Hermes
host approvals. Use the installed driver's schema rather than host tool parameters:

```bash
hermes-managed-desktops --profile-home "$PROFILE_HOME" cua demo --screen 1 describe click
hermes-managed-desktops --profile-home "$PROFILE_HOME" capture demo --screen 1 --pid 123 --window-id 456
```

Replace PID/window ID with discovery results. Capture saves a PNG and complete JSON
accessibility snapshot, including the session label. Read the full saved JSON and
inspect the PNG; do not reuse stale element tokens after UI changes. For `app` and
display-scoped `exec`, put `--screen` **before NAME**; arguments after `--` belong to
the guest, including literal `-c`, `-r` and `--resume`.

## Network and trust boundary

- Creation requires `--network nat` because provisioning downloads packages. NAT
  permits internet and possible host/LAN access; it is **not** network isolation.
- After provisioning, stop, then `start --network isolated` enables QEMU user-network
  `restrict=on`. Guest-initiated networking is disabled; explicit loopback SSH
  management forwarding remains. IPv6 is disabled in both modes.
- `start` without a network flag retains recorded policy. Changes require a stopped
  VM. No host firewall, bridge, Tailscale route, desktop or global Cua changes occur.
- Two screens share one guest user and filesystem, **not separate security tenants**.
- No automatic mounts, provider keys, SSH agents, browser profiles, X11 forwarding
  or inherited SSH configuration. Guest SSH host keys are pinned before connection.
  Target failures never fall back to host desktop control.
- Neither hypervisor isolation nor cooperative profile targeting makes this a
  malware-analysis sandbox. Same-UID native plugins have full host-user privileges.

```bash
hermes-managed-desktops --profile-home "$PROFILE_HOME" stop demo
hermes-managed-desktops --profile-home "$PROFILE_HOME" start demo --network isolated
hermes-managed-desktops --profile-home "$PROFILE_HOME" wait demo
```

## Files, viewing and recording

The persistent guest workspace is `/home/agent/workspace`; recordings live in
`/home/agent/recordings`. Transfers are explicit single files, not recursive mounts.
Destinations must be new file paths.

```bash
hermes-managed-desktops --profile-home "$PROFILE_HOME" upload demo ./notes.txt /home/agent/workspace/notes.txt
hermes-managed-desktops --profile-home "$PROFILE_HOME" download demo /home/agent/workspace/notes.txt ./returned-notes.txt
hermes-managed-desktops --profile-home "$PROFILE_HOME" exec demo -- uname -a
hermes-managed-desktops --profile-home "$PROFILE_HOME" exec --screen 2 demo -- printenv DISPLAY
hermes-managed-desktops --profile-home "$PROFILE_HOME" view demo --screen 2
hermes-managed-desktops --profile-home "$PROFILE_HOME" record demo --screen 2 start walkthrough
hermes-managed-desktops --profile-home "$PROFILE_HOME" record demo --screen 2 status
hermes-managed-desktops --profile-home "$PROFILE_HOME" record demo --screen 2 stop
```

The viewer prints a loopback URL on the CLI host and holds an SSH tunnel until
Ctrl-C. It does not open/control the normal desktop browser. Local host users can
reach the viewer while active. On a remote CLI host, localhost means that host,
not a tablet/browser client. Do not publish the viewer as a workaround: remote
viewer publishing, session/autochat binding and tablet viewing are not provided.

Recording runs continuously inside the guest, independently of SSH invocation or
app restarts. Stop finalizes MP4; download the returned file explicitly. Use unique
recording names and inspect decoded frames when reporting UI evidence. A powered-off
VM cannot record its own downtime.

## Configuration and external state

Scoped standalone calls read only `plugins.entries.managed-desktops.settings` in
the explicit profile's `config.yaml`. Native callbacks capture `get_hermes_home()`
and read through `ctx.get_config` with that scope bound. Defaults and an example
config are in the [README](../README.md#explicit-scope-and-configuration).
Explicit creation flags win; config is not live resizing. Global creation ignores
profile config and creates unbound guests with package defaults.

The store is `$XDG_STATE_HOME/hermes-managed-desktops`, or
`<OS account home>/.local/state/hermes-managed-desktops` if unset. The fallback uses
the Linux account database, not ambient `HOME`; an isolated Hermes subprocess HOME
does not select another store. An explicit XDG state base must be absolute and
outside profiles and known Hermes roots; do not overlap roots. Metadata lives in
`resources/NAME/instance.json`; generated credentials live in the private `secrets/`
instance directory. The private
cloud-init seed also contains a generated guest host key. Never include these files
or disks in logs, Git or support bundles. The store, not the profile, owns the shared
image cache. Remember the chosen `XDG_STATE_HOME` for later recovery.

Do not move an overlay alone: backing-image paths are absolute. Copied VM metadata
in a different store cannot control the original guest. Do not copy a live disk or
delete its backing image. Automatic VM backup/migration and host-reboot autostart
are not provided.

## Profile lifecycle and operator recovery

Schema-2 metadata optionally binds a VM to a **physical profile directory**, using
canonical path, device, inode and birth time. No token is stored in profiles. A
clone, export/import to a new directory, rename, or deletion/recreation does not
inherit the original binding. The same OS user remains the real owner; these checks
prevent accidental cross-profile targeting, not malicious same-UID access.

An **in-place restore that keeps the same directory retains access**: binding is
not a hash of profile contents. Stock backup restore can replace files inside the
existing root. Before restoring or repurposing a profile slot for a different
intended owner, deliberately stop and unbind its VMs.

Stock Hermes permits profile rename/delete, including when the plugin is disabled
or uninstalled. External VMs remain recoverable and **may still be running**;
rename/delete makes their bindings stale. Disable/uninstall removes discovery
surfaces, not disks or systemd units. Scoped calls fail closed afterward, but
in-flight operations are not synchronously revoked.

Only with explicit user intent, an operator may use standalone `--global` for
recovery. It never requires Hermes or native plugin discovery. Keep the standalone
package outside a profile that may be deleted. These are **operator-only examples**,
not a fallback sequence for a normal scoped agent:

```bash
hermes-managed-desktops --global list
hermes-managed-desktops --global status demo
hermes-managed-desktops --global stop demo
# Read the exact immutable id from status; do not substitute the VM name.
VM_UUID=exact-uuid-from-status
hermes-managed-desktops --global bind demo --profile-home /absolute/path/to/intended-profile --confirm "$VM_UUID"
hermes-managed-desktops --profile-home /absolute/path/to/intended-profile status demo
```

`bind` is also the rebind command. It and `unbind` require a stopped VM and exact
immutable UUID confirmation. To deliberately leave a stopped VM unbound:

```bash
hermes-managed-desktops --global unbind demo --confirm "$VM_UUID"
hermes-managed-desktops --global status demo
```

If binding is unsupported by the filesystem, retain explicit operator scope or use
a suitable profile filesystem. Do not fabricate a birth time or copy a token.

Legacy 0.1.0 profile-owned VM roots are **untouched and not migrated/adopted**.
Nonempty `managed-resources/managed-desktops/resources/` blocks profile binding,
including if brought in through a legacy profile import. Use 0.1.0 and its old
prerequisite harness to stop/remove those VMs, or retain the old version pending an
explicit migration. Do not delete live disks to make a new binding succeed.

## Shutdown, diagnostics and removal

`stop` requests orderly ACPI shutdown and waits, without force-killing on timeout.
Inspect `doctor NAME` and the exact systemd unit in `status NAME` on failure.
There is no implicit destructive recovery/recreate operation.

```bash
hermes-managed-desktops --profile-home "$PROFILE_HOME" stop demo
hermes-managed-desktops --profile-home "$PROFILE_HOME" remove demo --confirm demo
hermes-managed-desktops --profile-home "$PROFILE_HOME" list
```

Removal requires a stopped VM and exact **name**, not UUID. It deletes the VM's disk,
keys and captures; fetch anything needed first. The shared image cache is retained.
For a stale/unbound VM, an explicitly authorized operator can use
`hermes-managed-desktops --global remove demo --confirm demo` after stopping it.
