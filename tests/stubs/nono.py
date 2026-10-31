import fnmatch
import json
import os
import socket
import sys
from pathlib import Path

out = Path(os.environ['TEST_OUT'])
args = sys.argv[1:]
with (out / 'calls').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if args == ['--version']:
    print(os.environ.get('TEST_NONO_VERSION', 'nono 0.80.0'))
    raise SystemExit(0)
if args[:2] == ['profile', 'validate']:
    raise SystemExit(0)
if args[:2] == ['profile', 'show']:
    if os.environ.get('TEST_RESOLVER_FAIL'):
        raise SystemExit(1)
    if '--format' in args:
        deny = (
            []
            if os.environ.get('TEST_DROP_DENY')
            else [{'path': str(Path.home() / 'Library/Keychains')}, {'path': '/Library/Keychains'}]
        )
        p = json.loads(Path(args[2]).read_text())
        grants = []
        root = (
            Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'nono/profiles'
        )

        def collect(value):
            parents = value.get('extends', [])
            if isinstance(parents, str):
                parents = [parents]
            for name in parents:
                source = root / (name + '.json')
                if source.is_file():
                    collect(json.loads(source.read_text()))
            for block in (value, *value.get('platform_overrides', {}).values()):
                for key in ('allow', 'write', 'allow_file', 'write_file'):
                    for path in block.get('filesystem', {}).get(key, []):
                        if path.startswith('@git:'):
                            continue
                        path = os.path.expandvars(path).replace('~/', str(Path.home()) + '/')
                        grants.append({'path': path, 'access': 'readwrite', 'type': 'directory'})

        collect(p)
        grants += [
            {'path': str(Path.home() / '.pi'), 'access': 'readwrite'},
            {'path': str(Path.home() / '.config/nono/profile-drafts'), 'access': 'readwrite'},
        ]
        print(json.dumps({'filesystem': {'deny': deny, 'grants': grants}}))
    else:
        p = json.loads(Path(args[2]).read_text())
        if os.environ.get('TEST_BYPASS'):
            p['filesystem']['bypass_protection'].append('$HOME/Library/Keychains')
        if os.environ.get('TEST_RAW_ALLOW'):
            p['unsafe_macos_seatbelt_rules'].append(
                '(allow mach-lookup (global-name "com.apple.SecurityServer"))'
            )
        print(json.dumps(p))
    raise SystemExit(0)
assert args[0] in ('run', 'wrap'), args
path = Path(args[args.index('--profile') + 1])
profile = json.loads(path.read_text())
env = {
    k: v
    for k, v in os.environ.items()
    if any(fnmatch.fnmatchcase(k, pattern) for pattern in profile['environment']['allow_vars'])
    and k not in ('JAVA_TOOL_OPTIONS', 'GEM_HOME', 'GEM_PATH')
}
env.update(profile['environment']['set_vars'])
env['NONO_CAP_FILE'] = '/fixture/nono-cap'
if '--env-credential-map' in args:
    i = args.index('--env-credential-map')
    env[args[i + 2]] = Path(args[i + 1].removeprefix('file://')).read_text().rstrip('\n')
capture = dict(
    profile=profile,
    env=env,
    argv=args,
    cwd=os.getcwd(),
    notice=(path.parent / 'notice.md').read_text(),
    wrapper=(path.parent / 'bin/pi').read_text(),
    gitconfig=(path.parent / 'gitconfig').read_text(),
    gh_files=list((path.parent / 'gh').iterdir()),
    docker=json.loads((path.parent / 'docker/config.json').read_text()),
    mode=path.parent.stat().st_mode & 0o777,
    browser_mode=Path(env['AGENT_BROWSER_SOCKET_DIR']).stat().st_mode & 0o777,
    tmux_mode=Path(env['TMUX_TMPDIR']).stat().st_mode & 0o777,
)
(out / 'capture.json').write_text(json.dumps(capture))
if os.environ.get('TEST_SOCKETS'):
    tmux = Path(env['TMUX_TMPDIR'])
    sockets = tmux / f'tmux-{os.getuid()}'
    sockets.mkdir(mode=0o700)
    for name in ('default', 'pi-subagent'):
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(sockets / name))
        server.close()
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(tmux / 'host'))
    server.close()
    (sockets / 'link').symlink_to(tmux / 'host')
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(sockets / 'hardlink'))
    server.close()
    os.link(sockets / 'hardlink', tmux / 'second-link')
browser = Path(env['AGENT_BROWSER_SOCKET_DIR'])
kind = os.environ.get('TEST_BROWSER')
if kind == 'namespace':
    (browser / 'namespaces/ns1/run').mkdir(parents=True)
    (browser / 'namespaces/ns1/run/ns1.sock').touch()
elif kind == 'root':
    (browser / 'default.sock').touch()
elif kind == 'plain':
    (browser / 'not-a-socket.txt').touch()
Path(env['AGENT_BROWSER_SCREENSHOT_DIR'], 'fixture.png').touch()
env.update({k: v for k, v in os.environ.items() if k.startswith('TEST_')})
os.execvpe('pi', ['pi', *args[args.index('--') + 2 :]], env)
