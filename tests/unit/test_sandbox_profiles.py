import json

import pytest

from pi_sandbox import Refusal, profile
from pi_sandbox.cli import split
from pi_sandbox.config import discover, resolve, validate_section
from tests.support import file


def native(paths, name, extends=None, **fields):
    value = {'meta': {'name': name}, **fields}
    if extends is not None:
        value['extends'] = extends
    return file(paths.nono / 'profiles' / (name + '.json'), json.dumps(value))


def sidecar(paths, name, text):
    return file(paths.root / 'profiles' / (name + '.toml'), text)


def test_none_ignores_unselected_policy_and_nono_selection(paths, monkeypatch):
    monkeypatch.delenv('PI_SANDBOX_PROFILE', raising=False)
    monkeypatch.setenv('NONO_PROFILE', 'not-our-selection')
    sidecar(paths, 'broken', 'not TOML')
    assert resolve(paths, 'linux').profiles == []
    assert len(profile.chain(resolve(paths, 'linux'))) == 1


def test_selection_and_argument_boundary(paths, monkeypatch):
    native(paths, 'from-env')
    native(paths, 'from-cli')
    monkeypatch.setenv('PI_SANDBOX_PROFILE', 'from-env')
    assert resolve(paths, 'linux').selected == 'from-env'
    args = split(['--profile', 'from-cli', '-p', 'hello', '--', '--profile', 'pi-argument'])
    assert args.rest == ['-p', 'hello', '--', '--profile', 'pi-argument']
    assert resolve(paths, 'linux', selected=args.profile).selected == 'from-cli'
    assert split(['--profile=from-cli']).profile == 'from-cli'
    with pytest.raises(Refusal, match='does not exist'):
        resolve(paths, 'linux', selected='missing')


@pytest.mark.parametrize('args', [['--profile'], ['--profile='], ['--profile', '--scratch=x']])
def test_empty_selection_refused(args):
    with pytest.raises(Refusal, match='needs a Sandbox Profile name'):
        split(args)


def test_diamond_revisits_shared_bases_in_native_order(paths):
    native(paths, 'shared')
    native(paths, 'first', 'shared')
    native(paths, 'second', 'shared')
    native(paths, 'machine', ['first', 'second'])
    sidecar(paths, 'shared', '[workspace]\nscratch_root="~/shared"\n[notice]\nlines=["shared"]')
    sidecar(paths, 'first', '[workspace]\nscratch_root="~/first"\n[notice]\nlines=["first"]')
    sidecar(paths, 'machine', '[platform.linux.notice]\nlines=["linux"]')
    config = resolve(paths, 'linux', selected='machine')
    assert [item.name for item in config.profiles] == [
        'shared',
        'first',
        'shared',
        'second',
        'machine',
    ]
    assert config.block('workspace')['scratch_root'] == str(paths.home / 'shared')
    assert config.entries('notice', 'lines') == ['shared', 'first', 'linux']
    assert len(profile.snapshots(config)) == 5
    assert config.profiles[0] is config.profiles[2]


def test_non_user_bases_have_no_sidecars(paths):
    native(paths, 'machine', ['default', 'vendor/pack@1.0.0'])
    sidecar(paths, 'default', 'invalid')
    config = resolve(paths, 'linux', selected='machine')
    assert [item.name for item in config.profiles] == ['default', 'vendor/pack@1.0.0', 'machine']
    assert config.values == {}
    assert profile.snapshots(config)[profile.chain(config)[-1]]['extends'] == [
        'default',
        'vendor/pack@1.0.0',
    ]


@pytest.mark.parametrize('extends', ['loop', ['loop'], [1], {}, '../escape', '/absolute'])
def test_invalid_or_cyclic_inheritance(paths, extends):
    native(paths, 'loop', extends)
    with pytest.raises(Refusal):
        discover(paths, 'loop')


def test_depth_is_bounded_before_install(paths):
    for index in range(11):
        native(paths, 'p' + str(index), 'p' + str(index + 1) if index < 10 else None)
    with pytest.raises(Refusal, match='depth 10'):
        discover(paths, 'p0')


def test_snapshots_rewire_and_pin_every_user_ancestor(paths):
    parent = native(paths, 'parent', filesystem={'read': ['~/context']})
    native(paths, 'child', 'parent', unsafe_macos_seatbelt_rules=['(deny device-microphone)'])
    config = resolve(paths, 'linux', selected='child')
    installed = profile.snapshots(config)
    base, parent_name, child_name = installed
    assert installed[child_name]['extends'] == [parent_name]
    handles = profile.sync(config, pin=True)
    try:
        assert len(handles) == 3
        parent.write_text('{"filesystem":{"allow":["/"]}}')
        assert profile.snapshots(config) == installed
        changed = resolve(paths, 'linux', selected='child')
        assert profile.chain(changed) != [base, child_name]
        assert json.loads((paths.nono / 'profiles' / (parent_name + '.json')).read_text())[
            'filesystem'
        ] == {'read': ['~/context']}
    finally:
        profile.release(handles)


@pytest.mark.parametrize(
    'key,replacement',
    [
        ('ro_dirs', 'filesystem.read'),
        ('rw_dirs', 'filesystem.allow'),
        ('ro_files', 'filesystem.read_file'),
        ('ro_files_bypass', 'filesystem.bypass_protection'),
    ],
)
def test_moved_sidecar_grants_name_native_replacement(paths, key, replacement):
    with pytest.raises(Refusal, match=replacement):
        validate_section({'grants': {key: []}}, paths, 'sidecar', root=True)


@pytest.mark.parametrize(
    'key,replacement', [('env', 'environment'), ('network', 'unsafe_macos_seatbelt_rules')]
)
def test_moved_sections_name_native_replacement(paths, key, replacement):
    with pytest.raises(Refusal, match=replacement):
        validate_section({key: {}}, paths, 'sidecar', root=True)
