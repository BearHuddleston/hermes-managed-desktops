"""``hermes desktop-vm`` — managed Linux/KVM guests, not the host desktop."""

from __future__ import annotations

import argparse
import json
import sys


def _dispatch(args):
    from . import vm, io
    from .state import VMError, require_linux

    def guest_exec():
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        return io.execute(args.name, command, screen=args.screen, timeout=args.timeout)

    def guest_app():
        import uuid

        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not command:
            raise VMError("An application command is required.")
        return io.execute(args.name, [
            "sudo", "systemd-run", "--collect", f"--unit=hermes-vm-app-{uuid.uuid4()}",
            "--uid=agent", "--gid=agent", "--property=WorkingDirectory=/home/agent/workspace",
            "--", "/usr/local/bin/hermes-vm-screen", io.screen_number(args.screen), *command,
        ])

    handlers = {
        "preflight": vm.preflight,
        "list": vm.list_vms,
        "create": lambda: vm.create(args.name, cpus=args.cpus, memory_mib=args.memory_mib, disk_gib=args.disk_gib, network=args.network, permission_mode=args.permission_mode),
        "start": lambda: vm.start(args.name, network=args.network),
        "stop": lambda: vm.stop(args.name, timeout=args.timeout),
        "status": lambda: vm.status(args.name),
        "remove": lambda: vm.remove(args.name, args.confirm),
        "wait": lambda: io.wait(args.name, timeout=args.timeout),
        "doctor": lambda: io.doctor(args.name),
        "exec": guest_exec,
        "app": guest_app,
        "cua": lambda: io.cua(args.name, args.screen, args.verb, args.argv),
        "record": lambda: io.record(args.name, args.screen, args.action, args.recording),
        "capture": lambda: io.capture(args.name, args.screen, args.output, args.pid, args.window_id),
        "upload": lambda: io.transfer(args.name, args.source, args.destination, upload=True),
        "download": lambda: io.transfer(args.name, args.source, args.destination, upload=False),
        "view": lambda: io.view(args.name, args.screen, args.port),
    }
    try:
        require_linux()
        result = handlers[args.vm_action]()
        if hasattr(result, "returncode"):
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            return result.returncode
        if isinstance(result, int):
            return result
        print(json.dumps(result, indent=2))
        if args.vm_action == "preflight":
            return 0 if result["ready"] else 1
        if args.vm_action == "doctor":
            return 0 if result.get("ready", result["host"]["ready"]) else 1
        return 0
    except (VMError, OSError, ValueError) as exc:
        print(f"desktop-vm: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("desktop-vm: interrupted; VM state retained (viewer tunnel closed).", file=sys.stderr)
        return 130


def setup_parser(parser):
    commands = parser.add_subparsers(dest="vm_action", required=True)
    commands.add_parser("preflight", help="Read-only host dependency and KVM checks")
    commands.add_parser("list", help="List this profile's VMs as JSON")
    create = commands.add_parser("create", help="Create a fresh guest; existing names are never overwritten")
    create.add_argument("name")
    create.add_argument("--network", choices=("nat",), required=True, help="Explicitly accept provisioning internet and potential host/LAN access")
    create.add_argument("--cpus", type=int)
    create.add_argument("--memory-mib", type=int)
    create.add_argument("--disk-gib", type=int)
    create.add_argument("--permission-mode", choices=("standard", "unrestricted"), default="standard", help="Guest Cua only; unrestricted explicitly accepts full guest input authority")
    start = commands.add_parser("start", help="Start idempotently; optionally change network while stopped")
    start.add_argument("name")
    start.add_argument("--network", choices=("nat", "isolated"), help="Persist NAT or QEMU restrict=on; omitted keeps the recorded policy")
    stop = commands.add_parser("stop", help="Orderly ACPI shutdown; never force-kills the guest")
    stop.add_argument("name")
    stop.add_argument("--timeout", type=int, default=120)
    commands.add_parser("status", help="VM process/identity status (use wait for guest readiness)").add_argument("name")
    remove = commands.add_parser("remove", help="Delete a stopped VM's disk, keys and artifacts; image cache retained")
    remove.add_argument("name")
    remove.add_argument("--confirm", required=True, metavar="NAME")
    wait = commands.add_parser("wait", help="Wait for provisioning and both desktop/Cua/view services")
    wait.add_argument("name")
    wait.add_argument("--timeout", type=int)
    commands.add_parser("doctor", help="Host, guest and cloud-init diagnostics").add_argument("name", nargs="?")
    for action in ("exec", "app"):
        command = commands.add_parser(action, help="Run a guest command" if action == "exec" else "Launch a managed guest GUI app")
        command.add_argument("--screen", type=int, choices=(1, 2), required=action == "app", help="Place before NAME; required for GUI apps")
        command.add_argument("--timeout", type=int, default=60)
        command.add_argument("name")
        command.add_argument("argv", nargs=argparse.REMAINDER, help="Guest command after --; never run on host")
    cua = commands.add_parser("cua", help="Call the selected guest screen's Cua CLI; never autostarts host Cua")
    cua.add_argument("name")
    cua.add_argument("--screen", type=int, choices=(1, 2), required=True)
    cua.add_argument("verb", choices=("call", "describe", "tools"))
    cua.add_argument("argv", nargs=argparse.REMAINDER)
    record = commands.add_parser("record", help="Continuous guest FFmpeg recording independent of SSH calls")
    record.add_argument("name")
    record.add_argument("--screen", type=int, choices=(1, 2), required=True)
    record.add_argument("action", choices=("start", "stop", "status"))
    record.add_argument("recording", nargs="?")
    capture = commands.add_parser("capture", help="Save a guest Cua screenshot and complete accessibility snapshot")
    capture.add_argument("name")
    capture.add_argument("--screen", type=int, choices=(1, 2), required=True)
    capture.add_argument("--output", help="New local PNG path; defaults to the VM's private artifacts directory")
    capture.add_argument("--pid", type=int)
    capture.add_argument("--window-id", type=int)
    for action in ("upload", "download"):
        transfer = commands.add_parser(action, help="Explicit single-file transfer; no automatic mounts")
        transfer.add_argument("name")
        transfer.add_argument("source")
        transfer.add_argument("destination")
    view = commands.add_parser("view", help="Hold a loopback-only SSH viewer tunnel until Ctrl-C")
    view.add_argument("name")
    view.add_argument("--screen", type=int, choices=(1, 2), required=True)
    view.add_argument("--port", type=int, default=0)
    parser.set_defaults(func=_dispatch)
