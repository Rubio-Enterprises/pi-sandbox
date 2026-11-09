import hashlib
import json
import os
from dataclasses import replace

import pytest

from pi_sandbox import Refusal, pack, profile
from pi_sandbox.config import Config, SandboxProfile, discover, resolve
from tests.support import file


@pytest.mark.parametrize('target', ['sidecar-directory', 'nono-directory', 'toml', 'json'])
def test_policy_symlinks_are_refused(fixture, target):
    root = fixture.paths.root if target in ('toml', 'sidecar-directory') else fixture.paths.nono
    path = root / 'profiles'
    if target in ('toml', 'json'):
        path /= 'user.' + target
    destination = fixture.repo / 'linked-policy'
    path.rename(destination)
    path.symlink_to(destination, target_is_directory=destination.is_dir())
    with pytest.raises(Refusal):
        discover(fixture.paths)


@pytest.mark.parametrize('kind', ['toml', 'json'])
@pytest.mark.parametrize('mode', [0o620, 0o602, 0o666])
def test_writable_policy_files_are_refused(fixture, kind, mode):
    root = fixture.paths.root if kind == 'toml' else fixture.paths.nono
    (root / 'profiles' / ('user.' + kind)).chmod(mode)
    with pytest.raises(Refusal, match='writable'):
        discover(fixture.paths)


def test_unselected_sidecars_are_never_discovered(fixture):
    file(fixture.paths.root / 'profiles/unselected.toml', 'invalid')
    assert len(discover(fixture.paths)) == 3
    result = fixture.ctl('chain')
    assert result.returncode == 0 and 'unselected.toml' not in result.stdout


def test_two_configs_cannot_replace_or_remove_another_chain(paths):
    sensitive = paths.home / 'context'
    a = Config(
        paths,
        [
            SandboxProfile(
                'user',
                paths.root,
                {},
                {
                    'filesystem': {'read': [str(sensitive)]},
                    'meta': {'name': 'pi-sandbox-user-50-user'},
                },
            )
        ],
    )
    b = Config(
        replace(paths, root=paths.home / 'other-config'),
        [
            SandboxProfile(
                'user',
                paths.root,
                {},
                {
                    'filesystem': {'allow': [str(sensitive)]},
                    'meta': {'name': 'pi-sandbox-user-50-user'},
                },
            )
        ],
    )
    profile.sync(a)
    chain_a = profile.chain(a)
    before = [(paths.nono / 'profiles' / (name + '.json')).read_bytes() for name in chain_a]
    profile.sync(b)
    assert chain_a != profile.chain(b)
    assert before == [(paths.nono / 'profiles' / (name + '.json')).read_bytes() for name in chain_a]
    for name, content in zip(chain_a, before, strict=True):
        assert name.endswith('-' + hashlib.sha256(content).hexdigest()[:12])


def test_profile_sync_never_overwrites_existing_bytes(paths):
    config = Config(paths)
    profile.sync(config)
    path = paths.nono / 'profiles' / (profile.chain(config)[0] + '.json')
    path.write_text('corrupt')
    with pytest.raises(Refusal):
        profile.sync(config)
    assert path.read_text() == 'corrupt'


@pytest.mark.parametrize('mutation', ['modified', 'malformed-digest', 'missing-digest'])
def test_pack_integrity_rejects_corruption(fixture, mutation):
    path = pack.directory(fixture.paths) / 'profiles/pi.json'
    content = path.read_bytes()
    lock = fixture.paths.nono / 'packages/lockfile.json'
    data = json.loads(lock.read_text())
    artifact = {'sha256': hashlib.sha256(content).hexdigest(), 'type': 'profile'}
    data['packages']['nolabs-ai/pi']['artifacts'] = {'profiles/pi.json': artifact}
    if mutation == 'modified':
        path.write_text('{"filesystem":{"allow":["/"]}}')
    elif mutation == 'malformed-digest':
        artifact['sha256'] = 'invalid'
    else:
        del artifact['sha256']
    lock.write_text(json.dumps(data))
    with pytest.raises(Refusal, match='integrity'):
        pack.require(fixture.paths)


@pytest.mark.parametrize('name', ['sidecar', 'nono'])
def test_writable_policy_directories_are_refused(fixture, name):
    ((fixture.paths.root if name == 'sidecar' else fixture.paths.nono) / 'profiles').chmod(0o777)
    with pytest.raises(Refusal, match='writable'):
        discover(fixture.paths)


def test_foreign_owned_policy_is_refused(fixture, monkeypatch):
    import stat

    from pi_sandbox import paths as module

    real = os.fstat

    def foreign(fd):
        info = real(fd)
        if stat.S_ISREG(info.st_mode):
            values = list(info)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(module.os, 'fstat', foreign)
    with pytest.raises(Refusal, match='user-owned'):
        discover(fixture.paths)


def test_symlink_swap_at_file_open_is_refused(fixture, monkeypatch):
    from pi_sandbox import paths as module

    target = fixture.paths.root / 'profiles/user.toml'
    inode = target.parent.stat().st_ino
    source = file(fixture.repo / 'policy.toml', '')
    real = os.open
    swapped = []

    def swapping(name, flags, mode=0o777, *, dir_fd=None):
        if name == 'user.toml' and dir_fd is not None and os.fstat(dir_fd).st_ino == inode:
            assert flags & os.O_NOFOLLOW
            target.unlink()
            target.symlink_to(source)
            swapped.append(True)
        return real(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, 'open', swapping)
    with pytest.raises(Refusal, match='without following symlinks'):
        discover(fixture.paths)
    assert swapped


def test_prune_retains_recent_current_and_pinned_profiles(paths):
    import time

    a = Config(
        paths, [SandboxProfile('user', paths.root, {}, {'filesystem': {'read': ['/strict']}})]
    )
    b = Config(
        replace(paths, root=paths.home / 'config-b'),
        [SandboxProfile('user', paths.root, {}, {'filesystem': {'allow': ['/permissive']}})],
    )
    handles = profile.sync(a, pin=True)
    profile.sync(b)
    root = paths.nono / 'profiles'
    stale = file(root / 'pi-sandbox-user-stale-000000000000.json', '{}')
    old = time.time() - 2 * 24 * 60 * 60
    for path in root.glob('pi-sandbox-*.json'):
        os.utime(path, (old, old))
    recent = file(root / 'pi-sandbox-user-recent-000000000000.json', '{}')
    unrelated = file(root / 'another-profile.json', '{}')
    os.utime(unrelated, (old, old))
    assert profile.prune(b) == 1
    assert not stale.exists() and recent.exists() and unrelated.exists()
    assert all((root / (name + '.json')).exists() for name in profile.chain(a))
    assert all((root / (name + '.json')).exists() for name in profile.chain(b))
    profile.release(handles)
    assert profile.prune(b) == 1
    assert not (root / (profile.chain(a)[-1] + '.json')).exists()


def test_native_credentials_deny_only_when_outside_engine_root(fixture):
    from pi_sandbox.cli import split
    from pi_sandbox.launch import preview
    from pi_sandbox.platform import Platform

    config = resolve(fixture.paths, 'macos')
    options = split(['--workdir=' + str(fixture.repo)])
    fs = preview(config, Platform('macos'), options)['filesystem']
    assert str(config.credentials_dir) not in fs['deny']
    config.values['credentials']['dir'] = str(fixture.paths.home / 'outside-credentials')
    fs = preview(config, Platform('macos'), options)['filesystem']
    assert str(config.credentials_dir) in fs['deny']


@pytest.mark.parametrize('stage', ['tmux', 'browser'])
def test_cleanup_steps_isolate_unexpected_errors(fixture, monkeypatch, stage):
    from unittest.mock import patch

    from pi_sandbox import launch
    from pi_sandbox.cli import split
    from pi_sandbox.platform import Platform

    failed = []

    def failure(root):
        failed.append(root)
        raise PermissionError('enumeration denied')

    monkeypatch.setattr(launch, 'child', lambda *args: 3)
    monkeypatch.setattr(launch, 'cleanup_' + stage, failure)
    with patch.dict(os.environ, fixture.env, clear=True):
        assert launch.launch(split(['--workdir=' + str(fixture.repo)]), Platform('macos')) == 3
    assert failed and not list(fixture.paths.launch_state.glob('launch.*'))
    for root in failed:
        launch.remove_private_tree(root)


def test_hook_validation_preserves_venv_executable_identity(paths):
    import venv

    from pi_sandbox import hooks

    directory = paths.home / 'venv'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(directory)
    binary = directory / 'bin/python'
    argv = hooks.executable([str(binary), '-c', 'import sys; print(sys.prefix)'], paths.home, [])
    assert argv[0] == str(binary)
    result = hooks.execute(argv, '', paths.home)
    assert result.returncode == 0 and result.stdout.strip() == str(directory)


def test_profile_pins_need_no_state_files_or_hardlink_aliases(paths):
    config = Config(paths)
    handles = profile.sync(config, pin=True)
    try:
        assert len(handles) == len(profile.chain(config))
        assert not paths.launch_state.exists()
        assert all(os.fstat(fd).st_nlink == 1 for fd in handles)
    finally:
        profile.release(handles)
