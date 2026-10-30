import itertools

import pytest

from pi_sandbox import Refusal
from pi_sandbox.config import Config
from pi_sandbox.credentials import Credentials, load
from pi_sandbox.environment import BASE_VARS, CACHES, UNSET, build
from pi_sandbox.platform import Platform


def environment(config, caller=None, credentials=None, os='macos'):
    root = config.paths.state
    return build(
        config,
        Platform(os),
        caller or {},
        root / 'launch',
        root / 'browser',
        root / 'tmux',
        root / 'artifacts',
        credentials or Credentials(),
        ('User', 'user@example.invalid'),
        root,
    )


@pytest.mark.parametrize(
    'source,value,expected',
    [
        ('', '', None),
        ('SSH_CONNECTION', 'client', '1'),
        ('SSH_CLIENT', 'client', '1'),
        ('MOSH_CONNECTION', 'client', '1'),
        ('PI_CLIPBOARD_OSC52', '0', '0'),
        ('PI_CLIPBOARD_OSC52', '1', '1'),
    ],
)
def test_clipboard(config, source, value, expected):
    env = environment(config, {source: value} if source else {})
    assert env.inherited.get('PI_CLIPBOARD_OSC52') == expected
    assert ('PI_CLIPBOARD_OSC52' in env.allow) == (expected is not None)


def test_allowlist_and_set_vars(config):
    caller = {name: 'canary' for name in UNSET}
    caller.update(
        PI_ASK_USER_ALLOW_COMMENT='true',
        PI_CANARY_SECRET='leak',
        SANDBOX_ENV_PASS='SAFE * 1BAD BAD-NAME',
        SAFE='safe',
        PATH='/bin',
    )
    env = environment(config, caller)
    assert set(BASE_VARS) <= set(env.allow)
    assert all(n not in env.inherited for n in UNSET)
    assert env.inherited['SAFE'] == 'safe' and 'SAFE' in env.allow
    assert 'PI_CANARY_SECRET' not in env.allow
    assert 'PI_ASK_USER_ALLOW_COMMENT' in env.allow
    assert not {'*', '1BAD', 'BAD-NAME'} & set(env.allow)
    for name in CACHES:
        assert env.inherited[name].startswith(str(config.paths.private_cache))
    assert set(env.set_vars) == {'JAVA_TOOL_OPTIONS', 'GEM_HOME', 'GEM_PATH'}
    assert 'GH_TOKEN' not in env.allow and 'GH_TOKEN' not in env.inherited


STATES = ['present', 'empty', 'absent']


@pytest.mark.parametrize('states', list(itertools.product(STATES, repeat=3)))
@pytest.mark.parametrize('os', ['macos', 'linux'])
def test_credential_function_matrix(fixture, states, os):
    from pi_sandbox.config import resolve

    config = resolve(fixture.paths, os)
    for name, state in zip(('first', 'second', 'chat'), states, strict=True):
        path = config.credentials_dir / (name + '.token')
        if state == 'absent':
            path.unlink()
        elif state == 'empty':
            path.write_text('\n')
    creds = load(config)
    caller = dict.fromkeys(
        [
            'EXAMPLE_FIRST_TOKEN',
            'EXAMPLE_SECOND_TOKEN',
            'EXAMPLE_URL',
            'EXAMPLE_EMAIL',
            'CHAT_TOKEN',
            'EXAMPLE_ADMIN_TOKEN',
        ],
        'caller',
    )
    caller['SANDBOX_ENV_PASS'] = ' '.join(caller) + ' SAFE *'
    caller['SAFE'] = 'safe'
    env = environment(config, caller, creds, os)
    for name, variable, state in zip(
        ('first', 'second', 'chat'),
        ('EXAMPLE_FIRST_TOKEN', 'EXAMPLE_SECOND_TOKEN', 'CHAT_TOKEN'),
        states,
        strict=True,
    ):
        enabled = state == 'present'
        assert (variable in env.allow) == (variable in env.inherited) == enabled
        if enabled:
            assert env.inherited[variable] == name + '_fixture'
        assert (
            any(message.startswith(name.title() + ' off:') for message in creds.messages) != enabled
        )
        assert any(name.title() + ' is reachable' in line for line in creds.notices) == enabled
    assert ('EXAMPLE_URL' in env.inherited) == any(state == 'present' for state in states[:2])
    assert ('EXAMPLE_EMAIL' in env.inherited) == any(state == 'present' for state in states[:2])
    assert 'EXAMPLE_ADMIN_TOKEN' not in env.inherited
    assert 'SAFE' in env.allow


@pytest.mark.parametrize('github,packages', list(itertools.product(STATES, repeat=2)))
def test_builtin_token_readers(fixture, github, packages):
    config = Config(fixture.paths)
    for name, state in [('github', github), ('ghcr', packages)]:
        path = config.credentials_dir / f'pi-sandbox-{name}.token'
        if state == 'absent':
            path.unlink()
        elif state == 'empty':
            path.write_text('\n\n')
    creds = load(config)
    assert bool(creds.github) == (github == 'present')
    assert bool(creds.ghcr) == (packages == 'present')
    assert bool([line for line in creds.messages if 'private ghcr' in line]) == (
        packages != 'present'
    )
    assert any('GitHub read-only' in line for line in creds.messages) == (github == 'present')


def test_dollar_in_generated_set_value_refused(config):
    config.paths.private_cache.parent.mkdir(parents=True)
    config.values = {'docker': {'socket': '/path/$dollar'}}
    with pytest.raises(Refusal, match='must not contain'):
        environment(config)
