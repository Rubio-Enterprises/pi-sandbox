import json
import subprocess
from dataclasses import replace

import pytest

from pi_sandbox.cli import split
from pi_sandbox.config import Config, SandboxProfile
from pi_sandbox.launch import plan, prefix, preview
from pi_sandbox.paths import atomic_json
from pi_sandbox.platform import Platform
from pi_sandbox.profile import sync
from tests.support import executable, file

pytestmark = pytest.mark.live


def test_two_config_native_resolution_keeps_original_read_only_snapshot(live):
    fixture = live
    sensitive = fixture.paths.home / 'other-project'
    sensitive.mkdir()
    strict = Config(
        fixture.paths,
        [
            SandboxProfile(
                'user',
                fixture.paths.root,
                {},
                {
                    'filesystem': {'read': [str(sensitive)]},
                    'meta': {'name': 'pi-sandbox-user-50-user'},
                },
            )
        ],
    )
    permissive = Config(
        replace(fixture.paths, root=fixture.paths.home / 'other-config'),
        [
            SandboxProfile(
                'user',
                fixture.paths.root,
                {},
                {
                    'filesystem': {'allow': [str(sensitive)]},
                    'meta': {'name': 'pi-sandbox-user-50-user'},
                },
            )
        ],
    )
    platform = Platform.detect()
    workdir = fixture.linked if platform.os == 'linux' else fixture.repo
    sync(strict)
    path = fixture.out / 'strict.json'
    options = split(['--workdir=' + str(workdir)])
    atomic_json(path.parent / 'engine.json', prefix(strict, plan(strict, platform, options)))
    atomic_json(path, preview(strict, platform, options))
    sync(permissive)
    result = subprocess.run(
        ['nono', 'profile', 'show', str(path), '--json'],
        env=fixture.env,
        capture_output=True,
        text=True,
        check=True,
    )
    fs = json.loads(result.stdout)['filesystem']
    assert str(sensitive) in fs['read'] and str(sensitive) not in fs['allow']


def test_real_nono_refuses_sidecar_link_into_rw_project_before_child(live):
    fixture = live
    target = file(fixture.repo / 'policy.toml', '')
    fixture.sandbox_profile('linked')
    sidecar = fixture.paths.root / 'profiles/linked.toml'
    sidecar.unlink()
    sidecar.symlink_to(target)
    executable(fixture.bins / 'pi', '#!/bin/sh\nprintf changed >> ' + str(target) + '\n')
    result = fixture.run(Platform.detect().os, '--add-dirs-ro=' + str(fixture.bins))
    assert result.returncode == 2
    assert target.read_text() == ''


def test_live_pack_tampering_is_refused(live):
    from pi_sandbox import Refusal, pack

    path = pack.directory(live.paths) / 'profiles/pi.json'
    data = json.loads(path.read_text())
    data['filesystem']['allow'].append('/')
    path.write_text(json.dumps(data))
    with pytest.raises(Refusal, match='integrity'):
        pack.require(live.paths)


def test_real_nono_sigint_disposition_is_not_inherited_ignored(live):
    import shlex
    import sys
    from pathlib import Path

    fixture = live
    code = 'import signal; print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN)'
    executable(
        fixture.bins / 'pi',
        '#!/bin/sh\nexec ' + shlex.join([str(Path(sys.executable).resolve()), '-c', code]) + '\n',
    )
    result = fixture.run(
        Platform.detect().os,
        '--add-dirs-ro=' + ':'.join([str(fixture.bins), sys.prefix, sys.base_prefix]),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'False\n'


@pytest.mark.parametrize('source', ['native-profile', 'pi-pack'])
def test_native_manifest_blocks_writable_hook_sources(live, source):
    fixture = live
    directory = fixture.paths.home / ('.pi/runtime' if source == 'pi-pack' else 'profile-rw')
    hook = executable(directory / 'hook', '#!/bin/sh\nexit 0\n')
    fragment = {'filesystem': {'allow': [str(directory)]}} if source == 'native-profile' else None
    fixture.sandbox_profile(
        '95-hook',
        '[[hooks]]\nname = "inventory"\ncommand = ' + json.dumps([str(hook)]) + '\n',
        fragment,
    )
    result = fixture.run(Platform.detect().os)
    assert result.returncode == 2 and 'read-write grant' in result.stderr
