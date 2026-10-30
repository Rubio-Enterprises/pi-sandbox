import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests.support import executable, file


def wait_file(path, process):
    deadline = time.monotonic() + 10
    while not path.exists():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, 'process never became ready'
        time.sleep(0.02)


def test_chmod_zero_tmux_cleanup_preserves_status_and_removes_credentials(fixture):
    executable(fixture.bins / 'pi', '#!/bin/sh\nchmod 000 "$TMUX_TMPDIR/tmux-$(id -u)"\nexit 3\n')
    result = fixture.run(extra={'TEST_SOCKETS': '1', 'TEST_BROWSER': 'namespace'})
    assert result.returncode == 3, result.stderr
    cap = fixture.capture()
    for name in ('TMUX_TMPDIR', 'AGENT_BROWSER_SOCKET_DIR', 'DOCKER_CONFIG', 'GIT_CONFIG_GLOBAL'):
        assert not Path(cap['env'][name]).exists()
    calls = (fixture.out / 'cleanup').read_text().splitlines()
    assert len(calls) == 4
    assert all('|-S|' in line for line in calls[:2])
    assert not any('hardlink' in line or '/link|' in line for line in calls)


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
@pytest.mark.parametrize('aliased', [False, True])
def test_hook_executable_in_rw_project_refused(fixture, os_name, aliased):
    workdir = fixture.repo if os_name == 'macos' else fixture.linked
    marker = fixture.out / 'hook-ran'
    target = executable(workdir / 'hook', '#!/bin/sh\ntouch ' + str(marker) + '\n')
    if aliased:
        alias = fixture.bins / 'hook-alias'
        alias.symlink_to(target)
        target = alias
    fixture.layer(
        '95-hook',
        'schema = 1\n[[hooks]]\nname = "inventory"\ncommand = ' + json.dumps([str(target)]) + '\n',
    )
    result = fixture.run(os_name)
    assert result.returncode == 2 and 'read-write grant' in result.stderr
    assert not marker.exists()
    assert not (fixture.out / 'capture.json').exists()
    assert 'GitHub read-only' not in result.stderr


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_refused_hook_precedes_all_credentials(fixture, os_name):
    marker = fixture.out / 'credential-command-ran'
    fixture.layer(
        '95-hook',
        'schema = 1\n[credentials.env.inference]\ncommand = '
        + json.dumps('touch ' + str(marker) + '; printf secret')
        + '\n[[hooks]]\nname = "refusal"\ncommand = ["sh", "-c", "echo reason >&2; exit 1"]\n',
    )
    result = fixture.run(os_name)
    assert result.returncode == 2
    assert 'hook refusal refused the launch\nreason' in result.stderr
    assert not marker.exists()
    assert not any(text in result.stderr for text in ('GitHub', 'ghcr', 'inference', 'First off'))


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_docker_socket_bypass_on_both_os_paths(fixture, os_name):
    with tempfile.TemporaryDirectory(prefix='socket.', dir='/tmp') as name:
        path = Path(name).resolve() / 'docker.sock'
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            fixture.layer('95-docker', 'schema = 1\n[docker]\nsocket = ' + json.dumps(str(path)))
            result = fixture.run(os_name)
            assert result.returncode == 0, result.stderr
            fs = fixture.capture()['profile']['filesystem']
            assert str(path) in fs['bypass_protection']
            assert ('unix_socket' in fs) == (os_name == 'macos')


def test_sigint_child_without_handler_is_interruptible(fixture):
    ready = fixture.out / 'default-child-ready'
    executable(fixture.bins / 'pi', '#!/bin/sh\ntouch ' + str(ready) + '\nexec sleep 120\n')
    process = subprocess.Popen(
        fixture.command(),
        env=fixture.env,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_file(ready, process)
        os.killpg(process.pid, signal.SIGINT)
        assert process.wait(timeout=5) == 130
        cap = fixture.capture()
        assert not Path(cap['env']['TMUX_TMPDIR']).exists()
        assert not Path(cap['env']['GIT_CONFIG_GLOBAL']).exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)


@pytest.mark.parametrize('ending', ['timeout', 'hup', 'term'])
def test_hook_subprocess_tree_is_stopped_and_reaped(fixture, ending):
    ready = fixture.out / 'tree.json'
    hook = executable(
        fixture.bins / 'tree-hook',
        f"""#!{sys.executable}
import json, os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
def stop(signum, frame):
    child.wait()
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
Path({str(ready)!r}).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(120)
""",
    )
    fixture.layer(
        '95-tree',
        'schema = 1\n[[hooks]]\nname = "tree"\ncommand = ' + json.dumps([str(hook)]) + '\n',
    )
    process = subprocess.Popen(
        fixture.command(),
        env=fixture.env,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    own = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    pids = []
    try:
        wait_file(ready, process)
        pids = json.loads(ready.read_text())
        before = time.monotonic()
        if ending != 'timeout':
            os.kill(process.pid, signal.SIGHUP if ending == 'hup' else signal.SIGTERM)
        assert process.wait(timeout=35) == {'timeout': 2, 'hup': 129, 'term': 143}[ending]
        if ending == 'timeout':
            assert 28 < time.monotonic() - before < 34
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert own.poll() is None, 'unrelated process was targeted'
        assert not list(fixture.paths.launch_state.glob('launch.*'))
        assert not (fixture.out / 'capture.json').exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        own.terminate()
        own.wait(timeout=5)


def test_hook_output_is_bounded(fixture):
    hook = executable(
        fixture.bins / 'large-hook', f'#!{sys.executable}\nprint("x" * (2 * 1024 * 1024))\n'
    )
    fixture.layer(
        '95-large',
        'schema = 1\n[[hooks]]\nname = "large"\ncommand = ' + json.dumps([str(hook)]) + '\n',
    )
    before = time.monotonic()
    result = fixture.run()
    assert result.returncode == 2 and 'hook large refused' in result.stderr
    assert time.monotonic() - before < 5
    assert len(result.stderr) < 1024


def test_empty_org_transition_preserves_credentials_and_host_state(fixture):
    (fixture.paths.root / 'layers/10-org/layer.toml').unlink()
    host = fixture.paths.root / 'layers/90-host/layer.toml'
    source = host.read_text()
    host.write_text('schema = 1\n' + source[source.index('[credentials.env.inference]') :])
    token = fixture.paths.root / 'credentials/pi-sandbox-github.token'
    original = token.read_bytes()
    canary = file(fixture.paths.root / 'host/canary', 'host-state')
    result = fixture.run()
    assert result.returncode == 0, result.stderr
    assert token.read_bytes() == original and canary.read_text() == 'host-state'
    assert (fixture.paths.root / 'layers/10-org').is_dir()


@pytest.mark.parametrize('source', ['native-layer', 'pi-pack'])
def test_hook_rejects_native_profile_and_pack_rw_grants(fixture, source):
    directory = fixture.paths.home / ('.pi/runtime' if source == 'pi-pack' else 'profile-rw')
    hook = executable(directory / 'hook', '#!/bin/sh\nexit 0\n')
    fragment = {'filesystem': {'allow': [str(directory)]}} if source == 'native-layer' else None
    fixture.layer(
        '95-hook',
        'schema = 1\n[[hooks]]\nname = "inventory"\ncommand = ' + json.dumps([str(hook)]) + '\n',
        fragment,
    )
    result = fixture.run()
    assert result.returncode == 2 and 'read-write grant' in result.stderr


def test_only_setup_and_prune_delete_old_unreferenced_profiles(fixture):
    root = fixture.paths.nono / 'profiles'
    root.mkdir(parents=True, exist_ok=True)
    old = file(root / 'pi-sandbox-layer-00-obsolete.json', '{}')
    age = time.time() - 48 * 60 * 60
    os.utime(old, (age, age))
    result = fixture.run()
    assert result.returncode == 0, result.stderr
    assert old.exists()
    assert fixture.ctl('validate').returncode == 0 and old.exists()
    assert fixture.ctl('setup').returncode == 0 and not old.exists()
    old.write_text('{}')
    os.utime(old, (age, age))
    result = fixture.ctl('prune')
    assert result.returncode == 0 and 'pruned 1 profiles' in result.stdout
    assert not old.exists()
