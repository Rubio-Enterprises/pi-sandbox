import copy
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pi_sandbox import Refusal
from pi_sandbox.paths import Paths, content_name, trusted_directory, trusted_text
from pi_sandbox.platform import OS

Table = dict[str, Any]
INTEGRATIONS = {'credential-stores', 'onepassword', 'cloud-credentials', 'keychain', 'agent-mcp'}
IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
PATTERN = re.compile(r'^[A-Za-z0-9_*]+$')
LAYER_NAME = re.compile(r'^[0-9]{2}-[a-z0-9][a-z0-9-]*$')

SECTIONS: dict[str, dict[str, str]] = {
    'grants': dict.fromkeys(
        (
            'ro_dirs',
            'rw_dirs',
            'ro_files',
            'ro_files_bypass',
            'runtime_dirs',
            'protected_checkouts',
        ),
        'paths',
    )
    | {'project_state_dirs': 'states'},
    'workspace': {'scratch_root': 'path', 'worktree_root': 'path'},
    'docker': {'socket': 'path'},
    'network': {'remote_port_deny': 'ports'},
    'credentials': {'dir': 'path', 'env': 'slots'},
    'env': {'pass': 'names', 'set': 'vars', 'unset': 'names', 'never_pass': 'strings'},
    'pi': {'exclude_tools': 'patterns', 'skill_packs': 'paths'},
    'notice': {'lines': 'strings'},
}


def strings(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in cast(list[Any], value)):
        raise Refusal(f'{location}: expected a list of strings')
    return cast(list[str], value)


def table(value: Any, location: str) -> Table:
    if not isinstance(value, dict):
        raise Refusal(f'{location}: expected a table')
    return cast(Table, value)


def keys(value: Table, allowed: set[str], location: str) -> None:
    if unknown := value.keys() - allowed:
        raise Refusal(f'{location}: unknown key(s): {", ".join(sorted(unknown))}')


def string(value: Any, location: str) -> str:
    if not isinstance(value, str) or '\x00' in value:
        raise Refusal(f'{location}: expected a string without NUL')
    return value


def variables(value: Any, location: str) -> Table:
    result = table(value, location)
    for name, item in result.items():
        if not IDENTIFIER.fullmatch(name):
            raise Refusal(f'{location}: invalid environment name: {name}')
        string(item, location)
    return result


def validate_value(value: Any, kind: str, location: str, paths: Paths) -> Any:
    if kind == 'path':
        return paths.expand(string(value, location))
    if kind in ('strings', 'paths', 'names', 'patterns'):
        items = strings(value, location)
        if kind == 'paths':
            return [paths.expand(v) for v in items]
        for item in items:
            if kind == 'names' and not IDENTIFIER.fullmatch(item):
                raise Refusal(f'{location}: invalid environment name: {item}')
            if kind == 'patterns' and not PATTERN.fullmatch(item):
                raise Refusal(f'{location}: invalid Pi tool pattern: {item}')
        return items
    if kind == 'vars':
        return variables(value, location)
    if kind == 'states':
        result = table(value, location)
        for origin, dirs in result.items():
            for directory in strings(dirs, location):
                if (
                    Path(directory).is_absolute()
                    or '..' in Path(directory).parts
                    or directory in ('', '.')
                ):
                    raise Refusal(f'{location}: state directories must be relative without ..')
            string(origin, location)
        return result
    if kind == 'ports':
        if not isinstance(value, list):
            raise Refusal(f'{location}: expected a list')
        for entry in cast(list[Any], value):
            port = table(entry, location)
            keys(port, {'port', 'service'}, location)
            if type(port.get('port')) is not int or not 1 <= port['port'] <= 65535:
                raise Refusal(f'{location}: port must be between 1 and 65535')
            string(port.get('service'), location)
        return cast(list[Any], value)
    if kind == 'slots':
        slots = table(value, location)
        for name, item in slots.items():
            slot = table(item, f'{location}.{name}')
            keys(slot, {'file', 'command', 'env', 'set', 'missing', 'notice'}, location)
            for key in ('file', 'command', 'env', 'missing', 'notice'):
                if key in slot:
                    string(slot[key], location)
            if 'env' in slot and not IDENTIFIER.fullmatch(slot['env']):
                raise Refusal(f'{location}: invalid slot environment name')
            if 'set' in slot:
                variables(slot['set'], location)
            if 'file' in slot:
                file = slot['file']
                if file.startswith(('~', '$', '/')):
                    slot['file'] = paths.expand(file)
                elif '..' in Path(file).parts or not file:
                    raise Refusal(
                        f'{location}: relative credential file must not escape credentials.dir'
                    )
        return slots
    raise AssertionError(kind)


def validate_section(value: Table, paths: Paths, location: str, *, root: bool) -> Table:
    allowed = set(SECTIONS) | {'integrations', 'hooks'}
    if root:
        allowed |= {'schema', 'layer', 'platform'}
    keys(value, allowed, location)
    result = copy.deepcopy(value)
    if root:
        if type(value.get('schema')) is not int or value['schema'] != 1:
            raise Refusal(f'{location}: schema must be 1')
        if 'layer' in value:
            metadata = table(value['layer'], location + '.layer')
            keys(metadata, {'description'}, location + '.layer')
            if 'description' in metadata:
                string(metadata['description'], location)
        if 'platform' in value:
            overrides = table(value['platform'], location + '.platform')
            keys(overrides, {'macos', 'linux'}, location)
            result['platform'] = {
                os: validate_section(
                    table(v, location), paths, f'{location}.platform.{os}', root=False
                )
                for os, v in overrides.items()
            }
    for section, fields in SECTIONS.items():
        if section not in value:
            continue
        block = table(value[section], location + '.' + section)
        keys(block, set(fields), location + '.' + section)
        result[section] = {
            k: validate_value(v, fields[k], f'{location}.{section}.{k}', paths)
            for k, v in block.items()
        }
    if 'integrations' in value:
        names = strings(value['integrations'], location)
        if set(names) - INTEGRATIONS:
            raise Refusal(f'{location}: unknown integration')
    if 'hooks' in value:
        if not isinstance(value['hooks'], list):
            raise Refusal(f'{location}: hooks must be a list')
        for index, item in enumerate(cast(list[Any], value['hooks'])):
            hook = table(item, location)
            keys(hook, {'name', 'command', 'platforms'}, location)
            if not string(hook.get('name'), location):
                raise Refusal(f'{location}: hook name must not be empty')
            command = strings(hook.get('command'), location)
            if not command or not command[0]:
                raise Refusal(f'{location}: hook command must not be empty')
            if set(strings(hook.get('platforms', ['macos', 'linux']), location)) - {
                'macos',
                'linux',
            }:
                raise Refusal(f'{location}: invalid hook platform')
            if command[0].startswith('~'):
                command[0] = paths.expand(command[0])
            result['hooks'][index]['command'] = command
    return result


def merge(left: Table, right: Table) -> Table:
    result = copy.deepcopy(left)
    for key, value in right.items():
        old = result.get(key)
        if isinstance(old, dict) and isinstance(value, dict):
            result[key] = merge(cast(Table, old), cast(Table, value))
        elif isinstance(old, list) and isinstance(value, list):
            combined = cast(list[Any], old)
            for item in cast(list[Any], value):
                if key == 'hooks':
                    combined = [v for v in combined if v['name'] != item['name']]
                if item not in combined:
                    combined.append(copy.deepcopy(item))
            result[key] = combined
        else:
            result[key] = copy.deepcopy(value)
    return result


def validate_profile(value: Any, name: str, location: str) -> Table:
    fragment = table(value, location)

    def check(node: Table) -> None:
        forbidden = {'extends', 'unsafe_macos_seatbelt_rules', 'workdir'} & node.keys()
        if forbidden:
            raise Refusal(f'{location}: forbidden profile key(s): {", ".join(sorted(forbidden))}')
        for key, item in node.items():
            if isinstance(item, dict):
                check(cast(Table, item))
            if key == 'meta':
                meta = table(item, location)
                if meta.get('name', name) != name:
                    raise Refusal(f'{location}: meta.name must be {name}')

    check(fragment)
    fragment.setdefault('meta', {})['name'] = name
    return fragment


@dataclass(frozen=True)
class Layer:
    name: str
    path: Path
    settings: Table
    profile: Table | None = None

    @property
    def profile_name(self) -> str:
        return content_name('pi-sandbox-layer-' + self.name, self.profile)


@dataclass
class Config:
    paths: Paths
    layers: list[Layer] = field(default_factory=list[Layer])
    values: Table = field(default_factory=Table)

    def block(self, name: str) -> Table:
        return cast(Table, self.values.get(name, {}))

    def entries(self, section: str, key: str) -> list[str]:
        return cast(list[str], self.block(section).get(key, []))

    @property
    def integrations(self) -> list[str]:
        return cast(list[str], self.values.get('integrations', []))

    @property
    def credentials_dir(self) -> Path:
        return Path(self.block('credentials').get('dir', self.paths.root / 'credentials'))

    @property
    def slots(self) -> dict[str, Table]:
        return cast(dict[str, Table], self.block('credentials').get('env', {}))

    @property
    def hooks(self) -> list[Table]:
        return cast(list[Table], self.values.get('hooks', []))


def layer_directories(paths: Paths) -> list[Path]:
    root = paths.root / 'layers'
    try:
        with trusted_directory(root) as fd:
            return [root / name for name in sorted(os.listdir(fd))]
    except FileNotFoundError:
        return []


def discover(paths: Paths) -> list[Layer]:
    result: list[Layer] = []
    root = paths.root / 'layers'
    try:
        with trusted_directory(root) as fd:
            for name in sorted(os.listdir(fd)):
                if layer := _load_layer(root / name, paths, fd):
                    result.append(layer)
    except FileNotFoundError as error:
        if error.filename == str(root):
            return []
        raise Refusal(f'{root}: cannot load policy: {error}') from error
    return result


def load_layer(directory: Path, paths: Paths) -> Layer | None:
    with trusted_directory(paths.root / 'layers') as fd:
        return _load_layer(directory, paths, fd)


def _load_layer(directory: Path, paths: Paths, parent: int) -> Layer | None:
    if not LAYER_NAME.fullmatch(directory.name):
        raise Refusal(f'{directory}: invalid layer directory name')
    try:
        with trusted_directory(directory.name, parent=parent) as fd:
            if not os.listdir(fd):
                return None
            source = trusted_text('layer.toml', parent=fd)
            value = tomllib.loads(source or '')
            settings = validate_section(value, paths, str(directory / 'layer.toml'), root=True)
            fragment = trusted_text('profile.json', parent=fd, optional=True)
            profile = (
                validate_profile(
                    json.loads(fragment),
                    'pi-sandbox-layer-' + directory.name,
                    str(directory / 'profile.json'),
                )
                if fragment is not None
                else None
            )
    except (OSError, ValueError) as error:
        raise Refusal(f'{directory}: cannot load layer: {error}') from error
    return Layer(directory.name, directory, settings, profile)


def resolve(paths: Paths, os: OS, layers: list[Layer] | None = None) -> Config:
    layers = discover(paths) if layers is None else layers
    values: Table = {}
    for layer in layers:
        values = merge(
            values,
            {k: v for k, v in layer.settings.items() if k not in ('schema', 'layer', 'platform')},
        )
        values = merge(values, layer.settings.get('platform', {}).get(os, {}))
    config = Config(paths, layers, values)
    for name, slot in config.slots.items():
        if ('file' in slot) == ('command' in slot) or 'env' not in slot:
            raise Refusal(
                f'credentials.env.{name}: requires env and exactly one of file or command'
            )
    return config
