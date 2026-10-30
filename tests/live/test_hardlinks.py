import json
import subprocess

import pytest

from pi_sandbox.paths import atomic_json
from pi_sandbox.platform import Platform
from tests.support import executable

pytestmark = pytest.mark.live


@pytest.mark.parametrize('kind', ['layer.toml', 'profile.json', 'hook'])
def test_rw_hardlink_write_probe_and_engine_refusal(live, kind):
    fixture = live
    platform = Platform.detect()
    workdir = fixture.linked if platform.os == 'linux' else fixture.repo
    if kind == 'hook':
        source = executable(fixture.paths.home / '.local/libexec/hook', '#!/bin/sh\nprintf "{}"\n')
        fixture.layer(
            '95-hardlink',
            'schema = 1\n[[hooks]]\nname = "inventory"\ncommand = '
            + json.dumps([str(source)])
            + '\n',
        )
    else:
        directory = fixture.layer('95-hardlink', 'schema = 1\n', {})
        source = directory / kind
    before = source.read_bytes()
    alias = workdir / 'policy-alias'
    alias.hardlink_to(source)
    assert source.stat().st_nlink == alias.stat().st_nlink == 2

    probe = fixture.out / 'hardlink-probe.json'
    atomic_json(
        probe,
        {
            'extends': 'nolabs-ai/pi',
            'filesystem': {'allow': [str(workdir)], 'deny': [str(fixture.paths.root)]},
            'workdir': {'access': 'readwrite'},
        },
    )
    result = subprocess.run(
        [
            'nono',
            'wrap',
            '-s',
            '--profile',
            str(probe),
            '--allow-cwd',
            '--',
            '/bin/sh',
            '-c',
            'printf "\\n" >> "$1"; printf "write_status=%s\\n" "$?"',
            'probe',
            str(alias),
        ],
        env=fixture.env,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and result.stdout == 'write_status=0\n', result.stderr
    assert source.read_bytes() == before + b'\n'

    marker = workdir / 'pi-ran'
    executable(fixture.bins / 'pi', '#!/bin/sh\ntouch ' + str(marker) + '\n')
    result = fixture.run(platform.os, '--add-dirs-ro=' + str(fixture.bins))
    assert result.returncode == 2 and 'exactly one hard link' in result.stderr
    assert not marker.exists()
    assert source.read_bytes() == before + b'\n'
