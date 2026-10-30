import shlex
from pathlib import Path

from pi_sandbox.config import Config
from pi_sandbox.integrations import Grants
from pi_sandbox.paths import private_dir
from pi_sandbox.platform import Platform


def render(pi: str, launch: Path, patterns: list[str], skills: list[str], *, op: bool) -> str:
    lines = ['#!/bin/sh']
    if op:
        lines += [
            'if [ -n "${PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN:-}" ]; then '
            'export OP_SERVICE_ACCOUNT_TOKEN="$PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN"; fi',
            'unset PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN',
        ]
    args = [pi]
    if patterns:
        args += ['--exclude-tools', ','.join(dict.fromkeys(patterns))]
    for skill in skills:
        args += ['--skill', skill]
    lines.append(
        'exec '
        + shlex.join(args)
        + ' --append-system-prompt "$(cat '
        + shlex.quote(str(launch / 'notice.md'))
        + ')" "$@"'
    )
    return '\n'.join(lines) + '\n'


def write(launch: Path, content: str) -> None:
    private_dir(launch / 'bin')
    path = launch / 'bin/pi'
    path.write_text(content)
    path.chmod(0o755)


def notice(
    config: Config,
    platform: Platform,
    grants: Grants,
    runtime: list[str],
    artifacts: Path,
    extra: list[str],
) -> str:
    lines = [
        '## Sandbox: nono (pi-sandbox)',
        '',
        "This Pi session runs inside nono, started by the user's `pi-sandbox` wrapper.",
        "Its limits are the user's decisions, not defects to fix.",
        '',
        '- "Operation not permitted" (EPERM) outside the grants below is a sandbox denial. '
        'Stop that step, tell the user the exact path, socket or tool and why you needed it, '
        'and do not work around it (sudo, chmod, copies, links, other paths, environment tricks, '
        'Docker or another tool). The user widens access by relaunching with '
        '`pi-sandbox --add-dirs=…` or `--add-dirs-ro=…`.',
        '- Stop and ask for a wider launch, instead of building a workaround, when the task '
        'would be markedly simpler, safer or more reliable with access you lack. This includes '
        'paths outside the grants, unavailable tools or capabilities, and multi-step detours '
        'that exist only to avoid the sandbox. Ask before starting the detour. Say exactly what '
        'you need (paths and access or capability), what the task gains, and what the sandboxed '
        'alternative costs. Then wait for the user to choose a relaunch or the longer route.',
    ]
    if platform.os == 'macos':
        lines.append(
            "- Writes to Git config/hooks, protected checkouts and Pi's own config and "
            'code (settings, MCP/model config, extensions, packages, skills) are blocked '
            'by rules `nono why` cannot see; EPERM there is intentional: stop and report it.'
        )
        for entry in config.block('network').get('remote_port_deny', []):
            lines.append(
                f'- Port {entry["port"]} ({entry["service"]}) is blocked on every host '
                'except localhost.'
            )
    else:
        lines += [
            '- Git config/hooks/info and sandbox policy stay read-only through narrow grants. '
            'Branch deletion, pack-refs and worktree add may need to run outside the sandbox.',
            '- Pathname Unix sockets are NOT isolated on Linux/WSL: host tmux, ssh-agent, '
            'Docker, the systemd user bus and WSL interop remain reachable. Do not use them '
            'to start unsandboxed commands or bypass the grants.',
        ]
    lines += [
        '- GitHub is read-only when provisioned, otherwise unavailable. Private repositories '
        'outside the token scope answer 404 or ask for credentials by design. Never push, open '
        'PRs, merge, approve, or try another token or Keychain. Commit on a branch in the '
        'worktree with the supplied `GIT_AUTHOR_*`/`GIT_COMMITTER_*` identity, then tell the '
        'user which branch and worktree to publish outside the sandbox.',
    ]
    if socket := config.block('docker').get('socket'):
        lines.append(
            f'- Use Docker only through `{socket}`. Its VM mounts bypass these rules, '
            'so never use it to change files outside this project.'
        )
    else:
        lines.append('- Docker is not configured for this launch.')
    lines += [
        '- Some tools are removed on purpose; codemode cannot call them.',
        *config.entries('notice', 'lines'),
        *extra,
        '',
        '### This launch',
    ]
    lines += [f'- Read-write: {path}' for path in dict.fromkeys(grants.rw)]
    lines += [
        '- Read-only: ' + (', '.join(dict.fromkeys([*grants.ro, *grants.read_file])) or 'none'),
        '- Supported runtime directories (read-write): ' + ', '.join(runtime),
        f'- Default screenshots: `{artifacts}/screenshots` (writable, retained after exit).',
        '- Most of $HOME outside these paths is denied.',
        "- Subagents are allowed: `subagent spawn` starts workers in this launch's private "
        'tmux server (`$TMUX_TMPDIR`) inside the sandbox. They inherit these grants, limits '
        "and removed tools. Never place workers in Herdr or the host's tmux.",
    ]
    return '\n'.join(lines) + '\n'
