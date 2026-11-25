import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from pi_sandbox import Refusal, wrapper
from pi_sandbox.cli import Arguments
from pi_sandbox.config import resolve, validate_section
from pi_sandbox.launch import pi_extensions, render_wrapper
from pi_sandbox.paths import Paths
from pi_sandbox.platform import Platform
from tests.support import executable, file


def sidecar_chain(paths, sidecars):
    parent = None
    for name, content in sidecars:
        profile = {'meta': {'name': name}} | ({'extends': parent} if parent else {})
        file(paths.nono / 'profiles' / (name + '.json'), json.dumps(profile))
        file(paths.root / 'profiles' / (name + '.toml'), content)
        parent = name
    return parent


@pytest.mark.parametrize(
    'value,expected',
    [
        ('~/ext/a.ts', 'h/ext/a.ts'),
        ('$HOME/ext/a.ts', 'h/ext/a.ts'),
        ('${HOME}/ext/../ext/a.ts', 'h/ext/a.ts'),
        ('$XDG_CONFIG_HOME/ext/a.ts', 'h/.config/ext/a.ts'),
        ('$XDG_DATA_HOME/ext/a.ts', 'h/.local/share/ext/a.ts'),
        ('$XDG_STATE_HOME/ext/a.ts', 'h/.local/state/ext/a.ts'),
        ('$XDG_CACHE_HOME/ext/a.ts', 'h/.cache/ext/a.ts'),
        ('/opt/ext/a.ts', '/opt/ext/a.ts'),
    ],
)
def test_entries_expand_like_other_sidecar_paths(paths, value, expected):
    result = validate_section({'pi': {'extensions': [value]}}, paths, 'fixture', root=True)
    assert result['pi']['extensions'] == [str(paths.home.parent / expected)]


@pytest.mark.parametrize(
    'value', [['ext/a.ts'], ['./a.ts'], ['$UNKNOWN/a.ts'], ['~/a\nb.ts'], '~/a.ts', [1]]
)
def test_relative_or_malformed_entries_refuse(paths, value):
    with pytest.raises(Refusal):
        validate_section({'pi': {'extensions': value}}, paths, 'fixture', root=True)


def test_relative_entry_refusal_names_it(paths):
    with pytest.raises(Refusal, match=re.escape("'ext/a.ts'")):
        validate_section({'pi': {'extensions': ['ext/a.ts']}}, paths, 'fixture', root=True)


def test_entries_merge_across_chain_in_order_without_duplicates(paths):
    selected = sidecar_chain(
        paths,
        [
            ('base', '[pi]\nextensions = ["~/a.ts", "~/b.ts"]\n'),
            (
                'middle',
                '[pi]\nextensions = ["$HOME/b.ts", "~/c.ts"]\n'
                '[platform.macos.pi]\nextensions = ["~/mac.ts", "~/a.ts"]\n'
                '[platform.linux.pi]\nextensions = ["~/linux.ts"]\n',
            ),
            ('top', '[pi]\nextensions = ["~/d.ts", "~/c.ts"]\n'),
        ],
    )
    home = paths.home
    macos = resolve(paths, 'macos', selected=selected)
    assert macos.entries('pi', 'extensions') == [
        str(home / name) for name in ('a.ts', 'b.ts', 'c.ts', 'mac.ts', 'd.ts')
    ]
    linux = resolve(paths, 'linux', selected=selected)
    assert linux.entries('pi', 'extensions') == [
        str(home / name) for name in ('a.ts', 'b.ts', 'c.ts', 'linux.ts', 'd.ts')
    ]


def test_profile_without_entries_has_none(paths):
    selected = sidecar_chain(paths, [('only', '[pi]\nexclude_tools = ["x"]\n')])
    config = resolve(paths, 'macos', selected=selected)
    assert pi_extensions(config) == []


def configured(paths: Paths, *entries: Path | str):
    content = '[pi]\nextensions = ' + json.dumps([str(entry) for entry in entries]) + '\n'
    return resolve(paths, 'macos', selected=sidecar_chain(paths, [('only', content)]))


def test_readable_files_pass_in_order(paths):
    second = file(paths.home / 'ext/second.ts')
    first = file(paths.home / 'ext with space/first.ts')
    assert pi_extensions(configured(paths, second, first)) == [str(second), str(first)]


def test_missing_entry_refuses_naming_it(paths):
    present = file(paths.home / 'ext/present.ts')
    missing = paths.home / 'ext/missing.ts'
    with pytest.raises(Refusal, match=re.escape(f'pi.extensions: cannot read {missing}')):
        pi_extensions(configured(paths, present, missing))


def test_directory_entry_refuses_naming_it(paths):
    directory = paths.home / 'ext'
    directory.mkdir()
    with pytest.raises(Refusal, match=re.escape(f'not a readable regular file: {directory}')):
        pi_extensions(configured(paths, directory))


def test_fifo_entry_refuses(paths):
    fifo = paths.home / 'ext.ts'
    os.mkfifo(fifo)
    with pytest.raises(Refusal, match=re.escape(f'not a readable regular file: {fifo}')):
        pi_extensions(configured(paths, fifo))


@pytest.mark.skipif(os.geteuid() == 0, reason='root reads any file')
def test_unreadable_entry_refuses_naming_it(paths):
    locked = file(paths.home / 'ext/locked.ts')
    locked.chmod(0o200)
    with pytest.raises(Refusal, match=re.escape(f'not a readable regular file: {locked}')):
        pi_extensions(configured(paths, locked))


def test_entry_removed_after_resolution_refuses_at_render(paths):
    entry = file(paths.home / 'ext/gone.ts')
    config = configured(paths, entry)
    entry.unlink()
    with pytest.raises(Refusal, match=re.escape(str(entry))):
        render_wrapper(config, Platform('macos'), Arguments(), '/bin/pi', paths.home / 'launch')


def test_wrapper_passes_one_flag_per_entry_after_skills():
    script = wrapper.render(
        '/bin/pi',
        Path('/launch'),
        ['one'],
        ['/skills'],
        op=False,
        extensions=['/ext/b.ts', '/ext with space/a.ts'],
    )
    assert (
        "exec /bin/pi --exclude-tools one --skill /skills -e /ext/b.ts -e '/ext with space/a.ts'"
        ' --append-system-prompt'
    ) in script
    assert script.rstrip().endswith('"$@"')


def test_wrapper_without_entries_has_no_extension_flag():
    script = wrapper.render('/bin/pi', Path('/launch'), [], [], op=False)
    assert ' -e ' not in script


def test_rendered_wrapper_execs_pi_with_entries_before_user_arguments(paths):
    first = file(paths.home / 'ext/guard.ts')
    second = file(paths.home / 'ext/transport.ts')
    config = configured(paths, first, second)
    launch = paths.home / 'launch'
    launch.mkdir()
    (launch / 'notice.md').write_text('notice')
    pi = executable(paths.home / 'bin/pi', '#!/bin/sh\nprintf "%s\\n" "$@"\n')
    wrapper.write(
        launch,
        render_wrapper(config, Platform('macos'), Arguments(patterns=['x']), str(pi), launch),
    )
    result = subprocess.run(
        [str(launch / 'bin/pi'), '-e', '/user.ts', '-p', 'hi'],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.splitlines() == [
        '--exclude-tools',
        'x',
        '-e',
        str(first),
        '-e',
        str(second),
        '--append-system-prompt',
        'notice',
        '-e',
        '/user.ts',
        '-p',
        'hi',
    ]
