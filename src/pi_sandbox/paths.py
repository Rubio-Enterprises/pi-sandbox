import json
import os
import re
import secrets
import string
import tempfile
from collections.abc import Mapping
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
