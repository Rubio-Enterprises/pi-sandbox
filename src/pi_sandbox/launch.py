import os
import shutil
import signal
import stat
import subprocess
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

from pi_sandbox import (
    Refusal,
    credentials,
    environment,
    hooks,
    integrations,
    linux,
    pack,
    profile,
    seatbelt,
    wrapper,
)
from pi_sandbox.cli import Arguments, message, workdir
from pi_sandbox.config import Config, resolve
from pi_sandbox.git import Git, identity, query
from pi_sandbox.integrations import Grants
from pi_sandbox.paths import Paths, atomic_json, private_dir, temporary_dir
from pi_sandbox.platform import Platform


class InterruptedError(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


@dataclass
class Plan:
    workdir: Path
    git: Git | None
    rw: list[str]
    ro: list[str]
    runtime: list[str]
    create: list[Path]
    rules: list[str]


def tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise Refusal(f'{name} is not installed' if name == 'pi' else f'{name} is required')
    return str(Path(found).absolute())


def extra_path(value: str, home: Path) -> Path:
    if value == '~' or value.startswith('~/'):
        value = str(home) + value[1:]
    path = Path(value)
    return path.absolute() if path.is_symlink() and not path.exists() else path.resolve()


def plan(config: Config, platform: Platform, args: Arguments) -> Plan:
    paths = config.paths
    directory = workdir(args.workdir, paths.home)
    git = Git.discover(directory)
    rw = [str(directory), *(str(extra_path(p, paths.home)) for p in args.rw)]
    ro = [str(extra_path(p, paths.home)) for p in args.ro]
    if platform.os == 'linux':
        linux.preflight(git, platform)
        linux.validate_grants([Path(p) for p in [*rw, *ro]], config, git, platform)
    create: list[Path] = []
    root = config.block('workspace').get('worktree_root')
    if root and git and git.common.name == '.git':
        create.append(Path(root.replace('{repo}', git.common.parent.name)))
    if args.scratch:
        root = config.block('workspace').get('scratch_root')
        if not root:
            raise Refusal('--scratch requires workspace.scratch_root in a layer')
        create.append(Path(root) / args.scratch)
    runtime = list(config.entries('grants', 'runtime_dirs'))
    origin = query(directory, 'remote', 'get-url', 'origin').removesuffix('.git')
    runtime += [
        str(paths.state / p)
        for p in config.block('grants').get('project_state_dirs', {}).get(origin, [])
    ]
    create += [Path(p) for p in runtime]
    rw += [p for p in config.entries('grants', 'rw_dirs') if Path(p).exists()]
    if platform.os == 'linux':
        linux.validate_grants([Path(p) for p in rw], config, git, platform)
        linux.validate_grants(create, config, git, platform, must_exist=False)
    rw += [str(p) for p in create]
    if (
        platform.os == 'macos'
        and (paths.state / 'mise').resolve() != (paths.home / '.local/state/mise').resolve()
    ):
        create.append(paths.state / 'mise')
        rw.append(str(paths.state / 'mise'))
    ro += [
        p
        for p in [
            *config.entries('grants', 'ro_dirs'),
            *config.entries('grants', 'protected_checkouts'),
            str(paths.data / 'agent-browser/skills'),
            str(paths.config / 'worktrunk'),
        ]
        if Path(p).exists()
    ]
    rules = seatbelt.git_rules(directory, git) if platform.os == 'macos' else []
    return Plan(directory, git, rw, ro, runtime, create, rules)


def grants(config: Config, platform: Platform, plan: Plan, *, github: bool, create: bool) -> Grants:
    result = integrations.grants(config, platform, github=github)
    result.rw = [*plan.rw, *result.rw]
    result.ro = [*plan.ro, *result.ro]
    result.read_file += config.entries('grants', 'ro_files')
    for file in config.entries('grants', 'ro_files_bypass'):
        if Path(file).is_file():
            result.read_file.append(file)
            result.bypass.append(file)
    if platform.os == 'linux':
        rw, ro = linux.git_grants(plan.git, create=create)
        result.rw += rw
        result.ro += ro
    if (socket := config.block('docker').get('socket')) and Path(socket).is_socket():
        result.bypass.append(socket)
    return result


def owned_directory(parent: int, name: str) -> int:
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError(f'refusing cleanup of unowned or aliased directory: {name}')
    os.chmod(name, stat.S_IMODE(info.st_mode) | 0o700, dir_fd=parent, follow_symlinks=False)
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def open_private_root(root: Path) -> int:
    parent = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return owned_directory(parent, root.name)
    finally:
        os.close(parent)


def remove_entry(parent: int, name: str) -> None:
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        fd = owned_directory(parent, name)
        try:
            for child_name in os.listdir(fd):
                remove_entry(fd, child_name)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def remove_private_tree(root: Path | None) -> None:
    if root is None:
        return
    try:
        fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            remove_entry(fd, root.name)
        finally:
            os.close(fd)
    except FileNotFoundError:
        pass


def cleanup_step(action: Callable[[Path | None], None], root: Path | None) -> None:
    try:
        action(root)
    except Exception as error:
        message(f'cleanup failed for {root}: {error}')


def cleanup_tmux(root: Path | None) -> None:
    if root is None:
        return
    try:
        fd = open_private_root(root)
        try:
            try:
                directory = owned_directory(fd, f'tmux-{os.getuid()}')
            except FileNotFoundError:
                return
            try:
                names = sorted(os.listdir(directory))
                if shutil.which('tmux'):
                    for name in names:
                        try:
                            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                            if stat.S_ISSOCK(info.st_mode) and info.st_nlink == 1:
                                subprocess.run(
                                    [
                                        'tmux',
                                        '-S',
                                        str(root / f'tmux-{os.getuid()}' / name),
                                        'kill-server',
                                    ],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    check=False,
                                    timeout=10,
                                )
                        except (OSError, subprocess.TimeoutExpired) as error:
                            message(f'tmux cleanup failed: {error}')
            finally:
                os.close(directory)
        finally:
            os.close(fd)
    except OSError as error:
        message(f'cannot enumerate private tmux sockets: {error}')
    finally:
        remove_private_tree(root)


def cleanup_browser(root: Path | None) -> None:
    if root is None:
        return
    try:
        fd = open_private_root(root)
        os.close(fd)
        sockets = [*root.glob('*.sock'), *root.glob('namespaces/*/run/*.sock')]
        if sockets and shutil.which('agent-browser'):
            env = dict(os.environ, AGENT_BROWSER_SOCKET_DIR=str(root))
            env.pop('AGENT_BROWSER_NAMESPACE', None)
            namespaces = [
                None,
                *sorted(p.name for p in (root / 'namespaces').glob('*') if p.is_dir()),
            ]
            for namespace in namespaces:
                if namespace is not None:
                    env['AGENT_BROWSER_NAMESPACE'] = namespace
                try:
                    subprocess.run(
                        ['agent-browser', 'close', '--all'],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=15,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    message(f'browser cleanup failed: {error}')
    except OSError as error:
        message(f'cannot enumerate private browser sockets: {error}')
    finally:
        remove_private_tree(root)


@contextmanager
def signals() -> Generator[None]:
    def interrupted(signum: int, _frame: FrameType | None) -> None:
        raise InterruptedError(signum)

    saved = {
        sig: signal.signal(sig, interrupted)
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


def child(argv: list[str], env: dict[str, str], directory: Path) -> int:
    process: subprocess.Popen[bytes] | None = None
    received = 0

    def forward(signum: int, _frame: FrameType | None) -> None:
        nonlocal received
        received = signum
        if process is not None:
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, forward)
    signal.signal(signal.SIGTERM, forward)

    def reset_sigint() -> None:
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    process = subprocess.Popen(argv, env=env, cwd=directory, preexec_fn=reset_sigint)
    if received:
        process.send_signal(received)
    status = process.wait()
    return 128 + received if received else 128 - status if status < 0 else status


def launch(args: Arguments, platform: Platform) -> int:
    pi = tool('pi')
    tool('nono')
    tool('git')
    paths = Paths.from_env(os.environ)
    workdir(args.workdir, paths.home)
    config = resolve(paths, platform.os)
    pack.version()
    pack.require(paths)
    planned = plan(config, platform, args)
    ident = identity(planned.workdir)
    if not all(ident):
        message(
            'Git user.name/user.email unresolved for workdir; '
            'launching anyway (commits will fail until set)'
        )
    launch_dir: Path | None = None
    browser: Path | None = None
    tmux: Path | None = None
    pins: list[int] = []
    with signals():
        try:
            launch_dir = temporary_dir(paths.launch_state, 'launch.')
            pins = profile.sync(config, pin=True)
            artifacts = temporary_dir(paths.state / 'agent-sandbox/artifacts', 'run.')
            private_dir(artifacts / 'screenshots')
            private_dir(paths.private_cache)
            for path in planned.create:
                private_dir(path)
            browser = temporary_dir(Path('/tmp'), 'sab.')
            tmux = temporary_dir(Path('/tmp'), 'pst.')
            private_rw = [
                str(launch_dir / 'gh'),
                str(launch_dir / 'docker'),
                str(paths.private_cache),
                str(browser),
                str(tmux),
                str(artifacts),
            ]
            candidate = grants(config, platform, planned, github=True, create=True)
            candidate.rw += private_rw
            candidate.ro.append(str(launch_dir))
            writable = list(candidate.rw)
            if any(
                platform.os in hook.get('platforms', ['macos', 'linux']) for hook in config.hooks
            ):
                check_path = launch_dir / 'hook-profile.json'
                atomic_json(
                    check_path,
                    profile.assemble(
                        config, platform, candidate, environment.Environment({}, [], {}), []
                    ),
                )
                try:
                    writable += profile.writable_grants(check_path, planned.workdir)
                finally:
                    check_path.unlink(missing_ok=True)
            if platform.os == 'macos' and planned.git is not None:
                writable.append(str(planned.git.common))
            extra_rules, extra_notices = hooks.run(
                config, platform, planned.workdir, launch_dir, writable=writable
            )
            creds = credentials.load(config)
            for line in creds.messages:
                message(line)
            current = grants(config, platform, planned, github=bool(creds.github), create=False)
            current.rw += private_rw
            current.ro.append(str(launch_dir))
            credentials.write_files(launch_dir, creds, platform.os)
            env = environment.build(
                config,
                platform,
                os.environ,
                launch_dir,
                browser,
                tmux,
                artifacts,
                creds,
                ident,
                planned.workdir,
            )
            rules = []
            if platform.os == 'macos':
                rules = [
                    *planned.rules,
                    *seatbelt.port_rules(config),
                    *extra_rules,
                    *seatbelt.host_rules(config, launch_dir),
                    *seatbelt.static_rules(paths, config.entries('grants', 'protected_checkouts')),
                ]
            (launch_dir / 'notice.md').write_text(
                wrapper.notice(
                    config,
                    platform,
                    current,
                    planned.runtime,
                    artifacts,
                    [*creds.notices, *extra_notices],
                )
            )
            wrapper.write(
                launch_dir,
                render_wrapper(config, platform, args, pi, launch_dir),
            )
            profile_path = launch_dir / 'profile.json'
            atomic_json(profile_path, profile.assemble(config, platform, current, env, rules))
            if platform.os == 'macos' and not creds.github:
                profile.keychain_preflight(profile_path, planned.workdir, paths)
            argv = ['nono', 'wrap'] if platform.os == 'macos' else ['nono', 'run', '--silent']
            argv += ['--profile', str(profile_path), '--allow-cwd']
            if platform.os == 'linux':
                argv += [
                    '--suppress-save-prompt',
                    str(launch_dir),
                    '--suppress-save-prompt',
                    str(paths.private_cache),
                    *integrations.credential_args(config, platform),
                ]
            return child([*argv, '--', 'pi', *args.rest], env.inherited, planned.workdir)
        finally:
            for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, signal.SIG_IGN)
            try:
                cleanup_step(cleanup_tmux, tmux)
            finally:
                try:
                    cleanup_step(cleanup_browser, browser)
                finally:
                    try:
                        cleanup_step(remove_private_tree, launch_dir)
                    finally:
                        profile.release(pins)


def render_wrapper(
    config: Config, platform: Platform, args: Arguments, pi: str, launch: Path
) -> str:
    return wrapper.render(
        pi,
        launch,
        [*config.excluded_tools, *args.patterns],
        pack.skills(
            [pack.directory(config.paths), *(Path(p) for p in config.entries('pi', 'skill_packs'))]
        ),
        op=platform.os == 'linux' and 'onepassword' in config.integrations,
    )


def preview(config: Config, platform: Platform, args: Arguments) -> dict[str, Any]:
    planned = plan(config, platform, args)
    current = grants(config, platform, planned, github=False, create=False)
    paths = config.paths
    launch = paths.launch_state / 'launch.preview'
    artifacts = paths.state / 'agent-sandbox/artifacts/run.preview'
    current.ro.append(str(launch))
    current.rw += [
        str(launch / 'gh'),
        str(launch / 'docker'),
        str(paths.private_cache),
        '/tmp/sab.preview',
        '/tmp/pst.preview',
        str(artifacts),
    ]
    env = environment.build(
        config,
        platform,
        os.environ,
        launch,
        Path('/tmp/sab.preview'),
        Path('/tmp/pst.preview'),
        artifacts,
        credentials.Credentials(),
        ('', ''),
        planned.workdir,
    )
    rules = [
        *planned.rules,
        *seatbelt.port_rules(config),
        *seatbelt.host_rules(config, launch),
        *seatbelt.static_rules(paths, config.entries('grants', 'protected_checkouts')),
    ]
    return profile.assemble(config, platform, current, env, rules)
