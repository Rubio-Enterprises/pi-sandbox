import json
import re
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any

from pi_sandbox import Refusal
from pi_sandbox.config import Config, Table
from pi_sandbox.environment import Environment
from pi_sandbox.integrations import Grants
from pi_sandbox.paths import Paths, atomic_json
from pi_sandbox.platform import Platform


def chain(config: Config) -> list[str]:
    return [
        'pi-sandbox-base',
        *(layer.profile_name for layer in config.layers if layer.profile is not None),
    ]


def sync(config: Config) -> None:
    root = config.paths.nono / 'profiles'
    base = json.loads(files('pi_sandbox').joinpath('data/base-profile.json').read_text())
    atomic_json(root / 'pi-sandbox-base.json', base)
    expected: set[Path] = set()
    for layer in config.layers:
        if layer.profile is not None:
            destination = root / (layer.profile_name + '.json')
            atomic_json(destination, layer.profile)
            expected.add(destination)
    for path in root.glob('pi-sandbox-layer-*.json'):
        if path not in expected:
            path.unlink()


def assemble(
    config: Config, platform: Platform, grants: Grants, env: Environment, rules: list[str]
) -> Table:
    fs: Table = {
        'allow': list(dict.fromkeys(grants.rw)),
        'read': list(dict.fromkeys(grants.ro)),
        'allow_file': list(dict.fromkeys(grants.allow_file)),
        'read_file': list(dict.fromkeys(grants.read_file)),
        'bypass_protection': list(dict.fromkeys(grants.bypass)),
        'deny': list(
            dict.fromkeys([str(config.paths.root), str(config.credentials_dir), *grants.deny])
        ),
    }
    if platform.os == 'macos':
        fs['allow'].append('@git:common-dir')
        fs['read'].append('@git:worktree')
        if socket := config.block('docker').get('socket'):
            fs['unix_socket'] = [socket]
    return {
        'extends': chain(config),
        'meta': {'name': 'pi-sandbox-launch'},
        'workdir': {'access': 'readwrite'},
        'filesystem': fs,
        'environment': {'allow_vars': env.allow, 'set_vars': env.set_vars},
        'unsafe_macos_seatbelt_rules': rules if platform.os == 'macos' else [],
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
