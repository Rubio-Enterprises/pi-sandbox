import hashlib
import json
import os
import re
import secrets
import stat
import string
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_sandbox import Refusal


@dataclass(frozen=True)
class Paths:
    home: Path
    config: Path
    data: Path
    state: Path
    cache: Path
    root: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Paths:
        home = Path(env.get('HOME') or str(Path.home())).absolute()
        roots = [
            Path(env.get(f'XDG_{key}_HOME') or home / default).absolute()
            for key, default in (
                ('CONFIG', '.config'),
                ('DATA', '.local/share'),
                ('STATE', '.local/state'),
                ('CACHE', '.cache'),
            )
        ]
        return cls(
            home,
            roots[0],
            roots[1],
            roots[2],
            roots[3],
            Path(env.get('PI_SANDBOX_CONFIG_DIR') or roots[0] / 'pi-sandbox').absolute(),
        )

    @property
    def nono(self) -> Path:
        return self.config / 'nono'

    @property
    def launch_state(self) -> Path:
        return self.state / 'pi-sandbox'

    @property
    def private_cache(self) -> Path:
        return self.cache / 'pi-sandbox'

    def expand(self, value: str) -> str:
        variables = {
            'HOME': self.home,
            'XDG_CONFIG_HOME': self.config,
            'XDG_DATA_HOME': self.data,
            'XDG_STATE_HOME': self.state,
            'XDG_CACHE_HOME': self.cache,
        }
        if value == '~' or value.startswith('~/'):
            value = str(self.home) + value[1:]

        def substitute(match: re.Match[str]) -> str:
            name = match[1] or match[2]
            if name not in variables:
                raise Refusal(f'unsupported path variable: {name}')
            return str(variables[name])

        value = re.sub(
            r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)', substitute, value
        )
        if not Path(value).is_absolute() or '\x00' in value or '\n' in value:
            raise Refusal(f'path must be absolute after expansion: {value!r}')
        return os.path.normpath(value)

    def display(self, path: Path) -> str:
        try:
            return '~/' + str(path.relative_to(self.home))
        except ValueError:
            return str(path)


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def temporary_dir(parent: Path, prefix: str) -> Path:
    private_dir(parent)
    for _ in range(100):
        suffix = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        path = parent / (prefix + suffix)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return path.resolve()
    raise FileExistsError('cannot allocate a private launch directory')


def atomic_json(path: Path, value: Any) -> None:
    content = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    if path.is_file() and path.read_text() == content:
        return
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()


def content_name(stem: str, value: Any) -> str:
    return stem + '-' + hashlib.sha256(json_bytes(value)).hexdigest()[:12]


def check_trusted(info: os.stat_result, location: str, *, directory: bool) -> None:
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct_type or info.st_uid != os.geteuid():
        raise Refusal(
            f'{location}: policy must be user-owned and a regular '
            + ('directory' if directory else 'file')
        )
    if info.st_mode & 0o022:
        raise Refusal(f'{location}: policy must not be group/world-writable')
    if not directory and info.st_nlink != 1:
        raise Refusal(f'{location}: policy must have exactly one hard link')


@contextmanager
def trusted_directory(path: Path | str, *, parent: int | None = None) -> Generator[int]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise Refusal(f'{path}: cannot open policy directory without following symlinks') from error
    try:
        check_trusted(os.fstat(fd), str(path), directory=True)
        yield fd
    finally:
        os.close(fd)


def trusted_bytes(
    path: Path | str, *, parent: int | None = None, optional: bool = False
) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        if optional:
            return None
        raise
    except OSError as error:
        raise Refusal(f'{path}: cannot read policy without following symlinks') from error
    with os.fdopen(fd, 'rb') as stream:
        check_trusted(os.fstat(stream.fileno()), str(path), directory=False)
        return stream.read()


def trusted_text(
    path: Path | str, *, parent: int | None = None, optional: bool = False
) -> str | None:
    content = trusted_bytes(path, parent=parent, optional=optional)
    return None if content is None else content.decode('utf-8')
