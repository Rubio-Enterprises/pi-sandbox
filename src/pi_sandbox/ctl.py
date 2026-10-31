import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pi_sandbox import Refusal, pack, profile
from pi_sandbox.cli import message, split
from pi_sandbox.config import (
    MCP_NAME,
    Config,
    discover,
    layer_directories,
    load_layer,
    normalize_mcp_name,
    resolve,
    table,
)
from pi_sandbox.launch import extra_path, preview, render_wrapper, tool
from pi_sandbox.paths import Paths, atomic_json, private_dir
from pi_sandbox.platform import Platform


def validate(config: Config) -> None:
    resolve(config.paths, 'macos', config.layers)
    resolve(config.paths, 'linux', config.layers)
    tool('nono')
    pack.require(config.paths)
    private_dir(config.paths.launch_state)
    with (
        profile.pinned(config),
        tempfile.TemporaryDirectory(prefix='validate.', dir=config.paths.launch_state) as name,
    ):
        path = Path(name) / 'profile.json'
        atomic_json(
            path, {'extends': profile.chain(config), 'meta': {'name': 'pi-sandbox-validation'}}
        )
        for args in (
            *(
                [
                    'profile',
                    'validate',
                    str(config.paths.nono / 'profiles' / (layer.profile_name + '.json')),
                ]
                for layer in config.layers
                if layer.profile is not None
            ),
            ['profile', 'show', str(path), '--json'],
        ):
            result = subprocess.run(['nono', *args], capture_output=True, text=True, check=False)
            if result.returncode:
                raise Refusal('cannot resolve layer profiles: ' + result.stderr.strip())


def mcp_diagnostics(config: Config, workdir: Path | None) -> None:
    agent_dir = Path(os.environ.get('PI_CODING_AGENT_DIR') or config.paths.home / '.pi/agent')
    sources = [agent_dir / 'mcp.json']
    if workdir is not None:
        sources.append(workdir / '.pi/mcp.json')
    configured: dict[str, object] = {}
    for source in sources:
        try:
            value = table(json.loads(source.read_text(encoding='utf-8')), str(source))
            servers = table(value.get('mcpServers', {}), str(source) + '.mcpServers')
            for name, entry in servers.items():
                if not MCP_NAME.fullmatch(name):
                    raise Refusal('invalid MCP server name')
                table(entry, str(source) + '.mcpServers.' + name)
            configured.update({normalize_mcp_name(name): entry for name, entry in servers.items()})
        except (OSError, ValueError, Refusal) as error:
            sys.stdout.write(f'warning: {source}: cannot read MCP configuration: {error}\n')
    for logical, server in config.mcp.items():
        names = [logical, *server.get('names', [])]
        if not any(normalize_mcp_name(name) in configured for name in names):
            sys.stdout.write(
                f'warning: pi.mcp.{logical}: no configured MCP server named {", ".join(names)}; '
                'its hidden tools match nothing (add names = [...] in your layer)\n'
            )
    sys.stdout.write('MCP check: servers registered by extensions are not visible to this check.\n')


def doctor(config: Config, platform: Platform, workdir: Path | None = None) -> None:
    mcp_diagnostics(config, workdir)
    version = pack.version()
    pack.require(config.paths)
    platform.preflight()
    if platform.os == 'linux':
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        abi = libc.syscall(
            ctypes.c_long(444), ctypes.c_void_p(0), ctypes.c_size_t(0), ctypes.c_uint(1)
        )
        if abi < 6:
            raise Refusal('Landlock ABI >= 6 required (disabled or unavailable on this kernel)')
        sys.stdout.write(f'Landlock ABI: {abi}\n')
    validate(config)
    sys.stdout.write(f'nono: {version}\npack: {pack.REFERENCE}\nconfiguration: valid\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='pi-sandbox-ctl')
    parser.add_argument(
        'command',
        choices=['setup', 'pack', 'layers', 'config', 'profile', 'validate', 'doctor', 'prune'],
    )
    parser.add_argument(
        '--json', action='store_true', help='machine-readable resolved configuration'
    )
    args, remaining = parser.parse_known_args(argv)
    try:
        if args.command == 'pack':
            sys.stdout.write(pack.REFERENCE + '\n')
            return 0
        paths = Paths.from_env(os.environ)
        platform = Platform.detect()
        if args.command == 'layers':
            failed = False
            directories = layer_directories(paths)
            if directories:
                for directory in directories:
                    try:
                        layer = load_layer(directory, paths)
                        status = 'valid' if layer is not None else 'empty, ignored'
                        sys.stdout.write(f'{directory.name}\t{directory}\t{status}\n')
                    except Refusal as error:
                        sys.stdout.write(f'{directory.name}\t{directory}\t{error}\n')
                        failed = True
            return int(failed)
        config = resolve(paths, platform.os, discover(paths))
        if remaining and args.command not in ('profile', 'doctor'):
            raise Refusal('unexpected arguments: ' + ' '.join(remaining))
        if args.command == 'setup':
            pack.setup(paths)
            profile.sync(config)
            profile.prune(config)
        elif args.command == 'prune':
            sys.stdout.write(f'pruned {profile.prune(config)} profiles\n')
        elif args.command == 'config':
            values = config.values | {
                'pi': config.block('pi') | {'rendered_exclude_tools': config.excluded_tools}
            }
            output = (
                json.dumps(values, indent=2)
                if args.json
                else (
                    'Platform: '
                    + platform.os
                    + '\nLayers: '
                    + ', '.join(layer.name for layer in config.layers)
                    + '\n'
                    + json.dumps(values, indent=2)
                )
            )
            sys.stdout.write(output + '\n')
        elif args.command == 'profile':
            options = split(remaining)
            if options.rest:
                raise Refusal('profile accepts grant flags only')
            value = preview(config, platform, options)
            script = render_wrapper(
                config, platform, options, 'pi', paths.launch_state / 'launch.preview'
            )
            sys.stdout.write(json.dumps(value, indent=2) + '\n')
            sys.stderr.write('Pi wrapper (preview):\n' + script)
        elif args.command == 'validate':
            validate(config)
            sys.stdout.write('configuration: valid\n')
        elif args.command == 'doctor':
            doctor_parser = argparse.ArgumentParser(prog='pi-sandbox-ctl doctor')
            doctor_parser.add_argument('--workdir', help='also check project MCP configuration')
            doctor_options = doctor_parser.parse_args(remaining)
            directory = (
                extra_path(doctor_options.workdir, paths.home) if doctor_options.workdir else None
            )
            if directory is not None and not directory.is_dir():
                raise Refusal(f'workdir is not a directory: {directory}')
            doctor(config, platform, directory)
        return 0
    except (Refusal, OSError, ValueError) as error:
        message(str(error))
        return 2
