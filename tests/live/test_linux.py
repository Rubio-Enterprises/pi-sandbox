import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import REPO, executable, file

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(sys.platform != 'linux', reason='native Linux only'),
]


@pytest.mark.parametrize('org', [True, False])
def test_linux_git_workflow_subdirectory_and_print_streams(live, org):
    fixture = live
    paths = fixture.paths
    if not org:
        shutil.rmtree(paths.root / 'layers/10-org')
        host = paths.root / 'layers/90-host/layer.toml'
        content = host.read_text()
        host.write_text('schema = 1\n' + content[content.index('[credentials.env.inference]') :])
    fixture.git('branch', 'delete-packed')
    fixture.git('pack-refs', '--all')
    probe = paths.home / 'probe'
    probe.mkdir()
    shutil.copyfile(REPO / 'tests/live/probe.py', probe / 'probe.py')
    spec = probe / 'spec.json'
    executable(
        fixture.bins / 'pi',
        '#!/bin/sh\nexec '
        + shlex.join([str(Path(sys.executable).resolve()), str(probe / 'probe.py'), str(spec)])
        + '\n',
    )
    ops = [
        {'id': 'env', 'op': 'env'},
        {
            'id': 'workflow',
            'op': 'exec',
            'argv': [
                'sh',
                '-c',
                'set -e; printf content >> tracked; git add tracked; '
                'git -c core.hooksPath=/dev/null commit -qm tracked; '
                'git switch -c workflow; printf change >> tracked; git stash; git stash pop; '
                'git restore tracked; git rebase HEAD; git gc --auto',
            ],
        },
        {'id': 'pack-refs', 'op': 'exec', 'argv': ['git', 'pack-refs', '--all']},
        {'id': 'branch-delete', 'op': 'exec', 'argv': ['git', 'branch', '-D', 'delete-packed']},
        {
            'id': 'worktree-add',
            'op': 'exec',
            'argv': [
                'git',
                'worktree',
                'add',
                '-b',
                'new',
                str(paths.home / '.local/share/worktrees/repo/new'),
            ],
        },
    ]
    spec.write_text(json.dumps({'ops': ops}))
    grant = ':'.join([str(probe), str(fixture.bins), sys.prefix, sys.base_prefix])

    def launch(workdir):
        return subprocess.run(
            [
                sys.executable,
                '-c',
                'from pi_sandbox.cli import main; raise SystemExit(main())',
                '--workdir=' + str(workdir),
                '--add-dirs-ro=' + grant,
            ],
            env=fixture.env,
            capture_output=True,
            text=True,
            timeout=90,
        )

    result = launch(fixture.linked)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['workflow']['rc'] == 0, data['workflow']
    for key in ('pack-refs', 'branch-delete', 'worktree-add'):
        assert data[key]['rc'] != 0, data[key]
    assert 'packed-refs.lock' in data['pack-refs']['err']
    assert 'packed-refs.lock' in data['branch-delete']['err']
    env = data['env']
    assert ('OP_SERVICE_ACCOUNT_TOKEN' in env) == org
    assert 'PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN' not in env
    if org:
        assert env['OP_SERVICE_ACCOUNT_TOKEN'] == 'op-service-account_fixture'
        assert env['OP_CONFIG_DIR'] == str(paths.private_cache / 'op')
    subdir = fixture.linked / 'subdir'
    subdir.mkdir()
    spec.write_text(
        json.dumps(
            {
                'ops': [
                    {'id': 'status', 'op': 'exec', 'argv': ['git', 'status', '--short']},
                    {
                        'id': 'root-read',
                        'op': 'read',
                        'path': str(file(fixture.linked / 'context')),
                    },
                    {'id': 'root-write', 'op': 'append', 'path': str(fixture.linked / 'context')},
                    {'id': 'subdir-write', 'op': 'create', 'path': str(subdir / 'new')},
                ]
            }
        )
    )
    result = launch(subdir)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['status']['rc'] == 0
    assert data['root-read']['errno'] == data['subdir-write']['errno'] == 0
    assert data['root-write']['errno'] in (1, 13)
    executable(fixture.bins / 'pi', '#!/bin/sh\nprintf "pi-stdout\\n"\nprintf "pi-stderr\\n" >&2\n')
    result = launch(subdir)
    assert result.returncode == 0
    assert result.stdout == 'pi-stdout\n'
    assert result.stderr == (
        'pi-sandbox: GitHub read-only; publish branches from outside the sandbox\npi-stderr\n'
    )
