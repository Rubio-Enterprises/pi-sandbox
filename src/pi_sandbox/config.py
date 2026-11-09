import copy
import json
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pi_sandbox import Refusal
from pi_sandbox.paths import Paths, trusted_directory, trusted_text
from pi_sandbox.platform import OS

Table = dict[str, Any]
INTEGRATIONS = {'credential-stores', 'onepassword', 'cloud-credentials', 'keychain', 'agent-mcp'}
IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
PATTERN = re.compile(r'^[A-Za-z0-9_*]+$')
MCP_NAME = re.compile(r'^[A-Za-z0-9_-]+$')
PROFILE_NAME = re.compile(r'^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$')

SECTIONS: dict[str, dict[str, str]] = {
    'grants': dict.fromkeys(
        (
            'runtime_dirs',
            'protected_checkouts',
        ),
        'paths',
    )
    | {'project_state_dirs': 'states'},
    'workspace': {'scratch_root': 'path', 'worktree_root': 'path'},
    'docker': {'socket': 'path'},
    'credentials': {'dir': 'path', 'env': 'slots'},
    'pi': {'exclude_tools': 'patterns', 'skill_packs': 'paths', 'mcp': 'mcp'},
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
        replacements = {
            'ro_dirs': 'filesystem.read',
            'rw_dirs': 'filesystem.allow',
            'ro_files': 'filesystem.read_file',
            'ro_files_bypass': 'filesystem.read_file + filesystem.bypass_protection',
            'env': 'environment.{allow_vars,set_vars,deny_vars}',
            'network': 'platform_overrides.macos.unsafe_macos_seatbelt_rules',
            'remote_port_deny': 'platform_overrides.macos.unsafe_macos_seatbelt_rules',
            'pass': 'environment.allow_vars',
            'set': 'environment.set_vars',
            'unset': 'environment.deny_vars',
            'never_pass': 'environment.deny_vars',
        }
        hints = '; '.join(
            f'{key}: use nono {replacements[key]}' for key in sorted(unknown) if key in replacements
        )
        raise Refusal(
            f'{location}: unknown key(s): {", ".join(sorted(unknown))}'
            + (f'; {hints}' if hints else '')
        )


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


def normalize_mcp_name(value: str, *, pattern: bool = False) -> str:
    return re.sub(r'[^A-Za-z0-9_*]' if pattern else r'[^A-Za-z0-9_]', '_', value)


def validate_mcp(value: Any, location: str) -> Table:
    servers = table(value, location)
    result: Table = {}
    for name, entry in servers.items():
        server_location = f'{location}.{name}'
        if not MCP_NAME.fullmatch(name):
            raise Refusal(f'{server_location}: invalid MCP server name')
        server = table(entry, server_location)
        keys(server, {'hide_tools', 'names'}, server_location)
        for alias in strings(server.get('names', []), server_location + '.names'):
            if not MCP_NAME.fullmatch(alias):
                raise Refusal(f'{server_location}.names: invalid MCP server name: {alias!r}')
        for pattern in strings(server.get('hide_tools', []), server_location + '.hide_tools'):
            if not pattern or any(
                char.isspace() or char == ',' or unicodedata.category(char) == 'Cc'
                for char in pattern
            ):
                raise Refusal(
                    f'{server_location}.hide_tools: invalid MCP tool pattern: {pattern!r}'
                )
        result[name] = {
            key: list(dict.fromkeys(cast(list[str], items))) for key, items in server.items()
        }
    return result


def validate_value(value: Any, kind: str, location: str, paths: Paths) -> Any:
    if kind == 'mcp':
        return validate_mcp(value, location)
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
        allowed |= {'platform'}
    keys(value, allowed, location)
    result = copy.deepcopy(value)
    if root and 'platform' in value:
        overrides = table(value['platform'], location + '.platform')
        keys(overrides, {'macos', 'linux'}, location)
        result['platform'] = {
            os: validate_section(table(v, location), paths, f'{location}.platform.{os}', root=False)
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


@dataclass(frozen=True)
class SandboxProfile:
    name: str
    path: Path
    settings: Table
    profile: Table | None = None

    sidecar: Path | None = None


@dataclass
class Config:
    paths: Paths
    profiles: list[SandboxProfile] = field(default_factory=list[SandboxProfile])
    values: Table = field(default_factory=Table)
    selected: str | None = None

    def block(self, name: str) -> Table:
        return cast(Table, self.values.get(name, {}))

    def entries(self, section: str, key: str) -> list[str]:
        return cast(list[str], self.block(section).get(key, []))

    @property
    def mcp(self) -> dict[str, Table]:
        return cast(dict[str, Table], self.block('pi').get('mcp', {}))

    @property
    def excluded_tools(self) -> list[str]:
        patterns = list(self.entries('pi', 'exclude_tools'))
        for logical, server in self.mcp.items():
            for name in [logical, *cast(list[str], server.get('names', []))]:
                for pattern in cast(list[str], server.get('hide_tools', [])):
                    patterns.append(
                        f'mcp__{normalize_mcp_name(name)}__'
                        + normalize_mcp_name(pattern, pattern=True)
                    )
        return list(dict.fromkeys(patterns))

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


def parents(value: Table, location: str) -> list[str]:
    bases = value.get('extends')
    if bases is None:
        return []
    return [bases] if isinstance(bases, str) else strings(bases, location + '.extends')


def policy_text(root: Path, filename: str) -> str | None:
    try:
        with trusted_directory(root) as fd:
            return trusted_text(filename, parent=fd, optional=True)
    except FileNotFoundError:
        return None


def discover(paths: Paths, selected: str | None = None) -> list[SandboxProfile]:
    if selected is None:
        selected = os.environ.get('PI_SANDBOX_PROFILE') or None
    if selected is None:
        return []
    if not PROFILE_NAME.fullmatch(selected):
        raise Refusal(f'invalid Sandbox Profile name: {selected!r}')
    result: list[SandboxProfile] = []
    active: list[str] = []
    loaded: dict[str, SandboxProfile] = {}

    def load(name: str) -> SandboxProfile:
        if name in loaded:
            return loaded[name]
        if not PROFILE_NAME.fullmatch(name):
            # Leave registry-reference lookup (including @version) to nono
            parts = name.partition('@')[0].split('/')
            if (
                len(parts) != 2
                or not all(parts)
                or name.startswith(('.', '~', '/'))
                or name.endswith('.json')
            ):
                raise Refusal(f'invalid nono profile reference: {name!r}')
            item = SandboxProfile(name, paths.nono / 'packages', {})
            loaded[name] = item
            return item
        source = paths.nono / 'profiles' / (name + '.json')
        raw = policy_text(source.parent, source.name)
        if raw is None:
            if not active:
                raise Refusal(f'Sandbox Profile does not exist: {source}')
            item = SandboxProfile(name, source, {})
            loaded[name] = item
            return item
        try:
            value = table(json.loads(raw), str(source))
            sidecar = paths.root / 'profiles' / (name + '.toml')
            text = policy_text(sidecar.parent, sidecar.name)
            settings = (
                validate_section(tomllib.loads(text), paths, str(sidecar), root=True)
                if text is not None
                else {}
            )
            item = SandboxProfile(
                name, source, settings, value, sidecar if text is not None else None
            )
            loaded[name] = item
            return item
        except (OSError, ValueError) as error:
            raise Refusal(f'{source}: cannot load Sandbox Profile: {error}') from error

    def visit(name: str) -> None:
        if name in active:
            raise Refusal('Sandbox Profile cycle: ' + ' -> '.join([*active, name]))
        if len(active) >= 10:
            raise Refusal('Sandbox Profile inheritance exceeds nono depth 10')
        item = load(name)
        active.append(name)
        try:
            if item.profile is not None:
                for base in parents(item.profile, str(item.path)):
                    visit(base)
            result.append(item)
        finally:
            active.pop()

    visit(selected)
    return result


def resolve(
    paths: Paths,
    os: OS,
    profiles: list[SandboxProfile] | None = None,
    *,
    selected: str | None = None,
) -> Config:
    profiles = discover(paths, selected) if profiles is None else profiles
    values: Table = {}
    for item in profiles:
        values = merge(values, {k: v for k, v in item.settings.items() if k != 'platform'})
        values = merge(values, item.settings.get('platform', {}).get(os, {}))
    config = Config(paths, profiles, values, profiles[-1].name if profiles else None)
    for name, slot in config.slots.items():
        if ('file' in slot) == ('command' in slot) or 'env' not in slot:
            raise Refusal(
                f'credentials.env.{name}: requires env and exactly one of file or command'
            )
    return config
