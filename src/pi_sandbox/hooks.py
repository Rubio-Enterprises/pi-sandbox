import json
import subprocess
from pathlib import Path

from pi_sandbox import Refusal
from pi_sandbox.config import Config, strings, table
from pi_sandbox.platform import Platform


def run(
    config: Config, platform: Platform, workdir: Path, launch: Path
) -> tuple[list[str], list[str]]:
    rules: list[str] = []
    notices: list[str] = []
    payload = json.dumps(
        dict(
            schema=1,
            os=platform.os,
            workdir=str(workdir),
            launch_dir=str(launch),
            home=str(config.paths.home),
        )
    )
    for hook in config.hooks:
        if platform.os not in hook.get('platforms', ['macos', 'linux']):
            continue
        name = hook['name']
        error = ''
        try:
            result = subprocess.run(
                hook['command'],
                input=payload,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                cwd=workdir,
            )
            error = result.stderr
            if result.returncode:
                raise ValueError('non-zero hook exit')
            if not result.stdout.strip():
                continue
            output = table(json.loads(result.stdout), 'hook output')
            if output.keys() - {'seatbelt_rules', 'notice'}:
                raise ValueError('unknown hook output keys')
            added = strings(output.get('seatbelt_rules', []), 'hook seatbelt_rules')
            if added and (platform.os != 'macos' or any(not r.startswith('(') for r in added)):
                raise ValueError('invalid Seatbelt rules for platform')
            rules += added
            notices += strings(output.get('notice', []), 'hook notice')
        except (OSError, ValueError, Refusal, subprocess.TimeoutExpired) as failure:
            raise Refusal(
                f'hook {name} refused the launch' + ('\n' + error.rstrip('\n') if error else '')
            ) from failure
    return rules, notices
