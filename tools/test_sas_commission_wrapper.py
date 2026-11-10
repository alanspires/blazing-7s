"""Execute the real tools/sas wrapper; SSH and rsync terminate at local process fakes."""

import os
import subprocess
from pathlib import Path

import pytest
import sas_commission as cm
from sas_testing import REPO, pattern, read_jsonl

ADAPTER = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with open(os.environ["CALLS"], "a") as out:
    out.write(json.dumps([name, *sys.argv[1:]]) + "\\n")
command = sys.argv[-1]
if name == "ssh" and "tools/sas_commission.py" in command:
    if "commission-capture" in command:
        sys.stdout.buffer.write(Path(os.environ["SOURCE"]).read_bytes())
    elif "commission-initialize" in command:
        Path(os.environ["RECEIVED"]).write_bytes(sys.stdin.buffer.read())
        print(json.dumps({"init_sent": True}))
    else:
        print(json.dumps({"mode": "UNKNOWN"}))
'''


@pytest.fixture
def remote(tmp_path):
    identity = cm.Identity(b"m" * cm.ROM_IDENTITY_SIZE, b"p" * cm.PERSONALITY_IDENTITY_SIZE, 1, 2)
    capture = pattern(cm.LAYOUT.capture_size, 3)
    archive = cm.Archive(capture, identity, cm.Provenance("/dev/test", "test", cm.CAPTURE_METHOD, "test"))
    source = tmp_path / "source.json"
    source.write_bytes(archive.encode())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ssh", "rsync"):
        script = bin_dir / name
        script.write_text(ADAPTER)
        script.chmod(0o755)
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                        "CALLS": str(tmp_path / "calls.jsonl"), "SOURCE": str(source),
                        "RECEIVED": str(tmp_path / "received.json"),
                        "SAS_HOST": "fake-host", "SAS_PORT": "/dev/test", "SAS_DIR": "/fake"}
    return archive, env


def run(env, *args):
    return subprocess.run([str(REPO / "tools" / "sas"), *args], cwd=REPO, env=env,
                          capture_output=True, check=False)


def calls(env):
    path = Path(env["CALLS"])
    return read_jsonl(path) if path.exists() else []


def serial_calls(env):
    return [call for call in calls(env) if call[0] == "ssh" and "tools/sas_commission.py" in call[-1]]


def test_capture_is_durable_local_and_init_receives_that_exact_archive(remote, tmp_path):
    archive, env = remote
    local = tmp_path / "local original's archive.json"
    capture = run(env, "commission-capture", str(local))
    assert capture.returncode == 0, capture.stderr
    assert cm.load_archive(local) == archive
    assert len(serial_calls(env)) == 1
    assert str(local) not in serial_calls(env)[0][-1]
    assert not Path(env["RECEIVED"]).exists()
    initialized = run(env, "commission-initialize", str(local), "--first-install")
    assert initialized.returncode == 0, initialized.stderr
    assert Path(env["RECEIVED"]).read_bytes() == local.read_bytes()
    assert len(serial_calls(env)) == 2
    assert all("-t" not in call for call in serial_calls(env))


def test_no_archive_overwrite_or_init_without_explicit_intent(remote, tmp_path):
    archive, env = remote
    local = tmp_path / "original.json"
    cm.save_archive(archive, local)
    assert run(env, "commission-capture", str(local)).returncode != 0
    assert run(env, "commission-initialize", str(local)).returncode != 0
    assert cm.load_archive(local) == archive
    assert not calls(env)
    assert not Path(env["RECEIVED"]).exists()


def test_invalid_local_archive_never_reaches_remote_initializer(remote, tmp_path):
    _, env = remote
    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"{}")
    result = run(env, "commission-initialize", str(invalid), "--first-install")
    assert result.returncode != 0
    # The remote CLI separately refuses empty stdin before opening its serial port.
    assert Path(env["RECEIVED"]).read_bytes() == b""
    assert len(serial_calls(env)) == 1
