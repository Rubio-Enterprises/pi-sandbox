import json

from tests.support import executable, file


def add_sidecar(fixture, content):
    with (fixture.paths.root / 'profiles/host.toml').open('a') as stream:
        stream.write(content)


def marker_hook(fixture):
    marker = fixture.out / 'hook-ran'
    hook = executable(fixture.bins / 'marker', f'#!/bin/sh\ntouch {marker}\n')
    add_sidecar(fixture, f'[[hooks]]\nname = "marker"\ncommand = [{json.dumps(str(hook))}]\n')
    return marker


def test_launch_loads_entries_in_order_before_pi_arguments(fixture):
    first = file(fixture.paths.home / 'ext/guard.ts')
    second = file(fixture.paths.home / 'ext/transport.ts')
    add_sidecar(fixture, '[pi]\nextensions = ["~/ext/guard.ts", "$HOME/ext/transport.ts"]\n')
    result = fixture.run('macos', '-e', '/user.ts')
    assert result.returncode == 0, result.stderr
    argv = json.loads((fixture.out / 'pi.json').read_text())['argv']
    assert argv[argv.index('-e') :][:4] == ['-e', str(first), '-e', str(second)]
    assert argv[-2:] == ['-e', '/user.ts']


def test_missing_entry_refuses_before_hooks_and_pi(fixture):
    marker = marker_hook(fixture)
    missing = fixture.paths.home / 'ext/missing.ts'
    add_sidecar(fixture, '[pi]\nextensions = ["~/ext/missing.ts"]\n')
    result = fixture.run('macos')
    assert result.returncode == 2
    assert f'pi.extensions: cannot read {missing}' in result.stderr
    assert not marker.exists()
    assert not (fixture.out / 'pi.json').exists()
    assert not list(fixture.paths.launch_state.glob('launch.*'))


def test_relative_entry_refuses_naming_it(fixture):
    marker = marker_hook(fixture)
    add_sidecar(fixture, '[pi]\nextensions = ["ext/guard.ts"]\n')
    result = fixture.run('macos')
    assert result.returncode == 2
    assert "'ext/guard.ts'" in result.stderr
    assert not marker.exists()
    assert not (fixture.out / 'pi.json').exists()


def test_controller_validate_and_config_report_entries(fixture):
    entry = file(fixture.paths.home / 'ext/guard.ts')
    add_sidecar(fixture, '[pi]\nextensions = ["~/ext/guard.ts"]\n')
    result = fixture.ctl('config', '--json')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['sidecar']['pi']['extensions'] == [str(entry)]
    entry.unlink()
    result = fixture.ctl('validate')
    assert result.returncode == 2
    assert f'pi.extensions: cannot read {entry}' in result.stderr
