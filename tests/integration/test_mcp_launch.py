import json
import shlex

import pytest

from tests.support import file


@pytest.mark.parametrize('os_name', ['macos', 'linux'])
def test_mcp_wrapper_and_controller_preview(fixture, os_name):
    fixture.layer(
        '95-mcp',
        f"""schema = 1
[pi.mcp.example]
hide_tools = ["delete-*", "write.file", "api/remove", "*"]
names = ["local-api", "local_api"]
[platform.{os_name}.pi.mcp.example]
hide_tools = ["platform-*"]
""",
    )
    file(fixture.paths.home / '.pi/agent/mcp.json', 'not JSON')
    file(fixture.repo / '.pi/mcp.json', 'not JSON')
    declared = {
        'example': {
            'hide_tools': ['delete-*', 'write.file', 'api/remove', '*', 'platform-*'],
            'names': ['local-api', 'local_api'],
        }
    }
    rendered = [
        'mcp__example__delete_*',
        'unsafe_*',
        'mcp__example__write_file',
        'mcp__example__api_remove',
        'mcp__example__*',
        'mcp__example__platform_*',
        'mcp__local_api__delete_*',
        'mcp__local_api__write_file',
        'mcp__local_api__api_remove',
        'mcp__local_api__*',
        'mcp__local_api__platform_*',
    ]
    result = fixture.ctl('config', '--json', os_name=os_name)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['pi']['mcp'] == declared
    assert value['pi']['exclude_tools'] == ['mcp__example__delete_*', 'unsafe_*']
    assert value['pi']['rendered_exclude_tools'] == rendered
    arguments = ['--exclude-tools=mcp__local_api__write_file,cli_*', '-xt', 'cli_*,other']
    result = fixture.run(os_name, *arguments)
    assert result.returncode == 0, result.stderr
    argv = json.loads((fixture.out / 'pi.json').read_text())['argv']
    assert argv.count('--exclude-tools') == 1
    expected = ','.join([*rendered, 'cli_*', 'other'])
    assert argv[argv.index('--exclude-tools') + 1] == expected
    assert 'MCP configuration' not in result.stdout + result.stderr
    directory = fixture.repo if os_name == 'macos' else fixture.linked
    result = fixture.ctl('profile', '--workdir=' + str(directory), *arguments, os_name=os_name)
    assert result.returncode == 0, result.stderr
    assert 'wrapper' not in json.loads(result.stdout)
    assert result.stderr.startswith('Pi wrapper (preview):\n')
    script = result.stderr
    argv = shlex.split(script.splitlines()[-1])
    assert argv.count('--exclude-tools') == 1
    assert argv[argv.index('--exclude-tools') + 1] == expected
    assert not list(fixture.paths.launch_state.glob('launch.*'))


@pytest.mark.parametrize(
    'logical,aliases,configured,warning',
    [
        ('example', [], 'example', False),
        ('example', ['local'], 'local', False),
        ('example-api', [], 'example_api', False),
        ('example_api', [], 'example-api', False),
        ('example', ['local-api'], 'local_api', False),
        ('example', ['local'], 'other', True),
        ('example', [], 'EXAMPLE', True),
    ],
)
def test_doctor_server_name_matches(fixture, logical, aliases, configured, warning):
    fixture.layer(
        '95-mcp',
        f'schema = 1\n[pi.mcp.{logical}]\nhide_tools = ["delete-*"]\n'
        f'names = {json.dumps(aliases)}\n',
    )
    source = file(
        fixture.paths.home / '.pi/agent/mcp.json',
        json.dumps({'mcpServers': {configured: {'command': 'does-not-run'}}}),
    )
    before = source.read_bytes()
    result = fixture.ctl('doctor')
    assert result.returncode == 0, result.stderr
    expected = (
        f'pi.mcp.{logical}: no configured MCP server named {", ".join([logical, *aliases])}; '
        'its hidden tools match nothing (add names = [...] in your layer)'
    )
    assert (expected in result.stdout) is warning
    assert 'servers registered by extensions are not visible to this check' in result.stdout
    assert source.read_bytes() == before


@pytest.mark.parametrize('form', ['separate', 'equals'])
def test_doctor_project_overrides_and_agent_dir(fixture, form):
    fixture.layer('95-mcp', 'schema = 1\n[pi.mcp.example]\nhide_tools = ["delete_*"]\n')
    default = file(fixture.paths.home / '.pi/agent/mcp.json', 'invalid and ignored')
    agent_dir = fixture.paths.home / 'custom-agent'
    fixture.env['PI_CODING_AGENT_DIR'] = str(agent_dir)
    user = file(agent_dir / 'mcp.json', json.dumps({'mcpServers': {'other': {'command': 'user'}}}))
    project = file(
        fixture.repo / '.pi/mcp.json',
        json.dumps(
            {'mcpServers': {'other': {'enabled': False}, 'example': {'command': 'project'}}}
        ),
    )
    originals = [path.read_bytes() for path in (default, user, project)]
    result = fixture.ctl('doctor')
    assert result.returncode == 0, result.stderr
    assert 'pi.mcp.example: no configured MCP server' in result.stdout
    assert 'cannot read MCP configuration' not in result.stdout
    args = ['--workdir', str(fixture.repo)] if form == 'separate' else [f'--workdir={fixture.repo}']
    result = fixture.ctl('doctor', *args)
    assert result.returncode == 0, result.stderr
    assert 'pi.mcp.example: no configured MCP server' not in result.stdout
    assert 'cannot read MCP configuration' not in result.stdout
    assert [path.read_bytes() for path in (default, user, project)] == originals


@pytest.mark.parametrize(
    'content',
    [
        None,
        'not JSON',
        '[]',
        '{"mcpServers": []}',
        '{"mcpServers": {"bad.name": {}}}',
        '{"mcpServers": {"example": null}}',
        b'\xff',
    ],
)
def test_doctor_missing_or_invalid_mcp_is_advisory(fixture, content):
    fixture.layer('95-mcp', 'schema = 1\n[pi.mcp.example]\nhide_tools = ["delete_*"]\n')
    source = fixture.paths.home / '.pi/agent/mcp.json'
    if isinstance(content, bytes):
        source.parent.mkdir(parents=True)
        source.write_bytes(content)
    elif content is not None:
        file(source, content)
    result = fixture.ctl('doctor')
    assert result.returncode == 0, result.stderr
    assert f'warning: {source}: cannot read MCP configuration:' in result.stdout
    assert 'pi.mcp.example: no configured MCP server named example;' in result.stdout
    assert 'configuration: valid' in result.stdout
    assert source.exists() is (content is not None)


def test_doctor_missing_project_file_is_advisory(fixture):
    file(fixture.paths.home / '.pi/agent/mcp.json', '{"mcpServers": {}}')
    result = fixture.ctl('doctor', '--workdir', str(fixture.repo))
    assert result.returncode == 0, result.stderr
    assert (
        f'warning: {fixture.repo / ".pi/mcp.json"}: cannot read MCP configuration' in result.stdout
    )


def test_doctor_mcp_warning_does_not_mask_failure(fixture):
    fixture.env['TEST_NONO_VERSION'] = 'nono 0.1.0'
    result = fixture.ctl('doctor')
    assert result.returncode == 2
    assert 'cannot read MCP configuration' in result.stdout
    assert 'nono >= ' in result.stderr


def test_doctor_rejects_non_directory_workdir(fixture):
    result = fixture.ctl('doctor', '--workdir', str(fixture.root / 'absent'))
    assert result.returncode == 2 and 'workdir is not a directory' in result.stderr
