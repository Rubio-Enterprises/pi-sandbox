import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

from pi_sandbox import Refusal
from pi_sandbox.paths import Paths, atomic_json

REFERENCE = 'nolabs-ai/pi@0.2.2'
MINIMUM = (0, 80, 0)


def version() -> str:
    result = subprocess.run(['nono', '--version'], capture_output=True, text=True, check=False)
    found = re.search(r'(\d+)\.(\d+)\.(\d+)', result.stdout)
    if result.returncode or not found or tuple(map(int, found.groups())) < MINIMUM:
        raise Refusal('nono >= 0.80.0 required')
    return found[0]


def directory(paths: Paths) -> Path:
    return paths.nono / 'packages/nolabs-ai/pi'


def installed(paths: Paths) -> str:
    try:
        lock = json.loads((paths.nono / 'packages/lockfile.json').read_text())
        return str(lock['packages']['nolabs-ai/pi']['version'])
    except OSError, ValueError, KeyError, TypeError:
        return ''


def require(paths: Paths) -> None:
    if (
        installed(paths) != REFERENCE.split('@')[1]
        or not (directory(paths) / 'profiles/pi.json').is_file()
    ):
        raise Refusal(f'pinned pack {REFERENCE} is missing; run pi-sandbox-ctl setup')
    try:
        lock = json.loads((paths.nono / 'packages/lockfile.json').read_bytes())
        digest = lock['packages']['nolabs-ai/pi']['artifacts']['profiles/pi.json']['sha256']
        content = (directory(paths) / 'profiles/pi.json').read_bytes()
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r'[0-9a-f]{64}', digest)
            or hashlib.sha256(content).hexdigest() != digest
            or not isinstance(json.loads(content), dict)
        ):
            raise ValueError('invalid profile digest or content')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Refusal(
            f'pinned pack {REFERENCE} integrity check failed; repair it with nono before launching'
        ) from error


def setup(paths: Paths) -> None:
    version()
    try:
        require(paths)
    except Refusal:
        result = subprocess.run(['nono', 'pull', '-s', REFERENCE], check=False)
        if result.returncode:
            raise Refusal(f'cannot pull {REFERENCE}') from None
        require(paths)
    settings_path = paths.home / '.pi/agent/settings.json'
    try:
        settings = (
            cast(dict[str, Any], json.loads(settings_path.read_text()))
            if settings_path.exists()
            else {}
        )
        packages = cast(list[Any], settings.setdefault('packages', []))
        source = str(directory(paths))
        found = False
        for index, entry in enumerate(packages):
            if entry == source:
                packages[index] = {'source': source, 'skills': []}
                found = True
            elif isinstance(entry, dict) and cast(dict[str, Any], entry).get('source') == source:
                entry['skills'] = []
                found = True
        if not found:
            packages.append({'source': source, 'skills': []})
        atomic_json(settings_path, settings)
    except (OSError, ValueError, AttributeError, TypeError) as error:
        raise Refusal('cannot update the pack skill filter in Pi settings') from error


def skills(packs: list[Path]) -> list[str]:
    result: list[str] = []
    for path in packs:
        roots = ['skills']
        manifest = path / 'package.json'
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text())
                roots = data.get('pi', {}).get('skills', ['skills'])
                if not isinstance(roots, list) or not all(
                    isinstance(v, str) for v in cast(list[Any], roots)
                ):
                    roots = []
            except OSError, ValueError, AttributeError:
                roots = []
        for root in cast(list[str], roots):
            if root and (path / root).exists():
                result.append(str(path / root))
    return list(dict.fromkeys(result))
