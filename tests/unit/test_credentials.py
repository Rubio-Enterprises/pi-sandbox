import base64
import json
import subprocess
from pathlib import Path

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


@pytest.mark.parametrize('build', ['', 'build_fixture'])
def test_ghcr_build_token_writes_buildx_alternative_config(paths, build):
    launch = paths.state / 'launch'
    write_files(launch, Credentials(ghcr='ghcr_fixture', ghcr_build=build), 'macos')
    docker = json.loads((launch / 'docker/config.json').read_text())
    assert docker['auths']['ghcr.io'] == {
        'auth': base64.b64encode(b'x-access-token:ghcr_fixture').decode()
    }
    alternative = launch / 'docker/buildx/config/ghcr.io/config.json'
    if not build:
        assert not (launch / 'docker/buildx').exists()
        return
    assert json.loads(alternative.read_text()) == {
        'auths': {'ghcr.io': {'auth': base64.b64encode(b'x-access-token:build_fixture').decode()}}
    }
    assert alternative.stat().st_mode & 0o777 == 0o600
    assert alternative.parent.stat().st_mode & 0o777 == 0o700


def test_default_credential_repositories_use_the_launch_default_auth(paths):
    launch = paths.state / 'launch'
    credentials = Credentials(ghcr='ghcr_fixture', ghcr_build='build_fixture')
    repositories = ['ghcr.io/example/base', 'ghcr.io/example/nested/image']
    write_files(launch, credentials, 'linux', repositories)
    config = launch / 'docker/buildx/config'
    expected = {
        'auths': {'ghcr.io': {'auth': base64.b64encode(b'x-access-token:ghcr_fixture').decode()}}
    }
    for repository in repositories:
        file = config / repository / 'config.json'
        assert json.loads(file.read_text()) == expected
        assert file.stat().st_mode & 0o777 == 0o600
        for directory in [file.parent, *file.parent.parents][: len(Path(repository).parts)]:
            assert directory.stat().st_mode & 0o777 == 0o700
    registry_wide = json.loads((config / 'ghcr.io/config.json').read_text())
    assert (
        registry_wide['auths']['ghcr.io']['auth']
        == base64.b64encode(b'x-access-token:build_fixture').decode()
    )
    assert 'build_fixture' not in (config / repositories[0] / 'config.json').read_text()


@pytest.mark.parametrize('build', ['', 'build_fixture'])
def test_default_credential_repositories_without_a_work_token_stay_anonymous(paths, build):
    launch = paths.state / 'launch'
    write_files(launch, Credentials(ghcr_build=build), 'macos', ['ghcr.io/example/base'])
    file = launch / 'docker/buildx/config/ghcr.io/example/base/config.json'
    assert json.loads(file.read_text()) == {'auths': {}}
    assert 'credsStore' not in file.read_text()


def test_default_credential_repositories_for_other_registries_are_anonymous(paths):
    launch = paths.state / 'launch'
    write_files(
        launch, Credentials(ghcr='ghcr_fixture'), 'macos', ['registry.example:5000/team/app']
    )
    file = launch / 'docker/buildx/config/registry.example:5000/team/app/config.json'
    assert json.loads(file.read_text()) == {'auths': {}}


def test_removed_repositories_leave_no_override_in_a_new_launch(paths):
    first, second = paths.state / 'one', paths.state / 'two'
    write_files(first, Credentials(ghcr='ghcr_fixture'), 'macos', ['ghcr.io/example/base'])
    write_files(second, Credentials(ghcr='ghcr_fixture'), 'macos', [])
    assert (first / 'docker/buildx').exists()
    assert not (second / 'docker/buildx').exists()
