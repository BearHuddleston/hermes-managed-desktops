"""Guest seed and executable contracts, without contacting a real desktop."""

import configparser
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import runpy
import shlex
import socket
import subprocess
import sys
import tarfile
from unittest.mock import Mock

import pytest
import yaml

from managed_desktops.guest import build_cloud_config

PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test"
PRIVATE = "-----BEGIN OPENSSH PRIVATE KEY-----\nTEST-ONLY\n-----END OPENSSH PRIVATE KEY-----\n"


@pytest.fixture
def seed():
    return yaml.safe_load(build_cloud_config(PUBLIC, PRIVATE, PUBLIC))


@pytest.fixture
def guest_files(seed, tmp_path):
    """Materialize cloud-init output; execute artifacts rather than inspect source."""
    result = {}
    for item in seed["write_files"]:
        path = tmp_path / item["path"].lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"])
        result[Path(item["path"]).name] = path
    return result


def test_seed_pins_guest_identity_and_keeps_credentials_out_of_commands(seed):
    agent = next(user for user in seed["users"] if user["name"] == "agent")
    assert agent["uid"] == 1000 and agent["homedir"] == "/home/agent"
    assert agent["ssh_authorized_keys"] == [PUBLIC]
    assert seed["ssh_keys"] == {"ed25519_private": PRIVATE, "ed25519_public": PUBLIC}
    # cloud-init requires a nonempty generation list; supplied ssh_keys take precedence.
    assert seed["ssh_genkeytypes"]
    assert set(seed["ssh_genkeytypes"]) == {name.removesuffix("_private") for name in seed["ssh_keys"] if name.endswith("_private")}
    assert seed["ssh_pwauth"] is False and seed["disable_root"] is True
    assert seed["ssh"]["emit_keys_to_console"] is False
    assert PRIVATE not in str(seed["runcmd"])
    assert PRIVATE not in str(seed["write_files"])
    assert {"acpid", "mousepad", "ffmpeg", "python3-websockify"} <= set(seed["packages"])
    generated_paths = {item["path"] for item in seed["write_files"]}
    assert seed["runcmd"][0][0] in generated_paths


@pytest.mark.parametrize("mode", ["standard", "unrestricted"])
def test_units_connect_each_screen_and_only_use_requested_permission(mode):
    seed = yaml.safe_load(build_cloud_config(PUBLIC, PRIVATE, PUBLIC, mode))
    units = {}
    for item in seed["write_files"]:
        if item["path"].endswith(".service"):
            parsed = configparser.ConfigParser(interpolation=None, strict=False)
            parsed.read_string(item["content"])
            units[Path(item["path"]).name] = parsed
    desktop = units["hermes-vm-desktop@.service"]
    assert desktop["Service"]["Type"] == "notify"
    assert desktop["Service"]["RuntimeDirectoryMode"] == "0700"
    assert desktop["Service"]["User"] == "agent"
    for kind in ("cua", "view"):
        service = units[f"hermes-vm-{kind}@.service"]
        assert service["Unit"]["After"] == "hermes-vm-desktop@%i.service"
        assert service["Unit"]["PartOf"] == "hermes-vm-desktop@%i.service"
        assert shlex.split(service["Service"]["ExecStart"])[1] == "%i"
        assert service["Service"]["ExecStartPost"].endswith(f" {kind} %i")
    cua = shlex.split(units["hermes-vm-cua@.service"]["Service"]["ExecStart"])
    assert cua[cua.index("--permission-mode") + 1] == mode
    assert cua[cua.index("--socket") + 1] == "/run/hermes-vm-desktop-%i/cua.sock"
    # Cua authenticates Unix peer UIDs: readiness must run as its daemon user.
    assert units["hermes-vm-ready.service"]["Service"]["User"] == units["hermes-vm-cua@.service"]["Service"]["User"]
    expected = {f"hermes-vm-{kind}@{screen}.service" for kind in ("desktop", "cua", "view") for screen in (1, 2)}
    assert set(units["hermes-vm-ready.service"]["Unit"]["After"].split()) == expected | {"cloud-config.service"}
    assert seed["bootcmd"] == [["rm", "-f", "/var/lib/hermes-desktop-vm/ready"]]


@pytest.mark.parametrize("mode", ["bounded", "standard --other", "", None])
def test_seed_rejects_unknown_permissions(mode):
    with pytest.raises(ValueError, match="permission_mode"):
        build_cloud_config(PUBLIC, PRIVATE, PUBLIC, mode)


def test_public_key_cannot_inject_yaml_or_commands():
    with pytest.raises(ValueError, match="single-line"):
        build_cloud_config(PUBLIC + "\nruncmd: [false]", PRIVATE, PUBLIC)


@pytest.mark.linux_only
def test_screen_exec_has_no_inherited_environment_and_fails_closed(guest_files, tmp_path, monkeypatch):
    module = runpy.run_path(str(guest_files["hermes-vm-screen"]))
    runtime = tmp_path / "hermes-vm-desktop-2"
    runtime.mkdir()
    values = {
        "DISPLAY": ":2", "XAUTHORITY": str(runtime / "Xauthority"),
        "XDG_RUNTIME_DIR": str(runtime), "DBUS_SESSION_BUS_ADDRESS": "unix:path=/test/screen2",
        "XDG_CACHE_HOME": "/home/agent/.cache/screen2", "XDG_SESSION_TYPE": "x11",
        "XDG_CURRENT_DESKTOP": "XFCE", "GTK_MODULES": "gail:atk-bridge", "NO_AT_BRIDGE": "0",
    }
    (runtime / "environment.json").write_text(json.dumps(values))
    monkeypatch.setenv("PROVIDER_TEST_SECRET", "must-not-leak")
    environment = module["screen_environment"]("2", tmp_path)
    result = subprocess.run([sys.executable, "-c", "import os,json;print(json.dumps(dict(os.environ)))"],
                            env=environment, check=True, capture_output=True, text=True)
    actual = json.loads(result.stdout)
    assert "PROVIDER_TEST_SECRET" not in actual
    assert actual["DISPLAY"] == ":2" and actual["HOME"] == "/home/agent"
    assert actual["CUA_DRIVER_RS_TELEMETRY_ENABLED"] == "0"
    with pytest.raises(FileNotFoundError):
        module["screen_environment"]("1", tmp_path)
    values["DISPLAY"] = ":1"
    (runtime / "environment.json").write_text(json.dumps(values))
    with pytest.raises(ValueError, match="different screen"):
        module["screen_environment"]("2", tmp_path)


@pytest.mark.linux_only
@pytest.mark.parametrize("arguments", [["call", "list_windows", "{}"], ["tools"], ["describe", "click"]])
def test_cua_missing_or_stale_socket_never_spawns(guest_files, tmp_path, monkeypatch, arguments):
    # The canonical runner nests tmp_path beyond the Unix socket path limit.
    monkeypatch.chdir(tmp_path)
    tmp_path = Path(".")
    main = runpy.run_path(str(guest_files["hermes-vm-cua"]))["main"]
    run = Mock(side_effect=AssertionError("Must not execute or launch Cua"))
    with pytest.raises(FileNotFoundError):
        main(["1", *arguments], runtime_root=tmp_path, run=run)
    path = tmp_path / "hermes-vm-desktop-1"
    path.mkdir()
    with socket.socket(socket.AF_UNIX) as stale:
        stale.bind(str(path / "cua.sock"))
    with pytest.raises(ConnectionRefusedError):
        main(["1", *arguments], runtime_root=tmp_path, run=run)
    run.assert_not_called()


@pytest.mark.linux_only
@pytest.mark.parametrize("arguments", [["call", "list_windows", "{}", "--socket", "/other"],
                                        ["call", "--help"], ["tools", "--direct"], ["describe", "click", "serve"]])
def test_cua_rejects_endpoint_or_runtime_switches(guest_files, tmp_path, arguments):
    run = Mock(side_effect=AssertionError("No subprocess allowed"))
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(guest_files["hermes-vm-cua"]))["main"](["2", *arguments], runtime_root=tmp_path, run=run)
    assert error.value.code == 2
    run.assert_not_called()


@pytest.mark.linux_only
@pytest.mark.parametrize("action,args,method", [("tools", [], "list"), ("describe", ["click"], "describe"),
                                               ("call", ["list_windows", "{}"], "metadata")])
def test_cua_uses_only_assigned_live_socket(guest_files, tmp_path, monkeypatch, capsys, action, args, method):
    monkeypatch.chdir(tmp_path)
    tmp_path = Path(".")
    main = runpy.run_path(str(guest_files["hermes-vm-cua"]))["main"]
    directory = tmp_path / "hermes-vm-desktop-2"
    directory.mkdir()
    endpoint = directory / "cua.sock"
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(endpoint))
        server.listen()
        server.settimeout(5)

        def respond():
            with server.accept()[0] as connection, connection.makefile("rb") as stream:
                request = json.loads(stream.readline())
                connection.sendall(b'{"ok":true,"result":{"tools":[]}}\n')
                return request

        with ThreadPoolExecutor(max_workers=1) as executor:
            request = executor.submit(respond)
            assert main(["2", action, *args], runtime_root=tmp_path, run=run) == 0
            assert request.result(timeout=5)["method"] == method
    if action == "call":
        command = run.call_args.args[0]
        assert command[:2] == ["/usr/local/bin/hermes-vm-screen", "2"]
        assert command[-2:] == ["--socket", str(endpoint)]
        assert run.call_args.kwargs["env"]["CUA_DRIVER_RS_TELEMETRY_ENABLED"] == "0"
    else:
        assert json.loads(capsys.readouterr().out) == {"tools": []}
        run.assert_not_called()


def test_bootstrap_checks_bytes_before_extracting_and_keeps_support_files(guest_files, tmp_path):
    install = runpy.run_path(str(guest_files["bootstrap.py"]))["install_driver"]
    archive = tmp_path / "driver.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data in [("cua-driver", b"test executable"), ("libcua_driver_sdk.so", b"test library")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    destination = tmp_path / "installed"
    config = {"url": archive.as_uri(), "sha256": "0" * 64}
    with pytest.raises(RuntimeError, match="SHA-256"):
        install(config, destination)
    assert not destination.exists()
    config["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    install(config, destination)
    assert (destination / "cua-driver").read_bytes() == b"test executable"
    assert (destination / "libcua_driver_sdk.so").read_bytes() == b"test library"


@pytest.mark.parametrize("origin,host,allowed", [
    ("http://127.0.0.1:16081", "127.0.0.1:16081", True),
    ("http://localhost:45678", "localhost:45678", True),
    ("http://evil.example", "localhost:6081", False),
    ("http://localhost:6082", "localhost:6081", False),
    ("http://localhost.evil:6081", "localhost.evil:6081", False),
    ("http://evil@localhost:6081", "evil@localhost:6081", False),
    ("http://localhost:6081/", "localhost:6081", False),
    ("null", "localhost:6081", False), (None, "localhost:6081", False),
    ("https://localhost:6081", "localhost:6081", False),
])
def test_view_requires_exact_loopback_origin(guest_files, origin, host, allowed):
    view = runpy.run_path(str(guest_files["view.py"]))
    assert view["allowed_origin"](origin, host) is allowed


@pytest.mark.linux_only
def test_recording_no_overwrite_and_orderly_stop_contract(guest_files, tmp_path, capsys):
    main = runpy.run_path(str(guest_files["hermes-vm-record"]))["main"]
    running = False
    calls = []

    def run(command, **kwargs):
        nonlocal running
        calls.append(command)
        if command[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(command, 0, "active\n" if running else "inactive\n")
        if "systemd-run" in command:
            running = True
            Path(command[-1]).write_bytes(b"test FFmpeg output")
        elif "stop" in command:
            running = False
        elif command[0] == "ffprobe":
            assert not running
            return subprocess.CompletedProcess(command, 0, '{"format":{"duration":"1.0"}}')
        return subprocess.CompletedProcess(command, 0)

    assert main(["1", "start", "capture"], root=tmp_path, run=run) == 0
    started = json.loads(capsys.readouterr().out)
    command = next(command for command in calls if "systemd-run" in command)
    assert "--property=KillSignal=SIGINT" in command
    assert "--collect" in command and "-n" in command[command.index("/usr/bin/ffmpeg"):]
    assert started["recording"] and Path(started["path"]).exists()
    with pytest.raises(ValueError, match="already exists"):
        main(["2", "start", "capture"], root=tmp_path, run=run)
    assert main(["1", "stop"], root=tmp_path, run=run) == 0
    stopped = json.loads(capsys.readouterr().out)
    assert stopped["recording"] is False and stopped["probe"]["format"]["duration"] == "1.0"
    assert stopped["path"] == started["path"]
    with pytest.raises(ValueError, match="already exists"):
        main(["1", "start", "capture"], root=tmp_path, run=run)
