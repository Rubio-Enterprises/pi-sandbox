import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from pi_sandbox import profile
from pi_sandbox.config import resolve
from tests.support import HOST_PATH, make_fixture
from tests.unit.test_sandbox_profiles import native, sidecar

pytestmark = pytest.mark.live


@pytest.fixture
def native_fixture():
    nono = shutil.which('nono', path=HOST_PATH)
    if nono is None:
        pytest.skip('real nono is absent')
    cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'pi-sandbox-tests'
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='native.', dir=cache) as name:
        fixture = make_fixture(Path(name))
        yield fixture, nono


def show(fixture, nono, path, *flags):
    return subprocess.run(
        [nono, 'profile', 'show', str(path), *flags],
        env=fixture.env,
        cwd=fixture.linked,
        capture_output=True,
        text=True,
        check=False,
    )


def test_native_diamond_order_and_snapshot_resolution(native_fixture):
    fixture, nono = native_fixture
    paths = fixture.paths
    native(paths, 'shared', filesystem={'read': ['~/context']}, network={'block': True})
    native(paths, 'first', 'shared', network={'block': False})
    native(paths, 'second', 'shared')
    native(paths, 'machine', ['first', 'second'], security={'ipc_mode': 'full'})
    sidecar(paths, 'shared', '[workspace]\nscratch_root="~/shared"')
    sidecar(paths, 'first', '[workspace]\nscratch_root="~/first"')
    config = resolve(paths, 'linux', selected='machine')
    profile.sync(config)
    snapshot = paths.nono / 'profiles' / (profile.chain(config)[-1] + '.json')
    result = show(fixture, nono, snapshot, '--json')
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['network']['block']
    assert value['security']['ipc_mode'] == 'full'
    assert config.block('workspace')['scratch_root'] == str(paths.home / 'shared')
    native(paths, 'shared', filesystem={'allow': ['/']})
    again = show(fixture, nono, snapshot, '--json')
    assert again.stdout == result.stdout


def test_native_rule_order_keeps_safety_tail_last(native_fixture):
    fixture, nono = native_fixture
    paths = fixture.paths
    git = '(deny file-write* (literal "/synthetic/git-config"))'
    common = '(deny network-outbound (remote ip "*:8080"))'
    platform = '(allow network-outbound (remote ip "localhost:8080"))'
    tail = '(deny device-microphone)'
    native(
        paths,
        'policy',
        unsafe_macos_seatbelt_rules=[common],
        platform_overrides={
            'linux': {'unsafe_macos_seatbelt_rules': [platform]},
            'macos': {'unsafe_macos_seatbelt_rules': [platform]},
        },
    )
    config = resolve(paths, 'linux', selected='policy')
    profile.sync(config)
    # A synthetic prefix keeps native ordering tests independent of pack downloads
    native(paths, 'prefix', unsafe_macos_seatbelt_rules=[git])
    top = native(
        paths,
        'top',
        ['prefix', profile.chain(config)[-1]],
        platform_overrides={
            'linux': {'unsafe_macos_seatbelt_rules': [tail]},
            'macos': {'unsafe_macos_seatbelt_rules': [tail]},
        },
    )
    result = show(fixture, nono, top, '--json')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['unsafe_macos_seatbelt_rules'] == [git, common, platform, tail]


def test_native_write_projection_excludes_implicit_group_writes(native_fixture, monkeypatch):
    fixture, nono = native_fixture
    monkeypatch.setenv('PATH', str(Path(nono).parent) + ':' + HOST_PATH)
    monkeypatch.setenv('HOME', str(fixture.paths.home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(fixture.paths.config))
    directory = fixture.paths.home / 'native-write'
    directory.mkdir()
    path = native(fixture.paths, 'writes', filesystem={'allow': ['~/native-write']})
    result = profile.native_writable_grants(path, fixture.linked, fixture.out / 'projection.json')
    assert result == [str(directory)]
    assert not (fixture.out / 'projection.json').exists()


def test_native_unknown_fields_are_not_silently_accepted(native_fixture):
    fixture, nono = native_fixture
    native(fixture.paths, 'unknown', filesystem={'engine_typo': []})
    config = resolve(fixture.paths, 'linux', selected='unknown')
    profile.sync(config)
    path = fixture.paths.nono / 'profiles' / (profile.chain(config)[-1] + '.json')
    result = show(fixture, nono, path, '--json')
    assert result.returncode and 'engine_typo' in result.stderr


def test_native_deny_globs_and_explicit_bridge(native_fixture):
    fixture, nono = native_fixture
    path = native(
        fixture.paths,
        'environment',
        environment={
            'allow_vars': ['PATH', 'SAFE', 'EXAMPLE_*'],
            'deny_vars': ['EXAMPLE_*'],
            'set_vars': {'STATIC_VALUE': 'native'},
        },
    )
    result = subprocess.run(
        [
            nono,
            'wrap',
            '-s',
            '--profile',
            str(path),
            '--env-credential-map',
            'env://EXAMPLE_BRIDGED',
            'EXAMPLE_BRIDGED',
            '--',
            '/usr/bin/env',
        ],
        env=fixture.env | {'SAFE': 'yes', 'EXAMPLE_HUMAN': 'no', 'EXAMPLE_BRIDGED': 'synthetic'},
        cwd=fixture.linked,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode and any(
        word in result.stderr for word in ('Landlock', 'landlock', 'seccomp')
    ):
        pytest.skip('container kernel cannot enforce nono: ' + result.stderr.strip())
    assert result.returncode == 0, result.stderr
    assert 'SAFE=yes' in result.stdout and 'STATIC_VALUE=native' in result.stdout
    assert 'EXAMPLE_HUMAN=' not in result.stdout
    assert 'EXAMPLE_BRIDGED=synthetic' in result.stdout
