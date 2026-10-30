import base64
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pi_sandbox.config import Config
from pi_sandbox.paths import atomic_json, private_dir
from pi_sandbox.platform import OS


def read_token(path: Path) -> str:
    try:
        return path.read_text().rstrip('\n')
    except OSError:
        return ''


@dataclass
class Credentials:
    github: str = ''
    ghcr: str = ''
    env: dict[str, str] = field(default_factory=dict[str, str])
    notices: list[str] = field(default_factory=list[str])
    messages: list[str] = field(default_factory=list[str])


def load(config: Config) -> Credentials:
    result = Credentials()
    directory = config.credentials_dir
    result.ghcr = read_token(directory / 'pi-sandbox-ghcr.token')
    result.github = read_token(directory / 'pi-sandbox-github.token')
    if not result.ghcr:
        path = config.paths.display(directory / 'pi-sandbox-ghcr.token')
        result.messages.append(f'private ghcr.io images off: no token at {path}')
    if result.github:
        result.messages.append('GitHub read-only; publish branches from outside the sandbox')
    else:
        path = config.paths.display(directory / 'pi-sandbox-github.token')
        result.messages.append(f'GitHub off: no token at {path}')
    for slot in config.slots.values():
        path = directory / slot['file'] if 'file' in slot else None
        if path is not None:
            value = read_token(path)
        else:
            command = subprocess.run(
                ['sh', '-c', slot['command']],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            value = command.stdout.rstrip('\n') if command.returncode == 0 else ''
        if value:
            result.env[slot['env']] = value
            result.env.update(slot.get('set', {}))
            if slot.get('notice'):
                result.notices.append(slot['notice'])
        elif slot.get('missing'):
            result.messages.append(
                slot['missing'].replace(
                    '{path}', config.paths.display(path) if path else '<command>'
                )
            )
    return result


def write_files(launch: Path, credentials: Credentials, os: OS) -> None:
    private_dir(launch / 'gh')
    private_dir(launch / 'docker')
    auths: dict[str, dict[str, str]] = {'https://index.docker.io/v1/': {}}
    if credentials.ghcr:
        auths['ghcr.io'] = {
            'auth': base64.b64encode(('x-access-token:' + credentials.ghcr).encode()).decode()
        }
    plugins = (
        ['/opt/homebrew/lib/docker/cli-plugins', '/usr/local/lib/docker/cli-plugins']
        if os == 'macos'
        else [
            '/usr/local/lib/docker/cli-plugins',
            '/usr/libexec/docker/cli-plugins',
            '/usr/lib/docker/cli-plugins',
        ]
    )
    atomic_json(launch / 'docker/config.json', {'auths': auths, 'cliPluginsExtraDirs': plugins})
    helper = '!gh auth git-credential' if credentials.github else '"!f() { echo quit=1; }; f"'
    (launch / 'gitconfig').write_text(
        '[credential]\n\thelper = \n[credential "https://github.com"]\n'
        f'\thelper = {helper}\n[commit]\n\tgpgsign = false\n[user]\n\tuseConfigOnly = true\n'
    )
    (launch / 'gitconfig').chmod(0o600)
