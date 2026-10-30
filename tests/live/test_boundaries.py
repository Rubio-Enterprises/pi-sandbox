import errno
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from pi_sandbox.platform import Platform
from pi_sandbox.seatbelt import PI_DIRS, PI_FILES
from tests.support import HOST_PATH, REPO, executable, file

pytestmark = pytest.mark.live


class Matrix:
    def __init__(self):
        self.ops = []
        self.expected = {}

    def add(self, name, op, path=None, allowed=None, **fields):
        self.ops.append(dict(id=name, op=op, **({'path': str(path)} if path else {}), **fields))
        if allowed is not None:
            self.expected[name] = allowed

    def check(self, results):
        for name, allowed in self.expected.items():
            code = results[name].get('errno')
            assert code == 0 if allowed else code in (errno.EPERM, errno.EACCES), (
                name,
                results[name],
            )


@pytest.mark.parametrize('org,token', [(True, True), (True, False), (False, True), (False, False)])
def test_real_boundaries(live, org, token):
    fixture = live
    paths = fixture.paths
    platform = Platform.detect()
    if not org:
        shutil.rmtree(paths.root / 'layers/10-org')
        host = paths.root / 'layers/90-host/layer.toml'
        content = host.read_text()
        host.write_text('schema = 1\n' + content[content.index('[credentials.env.inference]') :])
    if not token:
        (paths.root / 'credentials/pi-sandbox-github.token').unlink()
    # The protected checkout is also the worktree's actual main checkout
    user = paths.root / 'layers/50-user/layer.toml'
    user.write_text(user.read_text().replace('"~/protected"', json.dumps(str(fixture.repo))))
    workdir = fixture.linked
    if platform.os == 'macos' and not token:
        file(fixture.repo / 'README.md')
    probe_dir = paths.home / 'probe'
    probe_dir.mkdir()
    shutil.copyfile(REPO / 'tests/live/probe.py', probe_dir / 'probe.py')
    spec = probe_dir / 'spec.json'
    executable(
        fixture.bins / 'pi',
        '#!/bin/sh\nexec '
        + shlex.join([str(Path(sys.executable).resolve()), str(probe_dir / 'probe.py'), str(spec)])
        + '\n',
    )
    matrix = Matrix()
    for name, directory in [
        ('workdir', workdir),
        ('rw-extra', fixture.root / 'extra'),
        ('runtime', paths.home / ('runtime/mac' if platform.os == 'macos' else 'runtime/linux')),
    ]:
        directory.mkdir(parents=True, exist_ok=True)
        matrix.add('rw:' + name, 'create', directory / 'probe', True)
    ro = fixture.root / 'ro'
    file(ro / 'fixture')
    for directory in (ro, paths.home / 'context', paths.home / '.config/worktrunk', fixture.repo):
        matrix.add('ro-read:' + str(directory), 'read', file(directory / 'fixture'), True)
        matrix.add('ro-write:' + str(directory), 'append', directory / 'fixture', False)
        matrix.add('ro-create:' + str(directory), 'create', directory / 'new-file', False)
    for relative in ('instructions.md', '.config/herdr/config.toml'):
        matrix.add('exact-read:' + relative, 'read', file(paths.home / relative), True)
        matrix.add('exact-write:' + relative, 'append', paths.home / relative, False)
    matrix.add('herdr-sibling', 'read', file(paths.home / '.config/herdr/session.json'), False)
    credential_canaries = (
        '.claude/.credentials.json',
        '.codex/auth.json',
        '.ssh/id_ed25519',
        '.gnupg/private-keys-v1.d/key',
        '.kube/config',
        '.netrc',
        '.pgpass',
        '.pg_service.conf',
        '.git-credentials',
        '.npmrc',
        '.pypirc',
        '.config/gh/hosts.yml',
        '.config/pi-sandbox/credentials/canary.token',
        '.config/pi-sandbox/host/token',
        '.docker/config.json',
        '.config/incus/client.key',
        'Documents/outside',
        '.zsh_history',
        'Library/Application Support/Code/User/settings.json',
        'Library/Application Support/Code/User/globalStorage/state.vscdb',
        'Library/Application Support/Google/Chrome/Default/Cookies',
    )
    for relative in credential_canaries:
        matrix.add('credential:' + relative, 'read', file(paths.home / relative), False)
    for relative in ('.aws/fixture', '.config/gcloud/fixture', '.config/op/fixture'):
        matrix.add('integration:' + relative, 'read', paths.home / relative, org)
    for relative in (
        '.zshrc',
        '.zshenv',
        '.zprofile',
        '.gitconfig',
        '.config/zsh/fixture',
        '.config/mise/config.toml',
        '.local/share/mise/fixture',
        '.local/share/uv/fixture',
        '.local/share/agent-extensions/fixture',
        '.local/state/mise/trusted-configs/fixture',
        '.local/state/mise/ignored-configs/fixture',
        '.cache/other/fixture',
        '.config/nono/packages/lockfile.json',
        '.config/nono/profiles/fixture',
    ):
        path = paths.home / relative
        if not path.exists():
            file(path)
        matrix.add('host-write:' + relative, 'append', path, False)
    if platform.os == 'macos':
        for name in PI_FILES:
            path = paths.home / '.pi/agent' / name
            matrix.add('pi-append:' + name, 'append', file(path, '{}'), False)
            matrix.add('pi-replace:' + name, 'replace', path, False)
        for relative in PI_DIRS:
            path = paths.home / relative
            matrix.add('pi-dir-write:' + relative, 'append', file(path / 'fixture'), False)
            matrix.add('pi-dir-create:' + relative, 'create', path / 'new', False)
        for relative in ('.pi/agent/auth.json', '.pi/agent/mcp-auth.json'):
            matrix.add('pi-runtime:' + relative, 'append', file(paths.home / relative, '{}'), True)
        matrix.add('pi-lock', 'mkdir', paths.home / '.pi/agent/settings.json.lock', True)
        matrix.add('pi-models-store', 'create', paths.home / '.pi/agent/models-store.json', True)
    common = fixture.repo / '.git'
    directory = Path(fixture.git('rev-parse', '--path-format=absolute', '--git-dir', cwd=workdir))
    for path in (
        common / 'config',
        common / 'config.lock',
        common / 'hooks/new',
        directory / 'config.worktree',
    ):
        matrix.add('git-deny:' + str(path), 'append' if path.exists() else 'create', path, False)
    matrix.add('git-hook-dir', 'mkdir', common / 'hooks/new-dir', False)
    matrix.add(
        'git-commit',
        'exec',
        argv=[
            'git',
            '-c',
            'core.hooksPath=/dev/null',
            'commit',
            '-q',
            '--allow-empty',
            '-m',
            'probe',
        ],
    )
    for var in (
        'UV_CACHE_DIR',
        'UV_TOOL_DIR',
        'MISE_CACHE_DIR',
        'BUN_RUNTIME_TRANSPILER_CACHE_PATH',
        'TMUX_TMPDIR',
        'AGENT_BROWSER_SOCKET_DIR',
    ):
        matrix.add('cache:' + var, 'env-dir', allowed=True, var=var)
    matrix.add('env', 'env')
    matrix.add('pi-path', 'exec', argv=['sh', '-c', 'command -v pi'])
    matrix.add(
        'semaphore',
        'exec',
        argv=[
            str(Path(sys.executable).resolve()),
            '-c',
            'import multiprocessing; multiprocessing.Lock()',
        ],
    )
    if platform.os == 'macos':
        matrix.add('ps', 'exec', argv=['/bin/ps', '-o', 'pid=', '-p', '1'])
        matrix.add('open', 'exec', argv=['/usr/bin/open', '-h'])
        matrix.add('osascript', 'exec', argv=['/usr/bin/osascript', '-e', 'return 1'])
    tmux = shutil.which('tmux', path=HOST_PATH)
    if tmux:
        child = probe_dir / 'child.json'
        child.write_text(
            json.dumps(
                {
                    'ops': [
                        {'id': 'env', 'op': 'env'},
                        {'id': 'pid', 'op': 'pid'},
                        {'id': 'home', 'op': 'create', 'path': str(paths.home / 'tmux-denied')},
                        {'id': 'project', 'op': 'create', 'path': str(workdir / 'tmux-allowed')},
                    ]
                }
            )
        )
        matrix.add(
            'tmux', 'tmux-child', tmux=tmux, spec=str(child), out=str(workdir / 'child.json')
        )
    listeners, socket_paths, socket_dirs = [], [], []

    def listen_unix(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(path))
        server.listen(16)
        listeners.append(server)
        socket_paths.append(path)
        return path

    try:
        docker = listen_unix(paths.home / '.d/s')
        matrix.add('docker', 'connect', allowed=True, unix=str(docker))
        if platform.os == 'macos':
            # Only synthetic sockets are used, never a host service or an ssh-agent
            for prefix in ('tmux-', 'piab-'):
                root = Path(tempfile.mkdtemp(prefix=prefix, dir='/tmp')).resolve()
                socket_dirs.append(root)
                path = listen_unix(root / 's')
                matrix.add('host-socket:' + prefix, 'connect', allowed=False, unix=str(path))
            for prefix in ('herdr-', 'vscode-git-', 'ssh-askpass-'):
                root = Path(tempfile.mkdtemp(prefix=prefix, dir=tempfile.gettempdir())).resolve()
                socket_dirs.append(root)
                path = listen_unix(root / 's')
                matrix.add('host-socket:' + prefix, 'connect', allowed=False, unix=str(path))
            path = listen_unix(paths.home / '.config/herdr/host.sock')
            matrix.add('native-host-socket', 'connect', allowed=False, unix=str(path))
            server = socket.socket()
            server.bind(('127.0.0.1', 0))
            server.listen(16)
            listeners.append(server)
            port = server.getsockname()[1]
            output = json.dumps(
                {'seatbelt_rules': [f'(deny network-outbound (remote ip "localhost:{port}"))']}
            )
            executable(fixture.bins / 'inventory', f'#!{sys.executable}\nprint({output!r})\n')
            matrix.add('tcp-hook-deny', 'connect', allowed=False, port=port)
        spec.write_text(json.dumps({'ops': matrix.ops}))
        grant = ':'.join(
            dict.fromkeys([str(probe_dir), str(fixture.bins), sys.prefix, sys.base_prefix])
        )
        args = [
            '--workdir=' + str(workdir),
            '--scratch=fixture',
            '--add-dirs=' + str(fixture.root / 'extra'),
            '--add-dirs-ro=' + str(ro),
            '--add-dirs-ro=' + grant,
        ]
        env = fixture.env | {
            'CANARY_SECRET': 'leak',
            'GH_TOKEN': 'human',
            'GITHUB_TOKEN': 'human',
            'SSH_AUTH_SOCK': '/human',
            'HERDR_ENV': '1',
            'TMUX': 'host',
            'TMUX_PANE': '%1',
            'PI_ASK_USER_ALLOW_COMMENT': 'true',
            'SANDBOX_ENV_PASS': 'SAFE',
            'SAFE': 'safe',
        }
        run = subprocess.run(
            [
                sys.executable,
                '-c',
                'from pi_sandbox.cli import main; raise SystemExit(main())',
                *args,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert run.returncode == 0, run.stderr
        results = json.loads(run.stdout)
        matrix.check(results)
        for name in (
            'git-commit',
            'semaphore',
            'pi-path',
            *(['ps'] if platform.os == 'macos' else []),
        ):
            assert results[name]['rc'] == 0, (name, results[name])
        if platform.os == 'macos':
            for name in ('open', 'osascript'):
                assert (
                    results[name].get('errno') in (errno.EPERM, errno.EACCES)
                    or results[name].get('rc') != 0
                )
                assert not results[name].get('out')
        env = results['env']
        assert env['NONO_CAP_FILE']
        assert ('GH_TOKEN' in env) == token
        assert env.get('SAFE') == 'safe' and env.get('PI_ASK_USER_ALLOW_COMMENT') == 'true'
        for name in (
            'CANARY_SECRET',
            'GITHUB_TOKEN',
            'SSH_AUTH_SOCK',
            'HERDR_ENV',
            'TMUX',
            'TMUX_PANE',
        ):
            assert name not in env, name
        assert results['pi-path']['out'].strip().endswith('/bin/pi')
        for name in ('AGENT_BROWSER_SOCKET_DIR', 'TMUX_TMPDIR', 'GIT_CONFIG_GLOBAL'):
            assert not Path(env[name]).exists()
        assert Path(env['AGENT_BROWSER_SCREENSHOT_DIR']).is_dir()
        if tmux:
            child = results['tmux']['child']
            assert child is not None, results['tmux']
            assert child['env']['NONO_CAP_FILE']
            assert child['home']['errno'] in (errno.EPERM, errno.EACCES)
            assert child['project']['errno'] == 0
            for _ in range(100):
                try:
                    os.kill(child['pid'], 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                pytest.fail('private tmux pane outlived the launch')
    finally:
        for server in listeners:
            server.close()
        for path in socket_paths:
            path.unlink(missing_ok=True)
        for path in socket_dirs:
            shutil.rmtree(path)
