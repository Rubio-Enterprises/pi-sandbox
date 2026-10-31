import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import cast

from pi_sandbox import Refusal
from pi_sandbox.config import Config, strings, table
from pi_sandbox.platform import Platform

TIMEOUT = 30
OUTPUT_LIMIT = 1024 * 1024
SIGNALS = {signal.SIGHUP, signal.SIGINT, signal.SIGTERM}


def executable(command: list[str], workdir: Path, writable: list[str]) -> list[str]:
    name = command[0]
    if '/' in name:
        path = Path(name) if Path(name).is_absolute() else workdir / name
    else:
        search = ':'.join(
            str(workdir / part) if not Path(part).is_absolute() else part
            for part in os.get_exec_path()
        )
        found = shutil.which(name, path=search)
        if found is None:
            raise Refusal(f'hook executable is missing: {name}')
        path = Path(found)
    located = path.absolute()
    resolved = located.resolve(strict=True)
    if any(
        resolved.is_relative_to(Path(grant).resolve())
        or located.is_relative_to(Path(grant).resolve())
        for grant in writable
    ):
        raise Refusal(f'hook executable lies in a read-write grant: {located}')
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Refusal(f'hook executable must be a regular file: {located}')
        if info.st_nlink != 1:
            readonly_system = (
                info.st_uid == 0
                and info.st_uid != os.geteuid()
                and info.st_nlink > 1
                and not os.access(resolved, os.W_OK, effective_ids=True)
            )
            if not readonly_system:
                raise Refusal(f'hook executable must have exactly one hard link: {located}')
    finally:
        os.close(fd)
    return [str(located), *command[1:]]


def stop_group(process: subprocess.Popen[bytes]) -> None:
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def execute(command: list[str], payload: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    process: subprocess.Popen[bytes] | None = None
    output = [bytearray(), bytearray()]
    with tempfile.TemporaryFile() as source:
        source.write(payload.encode())
        source.seek(0)
        try:
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
            try:
                process = subprocess.Popen(
                    command,
                    stdin=source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=workdir,
                    start_new_session=True,
                    preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, mask),
                )
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            deadline = time.monotonic() + TIMEOUT
            with selectors.DefaultSelector() as selector:
                for index, stream in enumerate((process.stdout, process.stderr)):
                    if stream is not None:
                        selector.register(stream, selectors.EVENT_READ, index)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, TIMEOUT)
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        buffer = output[cast(int, key.data)]
                        if len(buffer) + len(chunk) > OUTPUT_LIMIT:
                            raise ValueError('hook output exceeds 1 MiB')
                        buffer.extend(chunk)
            status = process.wait(timeout=max(0, deadline - time.monotonic()))
            if status:
                stop_group(process)
            return subprocess.CompletedProcess(
                command, status, output[0].decode(), output[1].decode()
            )
        except BaseException as failure:
            if process is not None:
                stop_group(process)
            if isinstance(failure, subprocess.TimeoutExpired):
                failure.stderr = bytes(output[1])
            raise
        finally:
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


def run(
    config: Config, platform: Platform, workdir: Path, launch: Path, *, writable: list[str]
) -> tuple[list[str], list[str]]:
    rules: list[str] = []
    notices: list[str] = []
    payload = json.dumps(
        dict(
            schema=1,
            os=platform.os,
            workdir=str(workdir),
            launch_dir=str(launch),
            home=str(config.paths.home),
        )
    )
    for hook in config.hooks:
        if platform.os not in hook.get('platforms', ['macos', 'linux']):
            continue
        name = hook['name']
        error = ''
        try:
            command = executable(hook['command'], workdir, writable)
            result = execute(command, payload, workdir)
            error = result.stderr
            if result.returncode:
                raise ValueError('non-zero hook exit')
            if not result.stdout.strip():
                continue
            output = table(json.loads(result.stdout), 'hook output')
            if output.keys() - {'seatbelt_rules', 'notice'}:
                raise ValueError('unknown hook output keys')
            added = strings(output.get('seatbelt_rules', []), 'hook seatbelt_rules')
            if added and (platform.os != 'macos' or any(not r.startswith('(') for r in added)):
                raise ValueError('invalid Seatbelt rules for platform')
            rules += added
            notices += strings(output.get('notice', []), 'hook notice')
        except (OSError, ValueError, Refusal, subprocess.TimeoutExpired) as failure:
            if isinstance(failure, subprocess.TimeoutExpired) and isinstance(failure.stderr, bytes):
                error = failure.stderr.decode(errors='replace')
            detail = str(failure) if isinstance(failure, Refusal) else ''
            raise Refusal(
                f'hook {name} refused the launch'
                + ('\n' + detail if detail else '')
                + ('\n' + error.rstrip('\n') if error else '')
            ) from failure
    return rules, notices
