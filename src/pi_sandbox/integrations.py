from dataclasses import dataclass, field

from pi_sandbox.config import Config
from pi_sandbox.credentials import read_token
from pi_sandbox.platform import Platform


@dataclass
class Grants:
    rw: list[str] = field(default_factory=list[str])
    ro: list[str] = field(default_factory=list[str])
    allow_file: list[str] = field(default_factory=list[str])
    read_file: list[str] = field(default_factory=list[str])
    bypass: list[str] = field(default_factory=list[str])
    deny: list[str] = field(default_factory=list[str])


def grants(config: Config, platform: Platform, *, github: bool) -> Grants:
    paths = config.paths
    result = Grants()
    if 'credential-stores' not in config.integrations:
        result.deny.append(str(paths.home / '.op'))
    if 'agent-mcp' in config.integrations:
        if (paths.config / 'agent-mcp').exists():
            result.ro.append(str(paths.config / 'agent-mcp'))
        result.read_file.append(str(paths.home / '.opentabs/extension/auth.json'))
    else:
        result.deny.append(str(paths.home / '.config/agent-mcp'))
    if 'onepassword' in config.integrations and platform.os == 'macos':
        op = paths.home / '.config/op'
        settings = paths.home / (
            'Library/Group Containers/2BUA8C4S2C.com.1password/Library/'
            'Application Support/1Password/Data/settings'
        )
        if op.exists():
            result.rw.append(str(op))
            result.bypass.append(str(op))
        if settings.exists():
            result.ro.append(str(settings))
            result.bypass.append(str(settings))
        if (paths.home / '.config/1Password').exists():
            result.ro.append(str(paths.home / '.config/1Password'))
    if 'cloud-credentials' in config.integrations:
        for name in ('.aws', '.config/gcloud', '.azure', '.azd'):
            target = paths.home / name
            if target.exists():
                result.rw.append(str(target))
                if name != '.azd':
                    result.bypass.append(str(target))
    if 'keychain' in config.integrations and platform.os == 'macos' and github:
        result.rw.append(str(paths.home / 'Library/Keychains'))
        result.allow_file.append(str(paths.home / 'Library/Preferences/com.apple.security.plist'))
        result.read_file.append('/Library/Keychains/System.keychain')
        result.bypass += [
            str(paths.home / 'Library/Keychains'),
            '/Library/Keychains/System.keychain',
        ]
    return result


def credential_args(config: Config, platform: Platform) -> list[str]:
    if platform.os == 'linux' and 'onepassword' in config.integrations:
        token = config.credentials_dir / 'op-service-account.token'
        if read_token(token):
            return [
                '--env-credential-map',
                'file://' + str(token.resolve()),
                'PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN',
            ]
    return []
