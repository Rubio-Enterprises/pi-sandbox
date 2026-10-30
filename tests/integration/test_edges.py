import json
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.support import executable


@pytest.mark.parametrize('name', ['pi', 'nono', 'git'])
def test_missing_tools_before_state(fixture, name):
    git = shutil.which('git', path=fixture.env['PATH'])
    (fixture.bins / 'git').symlink_to(git)
    (fixture.bins / name).unlink()
    result = fixture.run(extra={'PATH': str(fixture.bins)})
    assert result.returncode == 2 and name in result.stderr
    assert not fixture.paths.launch_state.exists()


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_no_layers_is_conservative(fixture, os_name):
    shutil.rmtree(fixture.paths.root / 'layers')
    shutil.rmtree(fixture.paths.root / 'credentials')
    result = fixture.run(os_name, extra={'GH_TOKEN': 'human', 'DOCKER_HOST': 'unix:///host'})
    assert result.returncode == 0, result.stderr
    cap = fixture.capture()
    assert cap['profile']['extends'] == ['pi-sandbox-base']
    assert 'GH_TOKEN' not in cap['env'] and 'DOCKER_HOST' not in cap['env']
    assert 'Keychains' not in json.dumps(cap['profile']['filesystem']['bypass_protection'])
    assert 'Docker is not configured' in cap['notice']
    assert cap['docker']['auths'] == {'https://index.docker.io/v1/': {}}
    assert not [p for p in cap['profile']['filesystem']['allow'] if p.endswith('/.aws')]
    assert str(fixture.paths.home / '.op') in cap['profile']['filesystem']['deny']


def test_only_existing_docker_socket_bypasses(fixture):
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='docker.') as name:
        socket_path = Path(name).resolve() / 'docker.sock'
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(socket_path))
            fixture.layer(
                '95-socket', 'schema = 1\n[docker]\nsocket = ' + json.dumps(str(socket_path))
            )
            result = fixture.run()
            assert result.returncode == 0, result.stderr
            fs = fixture.capture()['profile']['filesystem']
            assert fs['unix_socket'] == [str(socket_path)]
            assert str(socket_path) in fs['bypass_protection']


def test_linux_dangling_windows_symlink_is_not_a_directory(fixture):
    path = fixture.root / 'windows-link'
    path.symlink_to('/mnt/c/absent-project')
    result = fixture.run('linux', '--add-dirs=' + str(path), wsl=True)
    assert result.returncode == 2 and 'not a directory' in result.stderr


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_xdg_and_config_root_overrides(fixture, os_name):
    paths = fixture.paths
    config = paths.home / 'xdg/config'
    engine = paths.home / 'custom-engine'
    shutil.copytree(paths.root, engine)
    shutil.copytree(paths.nono, config / 'nono')
    result = fixture.run(
        os_name,
        extra={
            'PI_SANDBOX_CONFIG_DIR': str(engine),
            'XDG_CONFIG_HOME': str(config),
            'XDG_DATA_HOME': str(paths.home / 'xdg/data'),
            'XDG_STATE_HOME': str(paths.home / 'xdg/state'),
            'XDG_CACHE_HOME': str(paths.home / 'xdg/cache'),
        },
    )
    assert result.returncode == 0, result.stderr
    cap = fixture.capture()
    assert str(engine) in cap['profile']['filesystem']['deny']
    assert cap['env']['GIT_CONFIG_GLOBAL'].startswith(str(paths.home / 'xdg/state'))
    if os_name == 'macos':
        rules = '\n'.join(cap['profile']['unsafe_macos_seatbelt_rules'])
        assert str(config / 'nono/packages') in rules
        assert str(config / 'zsh') in rules
        assert str(engine) in rules


def test_production_entry_ignores_test_override_environment(fixture):
    from pi_sandbox.platform import Platform

    executable(
        fixture.bins / 'pi-sandbox',
        '#!' + sys.executable + '\nfrom pi_sandbox.cli import main\nraise SystemExit(main())\n',
    )
    platform = Platform.detect()
    result = subprocess.run(
        [str(fixture.bins / 'pi-sandbox'), '--workdir=' + str(fixture.linked)],
        env=fixture.env
        | {
            'PI_SANDBOX_PLATFORM': 'other',
            'PI_SANDBOX_KERNEL_RELEASE': '0.0',
            'PI_SANDBOX_TEST': '1',
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    if platform.os == 'linux' and tuple(map(int, platform.release.split('.')[:2])) < (6, 12):
        assert result.returncode == 2 and '6.12' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert fixture.capture()['argv'][0] == ('wrap' if platform.os == 'macos' else 'run')


def test_hook_timeout_refuses_and_never_creates_child(config, monkeypatch):
    from pi_sandbox import Refusal, hooks
    from pi_sandbox.platform import Platform

    config.values['hooks'] = [{'name': 'timeout', 'command': ['hook']}]

    def timeout(*args, **kwargs):
        assert kwargs['timeout'] == 30
        raise subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(hooks.subprocess, 'run', timeout)
    with pytest.raises(Refusal, match='hook timeout refused'):
        hooks.run(config, Platform('macos'), config.paths.home, config.paths.state)
