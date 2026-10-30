"""Report kernel outcomes over stdout without writing protected test state."""

import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path


def filesystem(op):
    path = Path(op['path'])
    try:
        if op['op'] == 'read':
            path.read_bytes()
        elif op['op'] == 'append':
            with path.open('a') as stream:
                stream.write('probe')
        elif op['op'] == 'create':
            with path.open('x') as stream:
                stream.write('probe')
            path.unlink()
        elif op['op'] == 'mkdir':
            path.mkdir(parents=True)
            path.rmdir()
        elif op['op'] == 'replace':
            temporary = path.with_name(path.name + '.probe-tmp')
            temporary.write_text('probe')
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            raise ValueError(op['op'])
    except OSError as error:
        return {'errno': error.errno}
    return {'errno': 0}


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
        return dict(rc=result.returncode, out=result.stdout[-4000:], err=result.stderr[-4000:])
    except OSError as error:
        return dict(rc=None, errno=error.errno)
    except subprocess.TimeoutExpired:
        return dict(rc=None, timeout=True)


def execute(op):
    kind = op['op']
    if kind == 'env':
        return dict(os.environ)
    if kind == 'pid':
        return os.getpid()
    if kind == 'exec':
        return command(op['argv'])
    if kind == 'connect':
        with socket.socket(socket.AF_UNIX if 'unix' in op else socket.AF_INET) as client:
            client.settimeout(3)
            try:
                client.connect(op['unix'] if 'unix' in op else ('127.0.0.1', op['port']))
            except OSError as error:
                return {'errno': error.errno}
        return {'errno': 0}
    if kind == 'env-dir':
        base = os.environ.get(op['var'])
        if not base:
            return {'errno': None}
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return {'errno': error.errno}
        return filesystem({'op': 'create', 'path': str(Path(base) / '.probe')})
    if kind == 'tmux-child':
        out = Path(op['out'])
        argv = [sys.executable, __file__, op['spec']]
        cmd = shlex.join(argv) + ' > ' + shlex.quote(str(out) + '.tmp')
        cmd += ' && mv ' + shlex.quote(str(out) + '.tmp') + ' ' + shlex.quote(str(out))
        cmd += ' && exec sleep 120'
        started = command(
            [
                op['tmux'],
                '-f',
                '/dev/null',
                '-L',
                'pi-subagent',
                'new-session',
                '-d',
                '-s',
                'probe',
                cmd,
            ]
        )
        for _ in range(100):
            if out.exists():
                break
            time.sleep(0.05)
        return {'started': started, 'child': json.loads(out.read_text()) if out.exists() else None}
    return filesystem(op)


if __name__ == '__main__':
    spec = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps({op['id']: execute(op) for op in spec['ops']}))
