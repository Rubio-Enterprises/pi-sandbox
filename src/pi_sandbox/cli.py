import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pi_sandbox import Refusal
from pi_sandbox.platform import Platform


@dataclass
class Arguments:
    workdir: str = field(default_factory=os.getcwd)
    scratch: str = ''
    rw: list[str] = field(default_factory=list[str])
    ro: list[str] = field(default_factory=list[str])
    patterns: list[str] = field(default_factory=list[str])
    rest: list[str] = field(default_factory=list[str])


def split(args: list[str]) -> Arguments:
    result = Arguments()
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == '--':
            result.rest += [arg, *args[index:]]
            break
        if arg.startswith('--workdir='):
            result.workdir = arg.split('=', 1)[1]
        elif arg.startswith('--scratch='):
            result.scratch = arg.split('=', 1)[1]
        elif arg.split('=', 1)[0] in ('--add-dirs', '--add-dirs-ro', '--exclude-tools', '-xt'):
            flag, equal, value = arg.partition('=')
            if not equal:
                if index == len(args):
                    if flag in ('--exclude-tools', '-xt'):
                        continue
                    raise Refusal(f'{flag} needs a path')
                value = args[index]
                index += 1
            if flag in ('--exclude-tools', '-xt'):
                result.patterns += [v for v in value.split(',') if v]
            elif flag == '--add-dirs':
                result.rw += [v for v in value.split(':') if v]
            else:
                result.ro += [v for v in value.split(':') if v]
        else:
            result.rest.append(arg)
    if result.scratch and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', result.scratch):
        raise Refusal('--scratch requires one effort name (letters, digits, hyphens, underscores)')
    return result


def workdir(value: str, home: Path) -> Path:
    if value == '~' or value.startswith('~/'):
        value = str(home) + value[1:]
    path = Path(value)
    if not path.is_dir():
        raise Refusal(f'workdir is not a directory: {path}')
    path = path.resolve()
    if home.resolve().is_relative_to(path):
        raise Refusal(f'refusing workdir {path}: it contains $HOME; choose a project')
    return path


def message(value: str) -> None:
    sys.stderr.write('pi-sandbox: ' + value + '\n')


def main(argv: list[str] | None = None, *, platform: Platform | None = None) -> int:
    from pi_sandbox.launch import InterruptedError, launch

    try:
        if os.environ.get('NONO_CAP_FILE'):
            raise Refusal('already inside a sandbox')
        return launch(
            split(list(sys.argv[1:] if argv is None else argv)), platform or Platform.detect()
        )
    except InterruptedError as error:
        return 128 + error.signum
    except (Refusal, OSError, ValueError) as error:
        message(str(error))
        return 2
