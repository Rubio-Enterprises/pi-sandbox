import json
import tomllib

import pytest

from pi_sandbox import Refusal
from pi_sandbox.config import Layer, merge, resolve, validate_profile, validate_section
from pi_sandbox.paths import Paths
from pi_sandbox.profile import sync


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
    layers = [
        Layer(
            '10-org',
            paths.root,
            {
                'credentials': {
                    'env': {'one': {'file': 'one', 'env': 'TOKEN', 'set': {'URL': 'org'}}}
                },
                'notice': {'lines': ['org']},
                'platform': {'linux': {'notice': {'lines': ['linux']}}},
            },
        ),
        Layer(
            '50-user',
            paths.root,
            {
                'credentials': {'env': {'one': {'set': {'EMAIL': 'user'}}}},
                'notice': {'lines': ['org', 'user']},
            },
        ),
    ]
    config = resolve(paths, 'linux', layers)
    assert config.slots['one'] == {
        'file': 'one',
        'env': 'TOKEN',
        'set': {'URL': 'org', 'EMAIL': 'user'},
    }
    assert config.entries('notice', 'lines') == ['org', 'linux', 'user']


@pytest.mark.parametrize(
    'value',
    [
        {},
        {'schema': True},
        {'schema': 2},
        {'schema': 1, 'unknown': []},
        {'schema': 1, 'platform': {'windows': {}}},
        {'schema': 1, 'layer': {'integrations': []}},
        {'schema': 1, 'env': {'pass': ['*']}},
        {'schema': 1, 'env': {'set': {'1BAD': 'x'}}},
        {'schema': 1, 'grants': {'ro_dirs': ['relative']}},
        {'schema': 1, 'pi': {'exclude_tools': ['hyphen-name']}},
        {'schema': 1, 'integrations': ['unknown']},
        {'schema': 1, 'hooks': [{'name': 'x', 'command': []}]},
        {'schema': 1, 'hooks': [{'name': 'x', 'command': ['true'], 'platforms': ['windows']}]},
        {'schema': 1, 'network': {'remote_port_deny': [{'port': 0, 'service': 'x'}]}},
        {'schema': 1, 'network': {'remote_port_deny': [{'port': True, 'service': 'x'}]}},
        {'schema': 1, 'network': {'remote_port_deny': [{'port': 65536, 'service': 'x'}]}},
        {'schema': 1, 'credentials': {'env': {'slot': {'file': '../bad', 'env': 'TOKEN'}}}},
        {'schema': 1, 'grants': {'project_state_dirs': {'origin': ['../escape']}}},
        {'schema': 1, 'platform': {'linux': {'schema': 1}}},
    ],
)
def test_invalid_schema(paths, value):
    with pytest.raises(Refusal):
        validate_section(value, paths, 'fixture', root=True)


@pytest.mark.parametrize(
    'value',
    [
        {'extends': 'default'},
        {'workdir': {}},
        {'unsafe_macos_seatbelt_rules': []},
        {'platform_overrides': {'macos': {'workdir': {}}}},
        {'meta': {'name': 'other'}},
    ],
)
def test_profile_fragment_restrictions(value):
    with pytest.raises(Refusal):
        validate_profile(value, 'pi-sandbox-layer-10-org', 'profile')


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


def test_discovery_profile_sync_and_stale_removal(fixture):
    config = resolve(fixture.paths, 'macos')
    assert [layer.name for layer in config.layers] == ['10-org', '50-user', '90-host']
    sync(config)
    root = fixture.paths.nono / 'profiles'
    installed = root / 'pi-sandbox-layer-50-user.json'
    assert json.loads(installed.read_text())['meta']['name'] == 'pi-sandbox-layer-50-user'
    before = installed.stat().st_mtime_ns
    sync(config)
    assert installed.stat().st_mtime_ns == before
    (root / 'pi-sandbox-layer-00-stale.json').write_text('{}')
    (root / 'unrelated.json').write_text('{}')
    sync(config)
    assert not (root / 'pi-sandbox-layer-00-stale.json').exists()
    assert (root / 'unrelated.json').exists()


@pytest.mark.parametrize(
    'slot', [{}, {'file': 'x'}, {'file': 'x', 'env': 'TOKEN', 'command': 'true'}]
)
def test_incomplete_merged_slot_refused(paths, slot):
    with pytest.raises(Refusal):
        resolve(
            paths, 'macos', [Layer('10-org', paths.root, {'credentials': {'env': {'slot': slot}}})]
        )


def test_example_layers_parse(fixture):
    from tests.support import REPO

    for directory in (REPO / 'examples/layers').glob('*'):
        value = tomllib.loads((directory / 'layer.toml').read_text())
        validate_section(value, fixture.paths, str(directory), root=True)
