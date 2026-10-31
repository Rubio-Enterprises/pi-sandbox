import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pi_sandbox import Refusal
from pi_sandbox.config import IDENTIFIER, Config
from pi_sandbox.credentials import Credentials
from pi_sandbox.platform import Platform

BASE_VARS = (
    'HOME PATH SHELL TMPDIR USER LOGNAME PWD TERM COLORTERM TERM_PROGRAM TERM_PROGRAM_VERSION '
    'EDITOR VISUAL TMP TEMP LANG LC_ALL LC_CTYPE LC_COLLATE LC_NUMERIC LC_TIME LC_MESSAGES '
    'LC_MONETARY LC_PAPER LC_NAME LC_ADDRESS LC_TELEPHONE LC_MEASUREMENT LC_IDENTIFICATION TZ '
    'XDG_CONFIG_HOME XDG_CACHE_HOME XDG_STATE_HOME XDG_DATA_HOME HTTP_PROXY HTTPS_PROXY NO_PROXY '
    'http_proxy https_proxy no_proxy NODE_EXTRA_CA_CERTS NO_BROWSER PLAYWRIGHT_MCP_SANDBOX SDKROOT'
).split()
UNSET = (
    'HERDR_ENV SSH_AUTH_SOCK GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN GH_DEBUG '
    'GIT_CONFIG_PARAMETERS TMUX TMUX_PANE DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH'
).split()
CACHES = {
    'npm_config_cache': 'npm',
    'UV_CACHE_DIR': 'uv',
    'UV_TOOL_DIR': 'uv-tools',
    'PIP_CACHE_DIR': 'pip',
    'YARN_CACHE_FOLDER': 'yarn',
    'PNPM_HOME': 'pnpm',
    'COREPACK_HOME': 'corepack',
    'BUN_INSTALL_CACHE_DIR': 'bun',
    'CARGO_HOME': 'cargo',
    'GOCACHE': 'go-build',
    'GOMODCACHE': 'go-mod',
    'GRADLE_USER_HOME': 'gradle',
    'GEM_HOME': 'gems',
    'GEM_PATH': 'gems',
    'BUNDLE_USER_HOME': 'bundle',
}


@dataclass
class Environment:
    inherited: dict[str, str]
    allow: list[str]
    set_vars: dict[str, str]


def build(
    config: Config,
    platform: Platform,
    caller: Mapping[str, str],
    launch: Path,
    browser: Path,
    tmux: Path,
    artifacts: Path,
    credentials: Credentials,
    identity: tuple[str, str],
    workdir: Path,
) -> Environment:
    paths = config.paths
    cache = paths.private_cache
    env = dict(caller)
    slot_vars = {slot['env'] for slot in config.slots.values()}
    slot_vars.update(name for slot in config.slots.values() for name in slot.get('set', {}))
    identity_vars = [
        'GIT_AUTHOR_NAME',
        'GIT_COMMITTER_NAME',
        'GIT_AUTHOR_EMAIL',
        'GIT_COMMITTER_EMAIL',
    ]
    for name in [
        'GH_TOKEN',
        'PI_SANDBOX_INFERENCE_API_KEY',
        'PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN',
        *identity_vars,
        *slot_vars,
    ]:
        env.pop(name, None)
    own = {
        'GH_CONFIG_DIR': str(launch / 'gh'),
        'GIT_CONFIG_GLOBAL': str(launch / 'gitconfig'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_COUNT': '0',
        'GIT_TERMINAL_PROMPT': '0',
        'GIT_ASKPASS': '/usr/bin/false',
        'SSH_ASKPASS': '/usr/bin/false',
        'DOCKER_CONFIG': str(launch / 'docker'),
        'npm_config_userconfig': '/dev/null',
        'MAVEN_OPTS': f'-Dmaven.repo.local={cache}/maven',
        'AGENT_BROWSER_SOCKET_DIR': str(browser),
        'PI_AGENT_BROWSER_SOCKET_DIR': str(browser),
        'AGENT_BROWSER_SCREENSHOT_DIR': str(artifacts / 'screenshots'),
        'AGENT_BROWSER_ARGS': '--no-sandbox,--no-startup-window',
        'TMUX_TMPDIR': str(tmux),
        'PLAYWRIGHT_MCP_SANDBOX': caller.get('PLAYWRIGHT_MCP_SANDBOX') or 'false',
        'MISE_CACHE_DIR': str(cache / 'mise'),
        'BUN_RUNTIME_TRANSPILER_CACHE_PATH': str(cache / 'bun-transpiler'),
    }
    own.update({key: str(cache / name) for key, name in CACHES.items()})
    java = ''
    if socket := config.block('docker').get('socket'):
        own.update(
            DOCKER_HOST='unix://' + socket,
            TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE='/var/run/docker.sock',
        )
        java = '-Ddocker.host=unix://' + socket
    else:
        env.pop('DOCKER_HOST', None)
        env.pop('TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE', None)
    own['JAVA_TOOL_OPTIONS'] = java
    allow = [*own, 'AGENT_BROWSER_SKILLS_DIR', 'USERNAME']
    allow += [name for name in caller if name.startswith('PI_ASK_USER_')]
    clipboard = caller.get('PI_CLIPBOARD_OSC52', '')
    if not clipboard and any(
        caller.get(n) for n in ('SSH_CONNECTION', 'SSH_CLIENT', 'MOSH_CONNECTION')
    ):
        clipboard = '1'
    if clipboard:
        own['PI_CLIPBOARD_OSC52'] = clipboard
        allow.append('PI_CLIPBOARD_OSC52')
    never = [
        *config.entries('env', 'never_pass'),
        *slot_vars,
        'GH_TOKEN',
        'PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN',
        'PI_SANDBOX_INFERENCE_API_KEY',
    ]
    allow += config.entries('env', 'pass')
    allow += [
        name
        for name in caller.get('SANDBOX_ENV_PASS', '').split()
        if IDENTIFIER.fullmatch(name) and not any(fnmatch.fnmatchcase(name, p) for p in never)
    ]
    if credentials.github:
        own['GH_TOKEN'] = credentials.github
        allow.append('GH_TOKEN')
    for value, names in zip(identity, (identity_vars[:2], identity_vars[2:]), strict=True):
        if value:
            for name in names:
                own[name] = value
                allow.append(name)
    own.update(credentials.env)
    allow += list(credentials.env)
    if platform.os == 'linux':
        env.pop('OP_SERVICE_ACCOUNT_TOKEN', None)
        if 'onepassword' in config.integrations:
            own['OP_CONFIG_DIR'] = str(cache / 'op')
            allow.append('OP_CONFIG_DIR')
    env.update(own)
    env['PATH'] = str(launch / 'bin') + ':' + caller.get('PATH', '')
    env['PWD'] = str(workdir)
    allow += BASE_VARS
    set_vars = {name: own[name] for name in ('JAVA_TOOL_OPTIONS', 'GEM_HOME', 'GEM_PATH')}
    if any('$' in value for value in set_vars.values()):
        raise Refusal('launch paths must not contain $')
    set_vars.update(config.block('env').get('set', {}))
    for name in [*UNSET, *config.entries('env', 'unset')]:
        env.pop(name, None)
    return Environment(env, list(dict.fromkeys(allow)), set_vars)
