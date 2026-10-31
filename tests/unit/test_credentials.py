import json
import subprocess

import pytest

from pi_sandbox.credentials import Credentials, write_files


@pytest.mark.parametrize('token', ['', 'github_fixture'])
@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_isolated_config_files_parse_exactly(paths, token, os_name):
    launch = paths.state / 'launch'
    write_files(launch, Credentials(github=token), os_name)
    helper = subprocess.run(
        [
            'git',
            'config',
            '--file',
            str(launch / 'gitconfig'),
            '--get',
            'credential.https://github.com.helper',
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip('\n')
    assert helper == ('!gh auth git-credential' if token else '!f() { echo quit=1; }; f')
    text = (launch / 'gitconfig').read_text()
    assert text == (
        '[credential]\n\thelper = \n[credential "https://github.com"]\n\thelper = '
        + ('!gh auth git-credential' if token else '"!f() { echo quit=1; }; f"')
        + '\n[commit]\n\tgpgsign = false\n[user]\n\tuseConfigOnly = true\n'
    )
    docker = json.loads((launch / 'docker/config.json').read_text())
    assert docker['auths'] == {'https://index.docker.io/v1/': {}}
    plugins = docker['cliPluginsExtraDirs']
    assert plugins == (
        ['/opt/homebrew/lib/docker/cli-plugins', '/usr/local/lib/docker/cli-plugins']
        if os_name == 'macos'
        else [
            '/usr/local/lib/docker/cli-plugins',
            '/usr/libexec/docker/cli-plugins',
            '/usr/lib/docker/cli-plugins',
        ]
    )
