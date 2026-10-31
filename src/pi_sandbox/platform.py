import platform as host
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pi_sandbox import Refusal

OS = Literal['macos', 'linux']


@dataclass(frozen=True)
class Platform:
    os: OS
    release: str = ''
    wsl: bool = False

    @classmethod
    def detect(cls) -> Platform:
        system = host.system()
        if system == 'Darwin':
            return cls('macos')
        if system == 'Linux':
            version = Path('/proc/version')
            return cls(
                'linux',
                host.release(),
                version.exists() and 'microsoft' in version.read_text().lower(),
            )
        raise Refusal(f'unsupported platform: {system}')

    def preflight(self) -> None:
        if self.os != 'linux':
            return
        version = re.match(r'^(\d+)\.(\d+)', self.release)
        if version is None or tuple(map(int, version.groups())) < (6, 12):
            raise Refusal(
                'Linux >= 6.12 with Landlock ABI >= 6 required; '
                'on WSL run wsl --update (WSL >= 2.7.8)'
            )
