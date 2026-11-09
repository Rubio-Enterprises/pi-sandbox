import json

import pytest

from tests.support import file


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_cli_selection_beats_environment_and_never_reaches_pi(fixture, os_name):
    fixture.sandbox_profile('alternate', '[pi]\nexclude_tools=["alternate_*"]')
    result = fixture.run(os_name, '--profile', 'user', '-p', 'hello')
    assert result.returncode == 0, result.stderr
    pi = json.loads((fixture.out / 'pi.json').read_text())['argv']
    assert '--profile' not in pi and pi[-2:] == ['-p', 'hello']
    assert 'alternate_*' not in pi[pi.index('--exclude-tools') + 1]
    assert 'DOCKER_HOST' not in fixture.capture()['env']


@pytest.mark.parametrize('target', ['protected', 'policy', 'git', 'windows'])
def test_native_linux_write_grants_cannot_overlap_engine_floors(fixture, target):
    path = {
        'protected': fixture.paths.home / 'protected',
        'policy': fixture.paths.root,
        'git': fixture.repo / '.git',
        'windows': '/mnt/D/project',
    }[target]
    fixture.sandbox_profile('unsafe', profile={'filesystem': {'allow': [str(path)]}})
    result = fixture.run('linux', wsl=target == 'windows')
    assert result.returncode == 2 and 'refusing' in result.stderr
    assert not (fixture.out / 'capture.json').exists()


def test_prune_does_not_delete_named_user_sandbox_profiles(fixture):
    import os
    import time

    path = file(fixture.paths.nono / 'profiles/pi-sandbox-personal.json', '{}')
    old = time.time() - 48 * 3600
    os.utime(path, (old, old))
    result = fixture.ctl('prune')
    assert result.returncode == 0, result.stderr
    assert path.is_file()


def test_native_isolation_values_win_over_selected_set_vars(fixture):
    fixture.sandbox_profile(
        'redirect',
        profile={
            'platform_overrides': {
                'macos': {
                    'environment': {
                        'set_vars': {
                            'GIT_CONFIG_GLOBAL': '/host/gitconfig',
                            'DOCKER_CONFIG': '/host/docker',
                        }
                    }
                }
            }
        },
    )
    result = fixture.run()
    assert result.returncode == 0, result.stderr
    env = fixture.capture()['env']
    assert env['GIT_CONFIG_GLOBAL'] != '/host/gitconfig'
    assert env['DOCKER_CONFIG'] != '/host/docker'


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_related_service_auth_requires_an_explicit_native_grant(fixture, os_name):
    auth = file(
        fixture.paths.state / 'browser-service/extension/auth.json', '{"token":"synthetic"}'
    )
    legacy = file(fixture.paths.home / '.opentabs/extension/auth.json', '{"token":"synthetic"}')
    result = fixture.run(os_name)
    assert result.returncode == 0, result.stderr
    files = fixture.capture()['profile']['filesystem']['read_file']
    assert str(auth) not in files and str(legacy) not in files

    fixture.sandbox_profile('service-auth', profile={'filesystem': {'read_file': [str(auth)]}})
    result = fixture.run(os_name)
    assert result.returncode == 0, result.stderr
    fs = fixture.capture()['profile']['filesystem']
    assert str(auth) in fs['read_file']
    assert str(auth) not in fs['allow_file'] and str(legacy) not in fs['read_file']
