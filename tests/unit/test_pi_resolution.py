import os
import subprocess

import pytest

from pi_sandbox import Refusal, wrapper
from pi_sandbox.launch import tool
from tests.support import executable


@pytest.fixture
def search(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    for kind in ('CONFIG', 'DATA', 'STATE', 'CACHE'):
        monkeypatch.setenv(f'XDG_{kind}_HOME', str(tmp_path / kind.lower()))
    for name in list(os.environ):
        if name.startswith(('MISE_', 'PI_SUBAGENT_')) or name in ('TMUX', 'TMUX_PANE'):
            monkeypatch.delenv(name)
    bins = [tmp_path / name for name in ('shims', 'later')]
    for directory in bins:
        directory.mkdir()
    monkeypatch.setenv('PATH', os.pathsep.join(map(str, bins)))
    return tmp_path, bins


def shim(root, directory, answer='', status=1):
    mise = executable(
        root / 'mise',
        '#!/bin/sh\n[ "$1 $2" = "which pi" ] || exit 99\n'
        f"printf '%s\\n' '{answer}'\nexit {status}\n",
    )
    (directory / 'pi').symlink_to(mise)
    return mise


def test_plain_pi_is_unchanged(search):
    root, bins = search
    pi = executable(bins[0] / 'pi', '#!/bin/sh\nexit 0\n')
    assert tool('pi') == str(pi)
    pi.unlink()
    target = executable(root / 'real-pi', '#!/bin/sh\nexit 0\n')
    pi.symlink_to(target)
    assert tool('pi') == str(pi)


@pytest.mark.parametrize('other_pi', [False, True])
def test_active_shim_prefers_configured_tool(search, monkeypatch, other_pi):
    root, bins = search
    pi = executable(root / 'installed-pi', '#!/bin/sh\nprintf active\n')
    shim(root, bins[0], str(pi), 0)
    if other_pi:
        executable(bins[1] / 'pi', '#!/bin/sh\nexit 99\n')
    else:
        monkeypatch.setenv('PATH', str(bins[0]))
    assert tool('pi') == str(pi)


def test_inactive_shim_uses_later_pi_and_wrapper_does_not_recurse(search):
    root, bins = search
    mise = shim(root, bins[0])
    alias = root / 'other-shims'
    alias.mkdir()
    (alias / 'pi').symlink_to(mise)
    os.environ['PATH'] = os.pathsep.join(map(str, [bins[0], alias, bins[1]]))
    pi = executable(bins[1] / 'pi', '#!/bin/sh\nprintf fallback\n')
    resolved = tool('pi')
    assert resolved == str(pi)
    launch = root / 'launch'
    launch.mkdir()
    (launch / 'notice.md').write_text('notice')
    wrapper.write(launch, wrapper.render(resolved, launch, [], [], op=False))
    result = subprocess.run(
        [str(launch / 'bin/pi')],
        env=dict(os.environ, PATH=str(launch / 'bin') + os.pathsep + '/usr/bin:/bin'),
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout == 'fallback'


def test_unresolvable_shim_refuses(search):
    root, bins = search
    shim(root, bins[0])
    with pytest.raises(Refusal, match=r'mise shim.*activate.*non-shim'):
        tool('pi')


@pytest.mark.parametrize('answer', ['relative/pi', '/absent/pi', 'self'])
def test_invalid_mise_answer_refuses(search, answer):
    root, bins = search
    shim(root, bins[0], str(bins[0] / 'pi') if answer == 'self' else answer, 0)
    with pytest.raises(Refusal, match='mise shim'):
        tool('pi')
