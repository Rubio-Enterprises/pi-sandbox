from pathlib import Path

from pi_sandbox.config import Config
from pi_sandbox.git import Git
from pi_sandbox.paths import Paths

PI_DIRS = (
    '.pi/agent/extensions',
    '.pi/agent/npm',
    '.pi/agent/git',
    '.pi/agent/bin',
    '.pi/agent/skills',
    '.pi/config',
)
PI_FILES = (
    'settings.json',
    'mcp.json',
    'models.json',
    'llama-swap-discovery.json',
    'AGENTS.md',
    'keybindings.json',
    'capability-exposure.json',
)
PS_RULE = '(allow process-exec (literal "/bin/ps") (with no-sandbox))'
HOST_SOCKET_RULE = (
    r'(deny file-read-data file-write* network-outbound (regex'
    r' #"^/private/tmp/(tmux|piab)-[^/]*(/|$)"'
    r' #"^/private/var/folders/[^/]+/[^/]+/T/(herdr|vscode-git|ssh-askpass)-[^/]*(/|$)"'
    r' #"^/private/var/run/com\.apple\.launchd\.[^/]*(/|$)"))'
)
CHROME_RULE = (
    r'(allow mach-register'
    r' (global-name-regex #"^com\.google\.Chrome\.MachPortRendezvousServer\.")'
    r' (global-name-regex #"^com\.google\.Chrome\.crashpad\.child_port_handshake\.")'
    r' (global-name-regex #"^com\.google\.chrome\.for\.testing\.MachPortRendezvousServer\.")'
    r' (global-name-regex #"^com\.google\.chrome\.for\.testing\.crashpad\.child_port_handshake\.")'
    r' (global-name-regex #"^org\.chromium\.Chromium\.MachPortRendezvousServer\.")'
    r' (global-name-regex #"^org\.chromium\.crashpad\.child_port_handshake\."))'
)


def quote(value: str | Path) -> str:
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def static_rules(paths: Paths, protected: list[str]) -> list[str]:
    subpaths = [
        paths.home / name
        for name in ('.config/zsh', '.config/nono/profiles', '.config/nono/packages', *PI_DIRS)
    ]
    if paths.config.resolve() != (paths.home / '.config').resolve():
        subpaths[3:3] = [paths.config / 'zsh', paths.nono / 'profiles', paths.nono / 'packages']
    selectors = [f'(subpath {quote(p)})' for p in subpaths]
    selectors += [f'(literal {quote(paths.home / ".pi/agent" / name)})' for name in PI_FILES]
    selectors += [
        r'(regex #"/\.git/hooks(/|$)")',
        r'(regex #"/\.git/(config(\.worktree)?|worktrees/[^/]+/config\.worktree)(\.lock)?$")',
    ]
    rules = ['(deny file-write* ' + ' '.join(selectors) + ')']
    for checkout in protected:
        rules.append(
            f'(deny file-write* (require-all (subpath {quote(checkout)}) '
            f'(require-not (subpath {quote(Path(checkout) / ".git")}))))'
        )
    return [
        *rules,
        '(deny device-microphone)',
        '(deny process-exec (literal "/usr/bin/open") (literal "/usr/bin/osascript"))',
        PS_RULE,
        '(deny mach-lookup (global-name "com.apple.coreservices.appleevents") '
        '(global-name "com.apple.xpc.smd") '
        '(global-name "com.apple.backgroundtaskmanagementagent"))',
        '(allow ipc-sysv-sem)',
        CHROME_RULE,
        '(allow iokit-open (iokit-user-client-class "RootDomainUserClient"))',
        HOST_SOCKET_RULE,
    ]


def git_rules(workdir: Path, git: Git | None) -> list[str]:
    if git is None:
        return []
    rules: list[str] = []
    for target in (
        git.config,
        git.worktree_config,
        git.directory / 'config',
        git.directory / 'config.worktree',
        git.common / 'config',
        git.common / 'config.worktree',
    ):
        real = target.resolve()
        selectors = ' '.join(
            f'(literal {quote(p)})'
            for p in (target, real, str(target) + '.lock', str(real) + '.lock')
        )
        rules.append(f'(deny file-write* {selectors})')
    for target in git.hook_paths(workdir):
        rules.append(
            f'(deny file-write* (subpath {quote(target)}) (subpath {quote(target.resolve())}))'
        )
    return rules


def port_rules(config: Config) -> list[str]:
    rules: list[str] = []
    for entry in config.block('network').get('remote_port_deny', []):
        rules += [
            f'(deny network-outbound (remote ip "*:{entry["port"]}"))',
            f'(allow network-outbound (remote ip "localhost:{entry["port"]}"))',
        ]
    return rules


def host_rules(config: Config, launch: Path) -> list[str]:
    paths = config.paths
    denied = [paths.root, config.credentials_dir]
    if 'agent-mcp' not in config.integrations:
        denied.append(paths.config / 'agent-mcp')
    if 'credential-stores' not in config.integrations:
        denied += [paths.config / p for p in ('op', 'incus', 'gcloud')]
    denied.append(paths.config / 'gh')
    rules = [f'(deny file-read* file-write* (subpath {quote(p.resolve())}))' for p in denied]
    readonly = [
        paths.data / 'mise',
        paths.data / 'uv',
        paths.data / 'agent-extensions',
        paths.home / '.local/share/agent-browser/skills',
        paths.data / 'agent-browser/skills',
        paths.config / 'zsh',
        paths.config / 'worktrunk',
        paths.state / 'mise/trusted-configs',
        paths.state / 'mise/ignored-configs',
        launch / 'gitconfig',
        launch / 'profile.json',
    ]
    for target in readonly:
        rules += [
            f'(allow file-read* (subpath {quote(target.resolve())}))',
            f'(deny file-write* (subpath {quote(target.resolve())}))',
        ]
    rules.append(
        f'(deny file-write* (require-all (subpath {quote(paths.cache)}) '
        f'(require-not (subpath {quote(paths.private_cache)}))))'
    )
    return rules
