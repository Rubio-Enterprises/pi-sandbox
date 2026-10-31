import pytest

from pi_sandbox import Refusal
from pi_sandbox.cli import split, workdir
from pi_sandbox.platform import Platform


def test_split_preserves_pi_arguments():
    args = split(
        [
            '--workdir=/code',
            '--add-dirs',
            '/a with space:/b',
            '--add-dirs-ro=/c',
            '-xt',
            'one,two',
            '--exclude-tools=two,three',
            '-p',
            'prompt',
            '--scratch=effort_2',
            '--',
            '-xt',
            'keep',
        ]
    )
    assert args.workdir == '/code' and args.scratch == 'effort_2'
    assert args.rw == ['/a with space', '/b'] and args.ro == ['/c']
    assert args.patterns == ['one', 'two', 'two', 'three']
    assert args.rest == ['-p', 'prompt', '--', '-xt', 'keep']


@pytest.mark.parametrize(
    'args',
    [
        ['--add-dirs'],
        ['--add-dirs-ro'],
        ['--scratch=../escape'],
        ['--scratch=-leading'],
        ['--scratch=a/b'],
    ],
)
def test_refused_args(args):
    with pytest.raises(Refusal):
        split(args)


def test_missing_exclusion_value_matches_predecessor():
    assert split(['--exclude-tools']).patterns == []


@pytest.mark.parametrize('relative', ['', '..'])
def test_home_and_ancestor_workdirs(paths, relative):
    with pytest.raises(Refusal, match='contains \\$HOME'):
        workdir(str(paths.home / relative), paths.home)


@pytest.mark.parametrize(
    'release,ok', [('6.11.99', False), ('6.12.0', True), ('7.0.0', True), ('bad', False)]
)
def test_linux_kernel(release, ok):
    platform = Platform('linux', release)
    if ok:
        platform.preflight()
    else:
        with pytest.raises(Refusal, match='wsl --update'):
            platform.preflight()
