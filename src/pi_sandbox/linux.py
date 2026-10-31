import re
from pathlib import Path

from pi_sandbox import Refusal
from pi_sandbox.config import Config
from pi_sandbox.git import Git
from pi_sandbox.platform import Platform


def native_paths(paths: list[Path], platform: Platform) -> None:
    if platform.wsl:
        for path in paths:
            path = path.resolve() if path.exists() else path.absolute()
            if re.match(r'^/mnt/[A-Za-z](/|$)', str(path)):
                raise Refusal(
                    f'refusing Windows drive grant {path}; '
                    'keep projects and context on the Linux ext4 filesystem'
                )


def preflight(git: Git | None, platform: Platform) -> None:
    platform.preflight()
    if git is None:
        return
    common, directory = git.common.resolve(), git.directory.resolve()
    native_paths([common, directory, git.root], platform)
    if common == directory:
        raise Refusal(
            'refusing main Git checkout on Linux; use wt switch -c <branch> '
            'and launch in the linked worktree'
        )
    if directory.parent != common / 'worktrees':
        raise Refusal('unsupported Git metadata layout; use a linked worktree')
    if git.worktree_config_active:
        raise Refusal(
            'Linux cannot protect active config.worktree inside writable metadata; '
            'use a worktree without extensions.worktreeConfig'
        )


def validate_grants(
    grants: list[Path],
    config: Config,
    git: Git | None,
    platform: Platform,
    *,
    must_exist: bool = True,
) -> None:
    paths = config.paths
    floors = [
        paths.home / '.config/nono',
        paths.nono,
        paths.home / '.config/zsh',
        paths.config / 'zsh',
        *(Path(p) for p in config.entries('grants', 'protected_checkouts')),
        config.credentials_dir,
        paths.root,
    ]
    if git:
        floors.append(git.common)
        if git.hooks.resolve() != Path('/dev/null'):
            floors.append(git.hooks)
    native_paths(grants, platform)
    for grant in grants:
        directory = (
            grant.absolute() if grant.is_symlink() and not grant.exists() else grant.resolve()
        )
        for floor in floors:
            floor = floor.resolve()
            if directory.is_relative_to(floor) or floor.is_relative_to(directory):
                raise Refusal(
                    f'refusing grant {directory}: overlaps protected policy or Git path {floor}'
                )
        if must_exist and not directory.is_dir():
            raise Refusal(f'grant is not a directory: {directory}')


def git_grants(git: Git | None, *, create: bool) -> tuple[list[str], list[str]]:
    if git is None:
        return [], []
    writable = [git.common / name for name in ('objects', 'refs', 'logs')] + [git.directory]
    if create:
        for path in writable:
            path.mkdir(parents=True, exist_ok=True)
    return [str(p.resolve()) for p in writable], [
        str(git.common.resolve()),
        str(git.root.resolve()),
    ]
