import copy
import shlex

import pytest

from pi_sandbox import Refusal
from pi_sandbox.config import Config, Layer, normalize_mcp_name, resolve, validate_section
from pi_sandbox.wrapper import render


@pytest.mark.parametrize('platform', [None, 'macos', 'linux'])
@pytest.mark.parametrize(
    'mcp',
    [
        [],
        {'': {}},
        {'bad.name': {}},
        {'bad/name': {}},
        {'bad name': {}},
        {'bad\n': {}},
        {'bad*': {}},
        {'example': []},
        {'example': {'unknown': []}},
        {'example': {'hide_tools': 'delete_*'}},
        {'example': {'hide_tools': [1]}},
        {'example': {'names': 'alias'}},
        {'example': {'names': [1]}},
        {'example': {'names': ['']}},
        {'example': {'names': ['bad.name']}},
        {'example': {'names': ['bad/name']}},
        {'example': {'names': ['bad name']}},
        {'example': {'names': ['bad\n']}},
        {'example': {'names': ['bad*']}},
    ],
)
def test_invalid_mcp_schema(paths, mcp, platform):
    settings = {'pi': {'mcp': mcp}}
    value = {'schema': 1} | (settings if platform is None else {'platform': {platform: settings}})
    with pytest.raises(Refusal, match=r'pi\.mcp'):
        validate_section(value, paths, 'fixture', root=True)


@pytest.mark.parametrize(
    'pattern',
    [
        '',
        ' ',
        'delete tool',
        'a,b',
        'a\tb',
        'a\nb',
        'a\rb',
        'a\x00b',
        'a\x1bb',
        'a\x7fb',
        'a\x85b',
        'a\x9fb',
        'a\u00a0b',
        'a\u2003b',
    ],
)
def test_invalid_mcp_tool_patterns(paths, pattern):
    value = {'schema': 1, 'pi': {'mcp': {'example': {'hide_tools': [pattern]}}}}
    with pytest.raises(Refusal, match=r'pi\.mcp\.example\.hide_tools'):
        validate_section(value, paths, 'fixture', root=True)


@pytest.mark.parametrize('platform', ['macos', 'linux'])
def test_mcp_layer_and_platform_merge(paths, platform):
    first = {
        'schema': 1,
        'pi': {'mcp': {'example': {'hide_tools': ['delete-*'], 'names': ['shared']}}},
        'platform': {
            'macos': {'pi': {'mcp': {'example': {'hide_tools': ['mac.*'], 'names': ['mac']}}}},
            'linux': {'pi': {'mcp': {'example': {'hide_tools': ['linux/*'], 'names': ['linux']}}}},
        },
    }
    second = {
        'schema': 1,
        'pi': {
            'mcp': {
                'example': {'hide_tools': ['delete-*', 'update.*'], 'names': ['shared', 'local']}
            }
        },
        'platform': {platform: {'pi': {'mcp': {'example': {'names': ['local', 'host']}}}}},
    }
    originals = copy.deepcopy([first, second])
    layers = [
        Layer(name, paths.root, validate_section(value, paths, name, root=True))
        for name, value in [('10-org', first), ('50-user', second)]
    ]
    config = resolve(paths, platform, layers)
    assert config.mcp == {
        'example': {
            'hide_tools': ['delete-*', 'mac.*' if platform == 'macos' else 'linux/*', 'update.*'],
            'names': ['shared', 'mac' if platform == 'macos' else 'linux', 'local', 'host'],
        }
    }
    assert [first, second] == originals


@pytest.mark.parametrize(
    'raw,normalized',
    [
        ('delete-item', 'delete_item'),
        ('write.file', 'write_file'),
        ('api/remove', 'api_remove'),
        ('*.remove-*/*', '*_remove_*_*'),
        ('Already_OK9', 'Already_OK9'),
        ('café', 'caf_'),
    ],
)
def test_mcp_tool_normalization(paths, raw, normalized):
    values = {'pi': {'mcp': {'Example-api': {'hide_tools': [raw], 'names': ['local-api']}}}}
    config = Config(paths, values=values)
    assert config.excluded_tools == [
        f'mcp__Example_api__{normalized}',
        f'mcp__local_api__{normalized}',
    ]
    assert normalize_mcp_name('local-api') == normalize_mcp_name('local_api')


def test_mcp_exclusions_deduplicate_flat_aliases_tools_and_cli(paths):
    values = {
        'pi': {
            'exclude_tools': ['unsafe_*', 'mcp__example_api__delete_*'],
            'mcp': {
                'example-api': {
                    'hide_tools': ['delete-*', 'delete_*', 'delete.*', 'delete/*', 'update.*'],
                    'names': ['example-api', 'example_api', 'local-api', 'local_api'],
                },
                'local-api': {'hide_tools': ['update.*']},
            },
        }
    }
    original = copy.deepcopy(values)
    config = Config(paths, values=values)
    expected = [
        'unsafe_*',
        'mcp__example_api__delete_*',
        'mcp__example_api__update_*',
        'mcp__local_api__delete_*',
        'mcp__local_api__update_*',
    ]
    assert config.excluded_tools == expected
    script = render(
        '/pi',
        paths.home,
        [*config.excluded_tools, 'unsafe_*', expected[-1], 'cli_*', 'cli_*'],
        [],
        op=False,
    )
    argv = shlex.split(script.splitlines()[-1])
    assert argv.count('--exclude-tools') == 1
    assert argv[argv.index('--exclude-tools') + 1] == ','.join([*expected, 'cli_*'])
    assert config.values == original


def test_mcp_lists_deduplicate_within_first_layer(paths):
    value = {
        'schema': 1,
        'pi': {
            'mcp': {
                'example': {
                    'hide_tools': ['delete-*', 'delete-*', 'write.file'],
                    'names': ['local', 'local', 'other'],
                }
            }
        },
    }
    original = copy.deepcopy(value)
    validated = validate_section(value, paths, 'fixture', root=True)
    config = resolve(paths, 'macos', [Layer('10-org', paths.root, validated)])
    assert config.mcp['example'] == {
        'hide_tools': ['delete-*', 'write.file'],
        'names': ['local', 'other'],
    }
    assert value == original


def test_mcp_empty_and_alias_only_tables(paths):
    value = {
        'schema': 1,
        'pi': {'mcp': {'_': {}, '9': {'names': ['-']}, 'example': {'hide_tools': []}}},
    }
    validated = validate_section(value, paths, 'fixture', root=True)
    assert Config(paths, values=validated).excluded_tools == []
