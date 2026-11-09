import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pi_sandbox.config import Config
from pi_sandbox.paths import Paths

REPO = Path(__file__).resolve().parents[1]
HOST_PATH = os.environ['PATH']


def file(path, content='fixture'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def executable(path, content):
    file(path, content)
    path.chmod(0o755)
    return path


@pytest.fixture
def paths(tmp_path):
    home = tmp_path / 'h'
    home.mkdir()
    return Paths.from_env({'HOME': str(home)})


@pytest.fixture
def config(paths):
    return Config(paths)


@dataclass
class Fixture:
    root: Path
    paths: Paths
    env: dict
    repo: Path
    linked: Path
    bins: Path
    out: Path

    def git(self, *args, cwd=None):
        return subprocess.run(
            ['git', '-C', str(cwd or self.repo), *args],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def sandbox_profile(self, name, content='', profile=None):
        value = dict(profile or {})
        value.setdefault('meta', {'name': name})
        if name != 'org':
            value.setdefault('extends', self.env.get('PI_SANDBOX_PROFILE', 'org'))
        file(self.paths.nono / 'profiles' / (name + '.json'), json.dumps(value))
        sidecar = file(self.paths.root / 'profiles' / (name + '.toml'), content)
        self.env['PI_SANDBOX_PROFILE'] = name
        return sidecar

    def command(self, os_name='macos', *args, release='6.12.0', wsl=False):
        return [
            sys.executable,
            str(REPO / 'tests/entry.py'),
            'launch',
            os_name,
            release,
            'yes' if wsl else 'no',
            f'--workdir={self.linked if os_name == "linux" else self.repo}',
            *args,
        ]

    def run(self, os_name='macos', *args, extra=None, **kwargs):
        (self.out / 'capture.json').unlink(missing_ok=True)
        return subprocess.run(
            self.command(os_name, *args, **kwargs),
            env=self.env | {'TEST_OS': os_name} | (extra or {}),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def capture(self):
        return json.loads((self.out / 'capture.json').read_text())

    def ctl(self, *args, os_name='macos', release='6.12.0', wsl=False):
        return subprocess.run(
            [
                sys.executable,
                str(REPO / 'tests/entry.py'),
                'ctl',
                os_name,
                release,
                'yes' if wsl else 'no',
                *args,
            ],
            env=self.env | {'TEST_OS': os_name},
            capture_output=True,
            text=True,
            timeout=30,
        )


def make_fixture(root):
    home = root / 'h'
    home.mkdir(parents=True)
    paths = Paths.from_env({'HOME': str(home)})
    bins, out, repo = root / 'bin', root / 'out', root / 'repo'
    for directory in (bins, out, repo):
        directory.mkdir()
    env = {
        'HOME': str(home),
        'PATH': str(bins) + ':' + HOST_PATH,
        'GIT_CONFIG_GLOBAL': str(home / '.gitconfig'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_COUNT': '0',
        'TEST_OUT': str(out),
        'NONO_NO_UPDATE_CHECK': '1',
        'USER': 'fixture',
        'LOGNAME': 'fixture',
        'SHELL': '/bin/sh',
        'TERM': 'xterm',
        'AGENT_BROWSER_SKILLS_DIR': str(home / '.local/share/agent-browser/skills'),
    }
    fixture = Fixture(root, paths, env, repo, root / 'linked', bins, out)
    file(home / '.gitconfig', '[user]\n name = Fixture User\n email = user@example.invalid\n')
    fixture.git('init', '-q')
    fixture.git('commit', '-q', '--allow-empty', '-m', 'fixture')
    fixture.git('remote', 'add', 'origin', 'https://github.com/example/app.git')
    fixture.git('worktree', 'add', '-q', '-b', 'feature', str(fixture.linked))
    file(
        paths.nono / 'packages/lockfile.json',
        json.dumps(
            {
                'packages': {
                    'nolabs-ai/pi': {
                        'version': '0.2.2',
                        'artifacts': {
                            'profiles/pi.json': {
                                'sha256': hashlib.sha256(b'{}').hexdigest(),
                                'type': 'profile',
                            }
                        },
                    }
                }
            }
        ),
    )
    file(paths.nono / 'packages/nolabs-ai/pi/profiles/pi.json', '{}')
    file(paths.nono / 'packages/nolabs-ai/pi/package.json', '{"pi":{"skills":["guides","absent"]}}')
    (paths.nono / 'packages/nolabs-ai/pi/guides').mkdir()
    for name in (
        '.aws',
        '.azure',
        '.azd',
        '.config/gcloud',
        '.config/op',
        '.config/1Password',
        '.local/share/agent-browser/skills',
        '.config/worktrunk',
        '.config/herdr',
        'context',
        'protected',
        'Library/Keychains',
    ):
        file(home / name / 'fixture')
    file(home / '.config/herdr/config.toml')
    fixture.sandbox_profile(
        'org',
        """integrations = [
  "credential-stores", "onepassword", "cloud-credentials", "keychain", "agent-mcp"
]
[credentials.env.first]
file = "first.token"
env = "EXAMPLE_FIRST_TOKEN"
set = { EXAMPLE_URL = "https://example.invalid" }
missing = "First off: no token at {path}"
notice = "- First is reachable read-only; it refuses writes."
[credentials.env.second]
file = "second.token"
env = "EXAMPLE_SECOND_TOKEN"
set = { EXAMPLE_URL = "https://example.invalid" }
missing = "Second off: no token at {path}"
notice = "- Second is reachable read-only; it refuses writes."
[credentials.env.chat]
file = "chat.token"
env = "CHAT_TOKEN"
missing = "Chat off: no token at {path}"
notice = "- Chat is reachable read-only; it refuses writes."
[grants.project_state_dirs]
"https://github.com/example/app" = ["app-harness"]
[pi]
exclude_tools = ["mcp__example__delete_*"]
""",
        {'environment': {'deny_vars': ['EXAMPLE_ADMIN_TOKEN', 'EXAMPLE_*', 'CHAT_*']}},
    )
    fixture.sandbox_profile(
        'user',
        """[grants]
protected_checkouts = ["~/protected"]
[workspace]
scratch_root = "~/scratch"
worktree_root = "~/.local/share/worktrees/{repo}"
[pi]
exclude_tools = ["mcp__example__delete_*", "unsafe_*"]
[notice]
lines = ["- Generic user policy."]
[platform.macos.grants]
runtime_dirs = ["~/runtime/mac"]
[platform.linux.grants]
runtime_dirs = ["~/runtime/linux"]
""",
        {
            'filesystem': {
                'deny': ['$HOME/.config/herdr'],
                'read': ['~/context', '$XDG_DATA_HOME/agent-browser/skills'],
                'read_file': ['~/instructions.md', '~/.config/herdr/config.toml'],
                'bypass_protection': ['~/.config/herdr/config.toml'],
            }
        },
    )
    fixture.sandbox_profile(
        'host',
        """[credentials.env.first.set]
EXAMPLE_EMAIL = "fixture@example.invalid"
[credentials.env.second.set]
EXAMPLE_EMAIL = "fixture@example.invalid"
[credentials.env.inference]
command = "printf inference_fixture"
env = "PI_SANDBOX_INFERENCE_API_KEY"
missing = "local inference key unavailable; local models cannot authenticate"
[docker]
socket = "~/.d/s"
[platform.linux.docker]
socket = "/var/run/docker.sock"
""",
        {
            'platform_overrides': {
                'macos': {
                    'unsafe_macos_seatbelt_rules': [
                        '(deny network-outbound (remote ip "*:8080"))',
                        '(allow network-outbound (remote ip "localhost:8080"))',
                    ]
                }
            }
        },
    )
    output = json.dumps(
        {
            'seatbelt_rules': ['(deny network-outbound (remote ip "localhost:8081"))'],
            'notice': ['- Inventory policy.'],
        }
    )
    hook = executable(bins / 'inventory', f'#!{sys.executable}\nprint({output!r})\n')
    host = paths.root / 'profiles/host.toml'
    with host.open('a') as stream:
        stream.write(
            '[[hooks]]\nname = "inventory"\ncommand = ['
            + json.dumps(str(hook))
            + ']\nplatforms = ["macos"]\n'
        )
    for name in (
        'pi-sandbox-github',
        'pi-sandbox-ghcr',
        'pi-sandbox-ghcr-build',
        'first',
        'second',
        'chat',
        'op-service-account',
    ):
        file(paths.root / 'credentials' / (name + '.token'), name + '_fixture\n\n')
    for name in ('nono', 'pi', 'tmux', 'agent-browser'):
        executable(
            bins / name,
            f'#!{sys.executable}\n' + (REPO / 'tests/stubs' / (name + '.py')).read_text(),
        )
    return fixture


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    result = make_fixture(tmp_path)
    monkeypatch.setenv('PI_SANDBOX_PROFILE', 'host')
    return result
