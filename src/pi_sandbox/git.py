import subprocess
from dataclasses import dataclass
from pathlib import Path

from pi_sandbox import Refusal


def query(workdir: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', '-C', str(workdir), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.rstrip('\n') if result.returncode == 0 else ''


@dataclass(frozen=True)
class Git:
    directory: Path
    common: Path
    config: Path
    worktree_config: Path
    hooks: Path
    root: Path
    worktree_config_active: bool

    @classmethod
    def discover(cls, workdir: Path) -> Git | None:
        output = query(
            workdir,
            'rev-parse',
            '--path-format=absolute',
            '--git-dir',
            '--git-common-dir',
            '--git-path',
            'config',
            '--git-path',
            'config.worktree',
            '--git-path',
            'hooks',
        )
        if not output:
            return None
        lines = output.splitlines()
        if len(lines) != 5:
            raise Refusal('cannot resolve Git policy paths')
        root = query(workdir, 'rev-parse', '--show-toplevel')
        if not root:
            raise Refusal('cannot resolve Git policy paths')
        return cls(
            Path(lines[0]),
            Path(lines[1]),
            Path(lines[2]),
            Path(lines[3]),
            Path(lines[4]),
            Path(root),
            query(workdir, 'config', '--bool', 'extensions.worktreeConfig') == 'true',
        )

    def hook_paths(self, workdir: Path) -> list[Path]:
        paths = [self.common / 'hooks', self.hooks]
        for target in paths:
            hooks = target.resolve()
            if hooks == Path('/dev/null'):
                continue
            if any(
                p.resolve().is_relative_to(hooks) for p in (workdir, self.directory, self.common)
            ):
                raise Refusal(
                    'Git hooks directory covers the workdir or Git metadata; '
                    'choose a dedicated hooks directory outside the sandbox'
                )
        return [p for p in paths if p.resolve() != Path('/dev/null')]


def identity(workdir: Path) -> tuple[str, str]:
    return query(workdir, 'config', 'user.name'), query(workdir, 'config', 'user.email')
