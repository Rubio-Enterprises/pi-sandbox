import json
import tomllib

import pytest

from pi_sandbox import Refusal
from pi_sandbox.config import SandboxProfile, merge, resolve, validate_section
from pi_sandbox.paths import Paths
from pi_sandbox.profile import snapshots, sync


def test_merge_has_no_aliases_and_preserves_order():
    left = {'a': [1, 2], 'b': {'scalar': 1}, 'hooks': [{'name': 'first', 'command': ['old']}]}
    right = {'a': [2, 3], 'b': {'scalar': 2}, 'hooks': [{'name': 'first', 'command': ['new']}]}
    assert merge(left, right) == {
        'a': [1, 2, 3],
        'b': {'scalar': 2},
        'hooks': [{'name': 'first', 'command': ['new']}],
    }
    assert left['a'] == [1, 2] and left['b']['scalar'] == 1


def test_platform_merge_and_slot_extension(paths):
    profiles = [
        SandboxProfile(
            'org',
            paths.root,
            {
                'credentials': {
                    'env': {'one': {'file': 'one', 'env': 'TOKEN', 'set': {'URL': 'org'}}}
                },
                'notice': {'lines': ['org']},
                'platform': {'linux': {'notice': {'lines': ['linux']}}},
            },
        ),
        SandboxProfile(
            'user',
            paths.root,
            {
                'credentials': {'env': {'one': {'set': {'EMAIL': 'user'}}}},
                'notice': {'lines': ['org', 'user']},
            },
        ),
    ]
    config = resolve(paths, 'linux', profiles)
    assert config.slots['one'] == {
        'file': 'one',
        'env': 'TOKEN',
        'set': {'URL': 'org', 'EMAIL': 'user'},
    }
    assert config.entries('notice', 'lines') == ['org', 'linux', 'user']


@pytest.mark.parametrize(
    'value',
    [
        {'schema': True},
        {'schema': 2},
        {'unknown': []},
        {'platform': {'windows': {}}},
        {'layer': {'integrations': []}},
        {'env': {'pass': ['*']}},
        {'env': {'set': {'1BAD': 'x'}}},
        {'grants': {'ro_dirs': ['relative']}},
        {'pi': {'exclude_tools': ['hyphen-name']}},
        {'integrations': ['unknown']},
        {'hooks': [{'name': 'x', 'command': []}]},
        {'hooks': [{'name': 'x', 'command': ['true'], 'platforms': ['windows']}]},
        {'network': {'remote_port_deny': [{'port': 0, 'service': 'x'}]}},
        {'network': {'remote_port_deny': [{'port': True, 'service': 'x'}]}},
        {'network': {'remote_port_deny': [{'port': 65536, 'service': 'x'}]}},
        {'credentials': {'env': {'slot': {'file': '../bad', 'env': 'TOKEN'}}}},
        {'grants': {'project_state_dirs': {'origin': ['../escape']}}},
        {'platform': {'linux': {'schema': 1}}},
        {'docker': {'default_credential_build_repositories': 'ghcr.io/example/base'}},
        *[
            {'docker': {'default_credential_build_repositories': [repository]}}
            for repository in (
                '',
                'example/base:latest',
                'ghcr.io',
                'ghcr.io/',
                'ghcr.io/Example/base',
                'ghcr.io/example/*',
                'ghcr.io/example/base@sha256:abc',
                'ghcr.io/../base',
                'ghcr.io/example/./base',
                'ghcr.io//base',
                '/ghcr.io/base',
                'ghcr.io/example/base/',
                'ghcr.io/config.json',
                'ghcr.io/example/base\n',
                'host name/base',
            )
        ],
    ],
)
def test_invalid_schema(paths, value):
    with pytest.raises(Refusal):
        validate_section(value, paths, 'fixture', root=True)


@pytest.mark.parametrize('value', ['~/path', '$HOME/path', '${HOME}/path'])
def test_home_expansion(paths, value):
    assert paths.expand(value) == str(paths.home / 'path')


@pytest.mark.parametrize(
    'name,default',
    [
        ('CONFIG', '.config'),
        ('DATA', '.local/share'),
        ('STATE', '.local/state'),
        ('CACHE', '.cache'),
    ],
)
def test_xdg_defaults_and_overrides(paths, name, default):
    assert paths.expand(f'$XDG_{name}_HOME/path') == str(paths.home / default / 'path')
    replacement = paths.home / 'override'
    changed = Paths.from_env({'HOME': str(paths.home), f'XDG_{name}_HOME': str(replacement)})
    assert changed.expand(f'${{XDG_{name}_HOME}}/path') == str(replacement / 'path')


@pytest.mark.parametrize('value', ['relative', '$UNKNOWN/path', '~/line\nbreak', '~/nul\x00'])
def test_invalid_paths(paths, value):
    with pytest.raises(Refusal):
        paths.expand(value)


def test_discovery_profile_sync_preserves_existing_profiles(fixture):
    config = resolve(fixture.paths, 'macos')
    assert [item.name for item in config.profiles] == ['org', 'user', 'host']
    sync(config)
    root = fixture.paths.nono / 'profiles'
    installed = root / (list(snapshots(config))[2] + '.json')
    assert json.loads(installed.read_text())['meta']['name'] == 'user'
    before = installed.stat().st_mtime_ns
    sync(config)
    assert installed.stat().st_mtime_ns == before
    (root / 'pi-sandbox-user-00-stale.json').write_text('{}')
    (root / 'unrelated.json').write_text('{}')
    sync(config)
    assert (root / 'pi-sandbox-user-00-stale.json').exists()
    assert (root / 'unrelated.json').exists()


@pytest.mark.parametrize(
    'slot', [{}, {'file': 'x'}, {'file': 'x', 'env': 'TOKEN', 'command': 'true'}]
)
def test_incomplete_merged_slot_refused(paths, slot):
    with pytest.raises(Refusal):
        resolve(
            paths,
            'macos',
            [SandboxProfile('org', paths.root, {'credentials': {'env': {'slot': slot}}})],
        )


def test_example_profiles_parse(fixture):
    from tests.support import REPO

    for directory in (REPO / 'examples/profiles').glob('*.toml'):
        value = tomllib.loads(directory.read_text())
        validate_section(value, fixture.paths, str(directory), root=True)


@pytest.mark.parametrize(
    'repository',
    [
        'ghcr.io/example/base',
        'ghcr.io/example/team/app-1_x.y',
        'registry.example:5000/app',
        'localhost/app',
    ],
)
def test_default_credential_build_repositories_accept_canonical_names(paths, repository):
    value = {'docker': {'default_credential_build_repositories': [repository]}}
    result = validate_section(value, paths, 'fixture', root=True)
    assert result['docker']['default_credential_build_repositories'] == [repository]


def test_default_credential_build_repositories_merge_across_profiles(paths):
    config = resolve(
        paths,
        'macos',
        [
            SandboxProfile(
                'a',
                paths.root,
                {'docker': {'default_credential_build_repositories': ['ghcr.io/a/b']}},
            ),
            SandboxProfile(
                'b',
                paths.root,
                {
                    'docker': {
                        'default_credential_build_repositories': ['ghcr.io/a/b', 'ghcr.io/c/d']
                    }
                },
            ),
        ],
    )
    assert config.default_credential_build_repositories == ['ghcr.io/a/b', 'ghcr.io/c/d']
