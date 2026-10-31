import json
from pathlib import Path

import pytest

from pi_sandbox import Refusal
from pi_sandbox.config import resolve
from pi_sandbox.git import Git
from pi_sandbox.hooks import run
from pi_sandbox.integrations import Grants
from pi_sandbox.linux import native_paths, validate_grants
from pi_sandbox.platform import Platform
from pi_sandbox.profile import keychain_safe
from pi_sandbox.seatbelt import (
    CHROME_RULE,
    HOST_SOCKET_RULE,
    PI_DIRS,
    PI_FILES,
    PS_RULE,
    git_rules,
    host_rules,
    quote,
    static_rules,
)
from pi_sandbox.wrapper import notice, render


def test_seatbelt_quoting():
    assert quote('a\\b"c') == '"a\\\\b\\"c"'


def test_static_rule_text_order_and_selectors(paths):
    rules = static_rules(paths, ['/protected'])
    assert (
        rules[1] == '(deny file-write* (require-all (subpath "/protected") '
        '(require-not (subpath "/protected/.git"))))'
    )
    assert rules[2:5] == [
        '(deny device-microphone)',
        '(deny process-exec (literal "/usr/bin/open") (literal "/usr/bin/osascript"))',
        PS_RULE,
    ]
    assert rules[-1] == HOST_SOCKET_RULE and rules[-3] == CHROME_RULE
    assert all(str(paths.home / directory) in rules[0] for directory in PI_DIRS)
    assert all(str(paths.home / '.pi/agent' / name) in rules[0] for name in PI_FILES)
    assert r'(regex #"/\.git/hooks(/|$)")' in rules[0]
    assert (
        r'(regex #"/\.git/(config(\.worktree)?|worktrees/[^/]+/config\.worktree)(\.lock)?$")'
        in rules[0]
    )
    assert not [r for r in rules if r.startswith('(allow') and 'Keychains' in r]


@pytest.mark.parametrize(
    'mode', ['missing-user', 'missing-system', 'bypass', 'raw', 'malformed', 'safe']
)
def test_keychain_predicate(paths, mode):
    manifest = {
        'filesystem': {
            'deny': [
                {'path': str(paths.home / 'Library/Keychains')},
                {'path': '/Library/Keychains'},
            ]
        }
    }
    resolved = {'filesystem': {'bypass_protection': []}, 'unsafe_macos_seatbelt_rules': []}
    if mode == 'missing-user':
        manifest['filesystem']['deny'].pop(0)
    elif mode == 'missing-system':
        manifest['filesystem']['deny'].pop()
    elif mode == 'bypass':
        resolved['filesystem']['bypass_protection'] = ['$HOME/Library/Keychains']
    elif mode == 'raw':
        resolved['unsafe_macos_seatbelt_rules'] = [
            '(allow mach-lookup (global-name "com.apple.security.agent"))'
        ]
    elif mode == 'malformed':
        manifest = {}
    assert keychain_safe(manifest, resolved, paths) == (mode == 'safe')


def test_git_config_realpaths_locks_and_hooks(fixture):
    git = Git.discover(fixture.linked)
    rules = git_rules(fixture.linked, git)
    assert len(rules) == 8
    assert all('.lock' in r for r in rules[:6])
    fixture.git('config', 'core.hooksPath', str(fixture.repo))
    with pytest.raises(Refusal, match='covers the workdir'):
        git_rules(fixture.repo, Git.discover(fixture.repo))


@pytest.mark.parametrize(
    'relative',
    [
        '.config',
        '.config/nono',
        '.config/zsh',
        '.config/pi-sandbox/credentials',
        '.config/nono/profiles',
    ],
)
def test_linux_overlap_both_directions(config, relative):
    with pytest.raises(Refusal, match='protected policy'):
        validate_grants([config.paths.home / relative], config, None, Platform('linux'))


@pytest.mark.parametrize('path', ['/mnt/c/project', '/mnt/D/context', '/mnt/c'])
def test_wsl_native_paths(path):
    with pytest.raises(Refusal, match='Windows drive'):
        native_paths([Path(path)], Platform('linux', '6.12', True))
    native_paths([Path(path)], Platform('linux', '6.12', False))


def test_host_rules_and_wrapper(config):
    rules = host_rules(config, Path('/launch'))
    assert str(config.paths.root) in rules[0]
    assert rules[-1].startswith('(deny file-write* (require-all')
    wrapper = render('/pi with space', Path('/launch'), ['one', 'one', 'two'], ['/skills'], op=True)
    assert "'/pi with space' --exclude-tools one,two --skill /skills" in wrapper
    assert 'unset PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN' in wrapper
    assert '"$@"' in wrapper
    text = notice(
        config,
        Platform('linux'),
        Grants(rw=['/rw'], ro=['/ro']),
        ['/runtime'],
        Path('/artifacts'),
        ['- Hook'],
    )
    for fragment in [
        'Operation not permitted',
        'wider launch',
        'NOT isolated',
        'Subagents are allowed',
        'Herdr',
        '/rw',
        '/ro',
        '/runtime',
        '/artifacts/screenshots',
        '- Hook',
        'not configured',
    ]:
        assert fragment in text
    assert 'mcp__' not in text


@pytest.mark.parametrize(
    'output,os,ok',
    [
        ('', 'macos', True),
        ('{}', 'linux', True),
        ('{"notice":["- hook"]}', 'linux', True),
        ('{"seatbelt_rules":["(deny test)"]}', 'macos', True),
        ('{"seatbelt_rules":["(deny test)"]}', 'linux', False),
        ('{"seatbelt_rules":["not a rule"]}', 'macos', False),
        ('[]', 'macos', False),
        ('{"notice":"not a list"}', 'macos', False),
        ('invalid', 'macos', False),
        ('{"unknown":[]}', 'macos', False),
    ],
)
def test_hook_output_protocol(fixture, output, os, ok):
    from tests.support import executable

    command = executable(
        fixture.bins / 'hook', f'#!{__import__("sys").executable}\nprint({output!r})\n'
    )
    config = resolve(fixture.paths, os)
    config.values['hooks'] = [{'name': 'fixture', 'command': [str(command)]}]
    if ok:
        rules, notices = run(config, Platform(os), fixture.repo, fixture.out, writable=[])
        if output.strip():
            assert rules == json.loads(output).get('seatbelt_rules', [])
            assert notices == json.loads(output).get('notice', [])
    else:
        with pytest.raises(Refusal, match='hook fixture refused'):
            run(config, Platform(os), fixture.repo, fixture.out, writable=[])


def test_hook_input_exit_and_stderr(fixture):
    from tests.support import executable

    command = executable(
        fixture.bins / 'hook', '#!/bin/sh\ncat > input.json\necho specific-reason >&2\nexit 2\n'
    )
    config = resolve(fixture.paths, 'macos')
    config.values['hooks'] = [{'name': 'fixture', 'command': [str(command)]}]
    with pytest.raises(Refusal, match='specific-reason'):
        run(config, Platform('macos'), fixture.repo, fixture.out, writable=[])
    assert json.loads((fixture.repo / 'input.json').read_text()) == {
        'schema': 1,
        'os': 'macos',
        'home': str(fixture.paths.home),
        'workdir': str(fixture.repo),
        'launch_dir': str(fixture.out),
    }
