import copy
import fcntl
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from pi_sandbox import Refusal
from pi_sandbox.config import Config, Table, parents, string, table
from pi_sandbox.environment import Environment
from pi_sandbox.integrations import Grants
from pi_sandbox.paths import (
    Paths,
    atomic_json,
    check_trusted,
    content_name,
    json_bytes,
    private_dir,
    trusted_bytes,
    trusted_directory,
)
from pi_sandbox.platform import Platform


def base_profile() -> Table:
    return json.loads(files('pi_sandbox').joinpath('data/base-profile.json').read_text())


def snapshots(config: Config) -> dict[str, Table]:
    result = {content_name('pi-sandbox-base', base_profile()): base_profile()}
    names: dict[str, str] = {}
    for item in config.profiles:
        if item.profile is None:
            continue
        value = copy.deepcopy(item.profile)
        if 'extends' in value:
            value['extends'] = [names.get(name, name) for name in parents(value, item.name)]
        name = content_name('pi-sandbox-user-' + item.name, value)
        names[item.name] = name
        result[name] = value
    return result


def chain(config: Config) -> list[str]:
    installed = list(snapshots(config))
    return installed[:1] + installed[-1:] if len(installed) > 1 else installed


@contextmanager
def store_lock(config: Config) -> Generator[int]:
    root = private_dir(config.paths.nono / 'profiles')
    with trusted_directory(root) as directory:
        fd = os.open(
            '.pi-sandbox.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        try:
            check_trusted(os.fstat(fd), str(root / '.pi-sandbox.lock'), directory=False)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield directory
        finally:
            os.close(fd)


def install(root: Path, directory: int, filename: str, content: bytes) -> None:
    existing = trusted_bytes(filename, parent=directory, optional=True)
    if existing is None:
        fd, temporary = tempfile.mkstemp(prefix='.profile.', dir=root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, filename, dst_dir_fd=directory)
            except FileExistsError:
                existing = trusted_bytes(filename, parent=directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if existing is not None and existing != content:
        raise Refusal(f'immutable profile integrity mismatch: {filename}')


def release(handles: list[int]) -> None:
    for fd in handles:
        try:
            os.close(fd)
        except OSError:
            pass


def sync(config: Config, *, pin: bool = False) -> list[int]:
    root = config.paths.nono / 'profiles'
    handles: list[int] = []
    try:
        with store_lock(config) as directory:
            for name, value in snapshots(config).items():
                filename = name + '.json'
                install(root, directory, filename, json_bytes(value))
                if pin:
                    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                    handles.append(fd)
                    fcntl.flock(fd, fcntl.LOCK_SH)
        return handles
    except BaseException:
        release(handles)
        raise


@contextmanager
def pinned(config: Config) -> Generator[None]:
    handles = sync(config, pin=True)
    try:
        yield
    finally:
        release(handles)


def prune(config: Config) -> int:
    keep = {name + '.json' for name in snapshots(config)}
    cutoff = time.time() - 24 * 60 * 60
    removed = 0
    with store_lock(config) as directory:
        for name in os.listdir(directory):
            if (
                not re.fullmatch(r'pi-sandbox-[A-Za-z0-9-]+-[0-9a-f]{12}\.json', name)
                or name in keep
            ):
                continue
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mtime >= cutoff
            ):
                continue
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                os.unlink(name, dir_fd=directory)
                removed += 1
            finally:
                os.close(fd)
    return removed


def assemble(
    config: Config, platform: Platform, grants: Grants, env: Environment, rules: list[str]
) -> Table:
    denied = [str(config.paths.root), *grants.deny]
    if not config.credentials_dir.resolve().is_relative_to(config.paths.root.resolve()):
        denied.insert(1, str(config.credentials_dir))
    fs: Table = {
        'allow': list(dict.fromkeys(grants.rw)),
        'read': list(dict.fromkeys(grants.ro)),
        'allow_file': list(dict.fromkeys(grants.allow_file)),
        'read_file': list(dict.fromkeys(grants.read_file)),
        'bypass_protection': list(dict.fromkeys(grants.bypass)),
        'deny': list(dict.fromkeys(denied)),
    }
    if platform.os == 'macos':
        fs['allow'].append('@git:common-dir')
        fs['read'].append('@git:worktree')
        if socket := config.block('docker').get('socket'):
            fs['unix_socket'] = [socket]
    return {
        'extends': ['engine', *chain(config)[1:]],
        'meta': {'name': 'pi-sandbox-launch'},
        'workdir': {'access': 'readwrite'},
        'filesystem': fs,
        'environment': {'allow_vars': env.allow, 'set_vars': env.set_vars},
        'platform_overrides': {
            platform.os: {
                'environment': {'set_vars': env.set_vars},
                'unsafe_macos_seatbelt_rules': rules if platform.os == 'macos' else [],
            }
        },
    }


def keychain_safe(manifest: Any, resolved: Any, paths: Paths) -> bool:
    try:
        denies = {item['path'] for item in manifest['filesystem']['deny']}
        homes = {
            str(paths.home / 'Library/Keychains'),
            str(paths.home.resolve() / 'Library/Keychains'),
        }
        if not denies.intersection(homes) or '/Library/Keychains' not in denies:
            return False
        if any(
            'Keychains' in entry
            for entry in resolved.get('filesystem', {}).get('bypass_protection', [])
        ):
            return False
        pattern = (
            r'Keychains|com\.apple\.(SecurityServer|securityd|secd|security\.(agent|keychaind))'
        )
        return not any(
            rule.startswith('(allow') and re.search(pattern, rule)
            for rule in resolved.get('unsafe_macos_seatbelt_rules', [])
        )
    except KeyError, TypeError, AttributeError:
        return False


def keychain_preflight(path: Path, workdir: Path, paths: Paths) -> None:
    outputs: list[Any] = []
    for options in (['--format', 'manifest'], ['--json']):
        result = subprocess.run(
            ['nono', 'profile', 'show', str(path), *options],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            if result.returncode:
                raise ValueError('resolver failed')
            outputs.append(json.loads(result.stdout))
        except ValueError as error:
            raise Refusal('cannot resolve the launch profile; refusing launch') from error
    if not keychain_safe(outputs[0], outputs[1], paths):
        raise Refusal('credentialless policy did not confirm the Keychain deny; refusing launch')


def native_writable_grants(path: Path, workdir: Path, projection: Path) -> list[str]:
    result = subprocess.run(
        ['nono', 'profile', 'show', str(path), '--json'],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        if result.returncode:
            raise ValueError('resolver failed')
        value = table(json.loads(result.stdout), 'resolved nono policy')
        groups = subprocess.run(
            ['nono', 'profile', 'groups', '--json', '--all-platforms'],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
        if groups.returncode:
            raise ValueError('cannot inspect implicit groups')
        catalog = json.loads(groups.stdout)
        excluded: list[str] = []
        for group in catalog:
            entry = table(group, 'nono group')
            if entry['required']:
                if entry['allow']:
                    raise ValueError('required group has grants; cannot isolate native writes')
            else:
                excluded.append(string(entry['name'], 'nono group name'))
        # nono's JSON view is not round-trippable except for its native filesystem block
        atomic_json(
            projection,
            {
                'meta': {'name': 'pi-sandbox-grant-check'},
                'filesystem': value['filesystem'],
                'groups': {'exclude': excluded},
            },
        )
        return writable_grants(projection, workdir)
    except (KeyError, TypeError, ValueError) as error:
        raise Refusal('cannot resolve native write grants; refusing launch') from error
    finally:
        projection.unlink(missing_ok=True)


def writable_grants(path: Path, workdir: Path) -> list[str]:
    result = subprocess.run(
        ['nono', 'profile', 'show', str(path), '--format', 'manifest'],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        if result.returncode:
            raise ValueError('resolver failed')
        manifest = json.loads(result.stdout)
        grants = manifest['filesystem']['grants']
        if not isinstance(grants, list):
            raise ValueError('invalid grants')
        writable: list[str] = []
        for item in cast(list[Any], grants):
            entry = table(item, 'manifest grant')
            access = entry.get('access')
            if access not in ('read', 'write', 'readwrite'):
                raise ValueError('unknown grant access')
            if access != 'read':
                target = entry['path']
                if not isinstance(target, str):
                    raise ValueError('invalid grant path')
                if target.startswith('@git:'):
                    continue
                if not Path(target).is_absolute():
                    raise ValueError('non-absolute grant')
                writable.append(target)
        return writable
    except (KeyError, TypeError, ValueError, Refusal) as error:
        raise Refusal(
            'cannot resolve hook write grants; refusing launch: ' + result.stderr.strip()
        ) from error
