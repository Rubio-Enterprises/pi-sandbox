import base64
import json
import os
import pty
import shutil
import signal
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

import pytest


def assert_clean(capture):
    env = capture['env']
    for key in ('TMUX_TMPDIR', 'AGENT_BROWSER_SOCKET_DIR', 'GIT_CONFIG_GLOBAL'):
        assert not Path(env[key]).exists(), (key, env[key])
    assert Path(env['AGENT_BROWSER_SCREENSHOT_DIR'], 'fixture.png').exists()


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
@pytest.mark.parametrize('org,token', [(True, True), (True, False), (False, True), (False, False)])
def test_full_launch_contract(fixture, os_name, org, token):
    if not org:
        shutil.rmtree(fixture.paths.root / 'layers/10-org')
        host = fixture.paths.root / 'layers/90-host/layer.toml'
        content = host.read_text()
        host.write_text('schema = 1\n' + content[content.index('[credentials.env.inference]') :])
    if not token:
        (fixture.paths.root / 'credentials/pi-sandbox-github.token').unlink()
    extra_rw, extra_ro = fixture.root / 'rw with space', fixture.root / 'ro with space'
    extra_rw.mkdir()
    extra_ro.mkdir()
    result = fixture.run(
        os_name,
        '--scratch=effort',
        '--add-dirs',
        str(extra_rw),
        '--add-dirs-ro=' + str(extra_ro),
        '-xt',
        'one,two',
        '--exclude-tools=two,three',
        '-p',
        'hello',
        '--',
        '-xt',
        'keep',
        extra={
            'SANDBOX_ENV_PASS': 'SAFE EXAMPLE_ADMIN_TOKEN EXAMPLE_FIRST_TOKEN CHAT_TOKEN *',
            'SAFE': 'safe',
            'EXAMPLE_ADMIN_TOKEN': 'human',
            'CHAT_TOKEN': 'human',
            'GH_TOKEN': 'human',
            'GITHUB_TOKEN': 'human',
            'GH_DEBUG': 'api',
            'HERDR_ENV': '1',
            'SSH_AUTH_SOCK': '/human',
            'TMUX': '/host',
            'TMUX_PANE': '%1',
            'DOCKER_CONTEXT': 'production',
            'PI_ASK_USER_ALLOW_COMMENT': 'true',
            'CANARY_SECRET': 'leak',
            'TEST_SOCKETS': '1',
            'TEST_BROWSER': 'namespace',
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'pi-stdout\n' and result.stderr.endswith('pi-stderr\n')
    cap = fixture.capture()
    assert_clean(cap)
    env, fs = cap['env'], cap['profile']['filesystem']
    pi = json.loads((fixture.out / 'pi.json').read_text())
    argv = pi['argv']
    assert argv.count('--exclude-tools') == 1
    patterns = argv[argv.index('--exclude-tools') + 1]
    assert patterns == 'mcp__example__delete_*,unsafe_*,one,two,three'
    assert argv[-5:] == ['-p', 'hello', '--', '-xt', 'keep']
    assert argv[argv.index('--append-system-prompt') + 1] == cap['notice'].rstrip('\n')
    assert 'mcp__example__delete_' not in cap['notice']
    assert 'Subagents are allowed' in cap['notice'] and 'wider launch' in cap['notice']
    assert cap['mode'] == cap['browser_mode'] == cap['tmux_mode'] == 0o700
    assert str(extra_rw.resolve()) in fs['allow'] and str(extra_ro.resolve()) in fs['read']
    assert str(fixture.paths.home / 'scratch/effort') in fs['allow']
    assert str(fixture.paths.home / '.local/share/worktrees/repo') in fs['allow']
    assert str(fixture.paths.home / 'protected') in fs['read']
    assert str(fixture.paths.home / 'instructions.md') in fs['read_file']
    assert str(fixture.paths.home / '.config/herdr/config.toml') in fs['bypass_protection']
    assert str(fixture.paths.root) in fs['deny']
    assert env['GIT_AUTHOR_NAME'] == env['GIT_COMMITTER_NAME'] == 'Fixture User'
    assert env['GIT_AUTHOR_EMAIL'] == env['GIT_COMMITTER_EMAIL'] == 'user@example.invalid'
    assert env['GIT_CONFIG_NOSYSTEM'] == '1' and env['GIT_CONFIG_COUNT'] == '0'
    assert env['GIT_TERMINAL_PROMPT'] == '0'
    for key in (
        'GITHUB_TOKEN',
        'GH_DEBUG',
        'HERDR_ENV',
        'TMUX',
        'TMUX_PANE',
        'SSH_AUTH_SOCK',
        'DOCKER_CONTEXT',
        'CANARY_SECRET',
    ):
        assert key not in env
    assert env['SAFE'] == 'safe' and env['PI_ASK_USER_ALLOW_COMMENT'] == 'true'
    assert ('GH_TOKEN' in env) == token
    assert ('gh auth git-credential' in cap['gitconfig']) == token
    assert ('echo quit=1' in cap['gitconfig']) != token
    assert cap['gh_files'] == []
    assert cap['docker']['auths'] == {
        'https://index.docker.io/v1/': {},
        'ghcr.io': {'auth': base64.b64encode(b'x-access-token:pi-sandbox-ghcr_fixture').decode()},
    }
    assert argv[argv.index('--skill') + 1].endswith('/nolabs-ai/pi/guides')
    assert argv.count('--skill') == 1
    assert env['AGENT_BROWSER_ARGS'] == '--no-sandbox,--no-startup-window'
    assert env['AGENT_BROWSER_SOCKET_DIR'] == env['PI_AGENT_BROWSER_SOCKET_DIR']
    assert env['PI_SANDBOX_INFERENCE_API_KEY'] == 'inference_fixture'
    assert env['UV_TOOL_DIR'].endswith('/pi-sandbox/uv-tools')
    for key in ('JAVA_TOOL_OPTIONS', 'GEM_HOME', 'GEM_PATH'):
        assert env[key] == cap['profile']['environment']['set_vars'][key]
    if org:
        assert env['EXAMPLE_FIRST_TOKEN'] == 'first_fixture'
        assert env['CHAT_TOKEN'] == 'chat_fixture'
        assert env['EXAMPLE_EMAIL'] == 'fixture@example.invalid'
        assert 'EXAMPLE_ADMIN_TOKEN' not in env
        assert str(fixture.paths.state / 'app-harness') in fs['allow']
    else:
        assert 'EXAMPLE_FIRST_TOKEN' not in env
        assert 'First is reachable' not in cap['notice']
        assert 'first.token' not in result.stderr
    keychains = any('Keychains' in path for path in fs['bypass_protection'])
    assert keychains == (org and token and os_name == 'macos')
    cleanup = (fixture.out / 'cleanup').read_text().splitlines()
    assert cleanup[:2] == [
        f'tmux|-S|{env["TMUX_TMPDIR"]}/tmux-{os.getuid()}/default|kill-server',
        f'tmux|-S|{env["TMUX_TMPDIR"]}/tmux-{os.getuid()}/pi-subagent|kill-server',
    ]
    assert (
        len(cleanup) == 4
        and cleanup[2].endswith('|<unset>|True')
        and cleanup[3].endswith('|ns1|True')
    )
    assert not [line for line in cleanup if 'hardlink' in line or '/link|' in line]
    raw = '\n'.join(cap['profile']['unsafe_macos_seatbelt_rules'])
    if os_name == 'macos':
        assert cap['argv'][0] == 'wrap' and '--silent' not in cap['argv']
        assert (
            raw.index('*:8080')
            < raw.index('localhost:8080')
            < raw.index('localhost:8081')
            < raw.index('file-read* file-write*')
        )
        from pi_sandbox.seatbelt import static_rules

        floors = static_rules(fixture.paths, [str(fixture.paths.home / 'protected')])
        rules = cap['profile']['unsafe_macos_seatbelt_rules']
        assert rules[-len(floors) :] == floors
        assert rules[0].startswith('(deny file-write* (literal ')
        assert '@git:common-dir' in fs['allow'] and '@git:worktree' in fs['read']
        assert fs['unix_socket'] == [str(fixture.paths.home / '.d/s')]
        assert not [p for p in fs['bypass_protection'] if p.endswith('/.d/s')]
        assert cap['cwd'] == str(fixture.repo.resolve())
        if not token:
            calls = (fixture.out / 'calls').read_text()
            assert '--format' in calls and '--json' in calls
    else:
        assert cap['argv'][:2] == ['run', '--silent']
        assert cap['argv'].count('--suppress-save-prompt') == 2
        assert '@git:' not in json.dumps(fs) and not raw and 'unix_socket' not in fs
        common = fixture.repo / '.git'
        assert str(common) in fs['read'] and str(common) not in fs['allow']
        for suffix in ('objects', 'refs', 'logs', 'worktrees/linked'):
            assert str(common / suffix) in fs['allow']
        assert str(fixture.linked) in fs['read']
        assert env['DOCKER_HOST'] == 'unix:///var/run/docker.sock'
        assert ('--env-credential-map' in cap['argv']) == org
        assert ('OP_SERVICE_ACCOUNT_TOKEN' in pi['env']) == org
        assert 'PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN' not in pi['env']
        assert 'Unix sockets are NOT isolated' in cap['notice']
    public = (
        json.dumps({k: v for k, v in cap.items() if k not in ('env', 'docker')}) + result.stderr
    )
    for value in (
        'first_fixture',
        'second_fixture',
        'chat_fixture',
        'pi-sandbox-github_fixture',
        'op-service-account_fixture',
    ):
        assert value not in public


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
@pytest.mark.parametrize(
    'states', [('present', 'empty', 'empty'), ('empty', 'present', 'absent'), ('absent',) * 3]
)
def test_combined_slot_states(fixture, os_name, states):
    for name, state in zip(('first', 'second', 'chat'), states, strict=True):
        path = fixture.paths.root / 'credentials' / (name + '.token')
        if state == 'absent':
            path.unlink()
        elif state == 'empty':
            path.write_text('\n')
    result = fixture.run(
        os_name,
        extra={
            'SANDBOX_ENV_PASS': (
                'EXAMPLE_FIRST_TOKEN EXAMPLE_SECOND_TOKEN EXAMPLE_URL EXAMPLE_EMAIL CHAT_TOKEN SAFE'
            ),
            'EXAMPLE_FIRST_TOKEN': 'human',
            'EXAMPLE_SECOND_TOKEN': 'human',
            'EXAMPLE_URL': 'caller',
            'EXAMPLE_EMAIL': 'caller',
            'CHAT_TOKEN': 'caller',
            'SAFE': 'safe',
        },
    )
    assert result.returncode == 0, result.stderr
    cap = fixture.capture()
    for name, state, variable in zip(
        ('first', 'second', 'chat'),
        states,
        ('EXAMPLE_FIRST_TOKEN', 'EXAMPLE_SECOND_TOKEN', 'CHAT_TOKEN'),
        strict=True,
    ):
        assert (variable in cap['env']) == (state == 'present')
        assert (name.title() + ' is reachable' in cap['notice']) == (state == 'present')
        assert result.stderr.count(name.title() + ' off: no token at ') == (state != 'present')
    assert ('EXAMPLE_URL' in cap['env']) == ('present' in states[:2])
    assert cap['env']['SAFE'] == 'safe'


@pytest.mark.parametrize('state', ['empty', 'absent'])
def test_linux_op_token_state(fixture, state):
    token = fixture.paths.root / 'credentials/op-service-account.token'
    token.write_text('\n') if state == 'empty' else token.unlink()
    result = fixture.run('linux', extra={'OP_SERVICE_ACCOUNT_TOKEN': 'human'})
    assert result.returncode == 0, result.stderr
    assert '--env-credential-map' not in fixture.capture()['argv']
    pi = json.loads((fixture.out / 'pi.json').read_text())
    assert 'OP_SERVICE_ACCOUNT_TOKEN' not in pi['env']


@pytest.mark.parametrize(
    'mutation', ['TEST_DROP_DENY', 'TEST_BYPASS', 'TEST_RAW_ALLOW', 'TEST_RESOLVER_FAIL']
)
def test_keychain_preflight_refusals(fixture, mutation):
    (fixture.paths.root / 'credentials/pi-sandbox-github.token').unlink()
    result = fixture.run(extra={mutation: '1'})
    assert result.returncode == 2
    assert 'refusing launch' in result.stderr
    assert not (fixture.out / 'capture.json').exists()
    assert not list(fixture.paths.launch_state.glob('launch.*'))


@pytest.mark.parametrize(
    'kind',
    [
        'nested',
        'root',
        'home',
        'ancestor',
        'missing',
        'scratch',
        'no-scratch-root',
        'pack',
        'old-version',
        'git-hooks',
        'hook',
    ],
)
def test_early_refusals(fixture, kind):
    args, extra = [], {}
    if kind == 'nested':
        extra['NONO_CAP_FILE'] = '/nonexistent'
    elif kind in ('root', 'home', 'ancestor', 'missing'):
        value = {
            'root': '/',
            'home': str(fixture.paths.home),
            'ancestor': str(fixture.root),
            'missing': str(fixture.root / 'absent'),
        }[kind]
        args = ['--workdir=' + value]
    elif kind == 'scratch':
        args = ['--scratch=../bad']
    elif kind == 'no-scratch-root':
        shutil.rmtree(fixture.paths.root / 'layers/50-user')
        args = ['--scratch=effort']
    elif kind == 'pack':
        (fixture.paths.nono / 'packages/lockfile.json').unlink()
    elif kind == 'old-version':
        extra['TEST_NONO_VERSION'] = 'nono 0.79.9'
    elif kind == 'git-hooks':
        fixture.git('config', 'core.hooksPath', str(fixture.repo))
    elif kind == 'hook':
        (fixture.bins / 'inventory').write_text('#!/bin/sh\necho fixture-refusal >&2\nexit 1\n')
    result = fixture.run('macos', *args, extra=extra)
    assert result.returncode == 2 and 'pi-sandbox: ' in result.stderr
    assert not (fixture.out / 'capture.json').exists()
    assert not list(fixture.paths.launch_state.glob('launch.*'))
    if kind == 'hook':
        assert 'hook inventory refused the launch\nfixture-refusal' in result.stderr


@pytest.mark.parametrize(
    'kind',
    [
        'main',
        'config',
        'nono',
        'zsh',
        'protected',
        'common',
        'credentials',
        'worktree-config',
        'hooks',
        'kernel',
        'wsl-rw',
        'wsl-ro',
    ],
)
def test_linux_refusals(fixture, kind):
    args, options = [], {}
    paths = {
        'main': fixture.repo,
        'config': fixture.paths.home / '.config',
        'nono': fixture.paths.nono / 'profiles',
        'zsh': fixture.paths.home / '.config/zsh',
        'protected': fixture.paths.home / 'protected',
        'common': fixture.repo / '.git',
        'credentials': fixture.paths.root / 'credentials',
    }
    if kind == 'main':
        args = ['--workdir=' + str(paths[kind])]
    elif kind in paths:
        args = ['--add-dirs=' + str(paths[kind])]
    elif kind == 'worktree-config':
        fixture.git('config', 'extensions.worktreeConfig', 'true')
    elif kind == 'hooks':
        fixture.git('config', 'core.hooksPath', str(fixture.linked / 'hooks'))
    elif kind == 'kernel':
        options['release'] = '6.11.99'
    elif kind.startswith('wsl-'):
        options['wsl'] = True
        flag = '--add-dirs' if kind.endswith('rw') else '--add-dirs-ro'
        args = [flag + '=/mnt/D/project']
    result = fixture.run('linux', *args, **options)
    assert result.returncode == 2, result.stderr
    assert not (fixture.out / 'capture.json').exists()
    assert not list(fixture.paths.launch_state.glob('launch.*'))


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_non_git_subdir_identity_and_fail_status(fixture, os_name):
    plain = fixture.root / 'plain'
    plain.mkdir()
    (fixture.paths.home / '.gitconfig').unlink()
    result = fixture.run(
        os_name, '--workdir=' + str(plain), extra={'TEST_STATUS': '3', 'GIT_AUTHOR_NAME': 'stale'}
    )
    assert result.returncode == 3 and 'commits will fail until set' in result.stderr
    assert 'GIT_AUTHOR_NAME' not in fixture.capture()['env']
    assert_clean(fixture.capture())
    subdir = fixture.linked / 'subdir'
    subdir.mkdir()
    result = fixture.run(os_name, '--workdir=' + str(subdir))
    assert result.returncode == 0, result.stderr
    if os_name == 'linux':
        fs = fixture.capture()['profile']['filesystem']
        assert str(fixture.linked) in fs['read'] and str(fixture.linked) not in fs['allow']
        assert str(subdir) in fs['allow']


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
@pytest.mark.parametrize('signum', [signal.SIGHUP, signal.SIGINT, signal.SIGTERM, None])
def test_signal_and_pty_cleanup(fixture, os_name, signum):
    env = fixture.env | {'TEST_HOLD': '1', 'TEST_SOCKETS': '1', 'TEST_BROWSER': 'namespace'}
    master = None
    if signum is None:
        pid, master = pty.fork()
        if pid == 0:
            os.execve(sys.executable, fixture.command(os_name), env)
    else:
        process = subprocess.Popen(
            fixture.command(os_name),
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid = process.pid
    try:
        deadline = time.monotonic() + 15
        while not (fixture.out / 'ready').exists():
            assert time.monotonic() < deadline, 'child never became ready'
            time.sleep(0.02)
        cap = fixture.capture()
        if master is not None:
            os.close(master)
            master = None
        else:
            os.killpg(pid, signum)
        deadline = time.monotonic() + 15
        while Path(cap['env']['TMUX_TMPDIR']).exists():
            assert time.monotonic() < deadline, 'cleanup did not finish'
            time.sleep(0.02)
        if signum is not None:
            assert process.wait(timeout=5) == 128 + signum
        else:
            os.waitpid(pid, 0)
        assert_clean(cap)
        assert len((fixture.out / 'cleanup').read_text().splitlines()) == 4
    finally:
        if master is not None:
            os.close(master)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize('browser,expected', [('plain', 0), ('root', 1), ('namespace', 2)])
def test_browser_cleanup_only_private_pool(fixture, browser, expected):
    result = fixture.run(extra={'TEST_BROWSER': browser, 'TEST_STATUS': '3'})
    assert result.returncode == 3
    log = fixture.out / 'cleanup'
    assert (len(log.read_text().splitlines()) if log.exists() else 0) == expected
    assert_clean(fixture.capture())


def test_slot_command_shell_stderr_and_missing(fixture):
    fixture.layer(
        '95-command',
        """schema = 1
[credentials.env.inference]
command = "echo should-not-leak >&2; exit 1"
""",
    )
    result = fixture.run(
        extra={
            'PI_SANDBOX_INFERENCE_API_KEY': 'human',
            'SANDBOX_ENV_PASS': 'PI_SANDBOX_INFERENCE_API_KEY',
        }
    )
    assert result.returncode == 0, result.stderr
    assert 'should-not-leak' not in result.stderr
    assert result.stderr.count('local inference key unavailable') == 1
    assert 'PI_SANDBOX_INFERENCE_API_KEY' not in fixture.capture()['env']


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_ctl_commands_and_dry_run(fixture, os_name):
    ctl = partial(fixture.ctl, os_name=os_name)
    assert ctl('pack').stdout == 'nolabs-ai/pi@0.2.2\n'
    layers = ctl('layers')
    assert layers.returncode == 0 and layers.stdout.count('\tvalid') == 3
    result = ctl('config', '--json')
    assert (
        result.returncode == 0
        and json.loads(result.stdout)['credentials']['env']['first']['set']['EXAMPLE_EMAIL']
    )
    runtime = fixture.paths.home / 'runtime' / ('mac' if os_name == 'macos' else 'linux')
    assert json.loads(result.stdout)['grants']['runtime_dirs'] == [str(runtime)]
    workdir = fixture.linked if os_name == 'linux' else fixture.repo
    result = ctl('profile', '--workdir=' + str(workdir))
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert 'GH_TOKEN' not in value['environment']['allow_vars']
    assert 'fixture' not in json.dumps(value['environment']['set_vars'])
    assert not list(fixture.paths.launch_state.glob('launch.*'))
    assert ctl('validate').returncode == 0
    fixture.layer('99-invalid', 'schema = 1\nunknown = true\n')
    result = ctl('layers')
    assert result.returncode and '99-invalid' in result.stdout and 'unknown key' in result.stdout


@pytest.mark.parametrize('release,status', [('6.11.99', 2), ('6.12.0', 0)])
def test_ctl_profile_uses_injected_kernel_preflight(fixture, release, status):
    result = fixture.ctl(
        'profile', '--workdir=' + str(fixture.linked), os_name='linux', release=release
    )
    assert result.returncode == status, result.stderr
    if status:
        assert 'Linux >= 6.12' in result.stderr
    else:
        assert not json.loads(result.stdout)['unsafe_macos_seatbelt_rules']
    assert not list(fixture.paths.launch_state.glob('launch.*'))
