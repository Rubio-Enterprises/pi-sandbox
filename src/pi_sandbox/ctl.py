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
from pi_sandbox.config import Config, discover, layer_directories, load_layer, resolve
from pi_sandbox.launch import preview, tool
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


def doctor(config: Config, platform: Platform) -> None:
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
        if remaining and args.command != 'profile':
            raise Refusal('unexpected arguments: ' + ' '.join(remaining))
        if args.command == 'setup':
            pack.setup(paths)
            profile.sync(config)
            profile.prune(config)
        elif args.command == 'prune':
            sys.stdout.write(f'pruned {profile.prune(config)} profiles\n')
        elif args.command == 'config':
            output = (
                json.dumps(config.values, indent=2)
                if args.json
                else (
                    'Platform: '
                    + platform.os
                    + '\nLayers: '
                    + ', '.join(layer.name for layer in config.layers)
                    + '\n'
                    + json.dumps(config.values, indent=2)
                )
            )
            sys.stdout.write(output + '\n')
        elif args.command == 'profile':
            options = split(remaining)
            if options.rest:
                raise Refusal('profile accepts grant flags only')
            sys.stdout.write(json.dumps(preview(config, platform, options), indent=2) + '\n')
        elif args.command == 'validate':
            validate(config)
            sys.stdout.write('configuration: valid\n')
        elif args.command == 'doctor':
            doctor(config, platform)
        return 0
    except (Refusal, OSError, ValueError) as error:
        message(str(error))
        return 2
