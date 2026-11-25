import itertools
import re
import stat

import pytest

from pi_sandbox import Refusal
from pi_sandbox.cli import Arguments, split
from pi_sandbox.config import Config, validate_section
from pi_sandbox.launch import select_workdir, workdir_id


def rooted(paths, prefix='agent-'):
    root = paths.home / 'sprite-sandbox'
    workspace = {'workdir_root': str(root)} | ({'workdir_prefix': prefix} if prefix else {})
    return Config(paths, values={'workspace': workspace}), root


def ids(*values):
    sequence = iter(values)
    return lambda: next(sequence)


def test_split_reads_workdir_name_and_marks_an_explicit_workdir():
    args = split(['--workdir-name=eval-2', '--workdir=/code', '-p', 'x'])
    assert args.workdir_name == 'eval-2'
    assert args.workdir == '/code' and args.workdir_given
    assert args.rest == ['-p', 'x']
    assert not split(['-p', 'x']).workdir_given


def test_split_leaves_pi_name_flag_to_pi():
    assert split(['--name', 'Refactor']).rest == ['--name', 'Refactor']


@pytest.mark.parametrize('value', ['', 'Eval', 'a_b', '-a', 'a-', 'a--b', 'a/b', '../x'])
def test_split_refuses_malformed_workdir_names(value):
    with pytest.raises(Refusal, match='--workdir-name'):
        split([f'--workdir-name={value}'])


@pytest.mark.parametrize('value', ['agent', 'Agent-', 'agent_', '-', 'a--', 'agent-x'])
def test_prefix_must_be_lowercase_words_ending_in_a_hyphen(paths, value):
    with pytest.raises(Refusal, match='ending in a hyphen'):
        validate_section({'workspace': {'workdir_prefix': value}}, paths, 'fixture', root=True)


def test_root_expands_and_prefix_validates(paths):
    result = validate_section(
        {'workspace': {'workdir_root': '~/sprite-sandbox', 'workdir_prefix': 'agent-'}},
        paths,
        'fixture',
        root=True,
    )
    assert result['workspace'] == {
        'workdir_root': str(paths.home / 'sprite-sandbox'),
        'workdir_prefix': 'agent-',
    }


def test_without_a_root_the_workdir_is_unchanged(paths, config):
    assert select_workdir(config, Arguments(workdir='/anywhere'), paths.home) == '/anywhere'


def test_without_a_root_a_workdir_name_refuses(paths, config):
    with pytest.raises(Refusal, match=re.escape('workspace.workdir_root')):
        select_workdir(config, Arguments(workdir_name='eval'), paths.home)


def test_outside_the_root_creates_a_private_new_workdir_and_says_so(paths, capsys):
    config, root = rooted(paths)
    outside = paths.home / 'project'
    outside.mkdir()
    chosen = select_workdir(config, Arguments(workdir=str(outside)), paths.home, ids('k3f9q2'))
    assert chosen == str(root.resolve() / 'agent-k3f9q2')
    for folder in (root, root / 'agent-k3f9q2'):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert f'new workdir {root / "agent-k3f9q2"}' in capsys.readouterr().err


def test_home_itself_creates_a_new_workdir(paths):
    config, root = rooted(paths)
    chosen = select_workdir(config, Arguments(workdir=str(paths.home)), paths.home, ids('aaaaaa'))
    assert chosen == str(root.resolve() / 'agent-aaaaaa')


def test_the_root_itself_creates_a_new_workdir(paths):
    config, root = rooted(paths)
    root.mkdir()
    chosen = select_workdir(config, Arguments(workdir=str(root)), paths.home, ids('bbbbbb'))
    assert chosen == str(root.resolve() / 'agent-bbbbbb')


def test_a_name_goes_between_prefix_and_id(paths):
    config, root = rooted(paths)
    args = Arguments(workdir=str(paths.home), workdir_name='eval')
    chosen = select_workdir(config, args, paths.home, ids('cccccc'))
    assert chosen == str(root.resolve() / 'agent-eval-cccccc')


def test_no_prefix_means_name_and_id_only(paths):
    config, root = rooted(paths, prefix='')
    args = Arguments(workdir=str(paths.home), workdir_name='eval')
    assert select_workdir(config, args, paths.home, ids('dddddd')) == str(
        root.resolve() / 'eval-dddddd'
    )


def test_an_existing_name_is_never_reused_for_a_new_workdir(paths):
    config, root = rooted(paths)
    (root / 'agent-taken1').mkdir(parents=True)
    (root / 'agent-taken1' / 'keep').write_text('x')
    chosen = select_workdir(
        config, Arguments(workdir=str(paths.home)), paths.home, ids('taken1', 'fresh2')
    )
    assert chosen == str(root.resolve() / 'agent-fresh2')
    assert (root / 'agent-taken1' / 'keep').read_text() == 'x'


def test_gives_up_after_repeated_collisions(paths):
    config, root = rooted(paths)
    (root / 'agent-same00').mkdir(parents=True)
    with pytest.raises(Refusal, match='every name tried exists'):
        select_workdir(
            config,
            Arguments(workdir=str(paths.home)),
            paths.home,
            lambda: 'same00',
        )


@pytest.mark.parametrize('given', [False, True])
def test_a_folder_directly_in_the_root_is_reused(paths, given):
    config, root = rooted(paths)
    existing = root / 'agent-eval-k3f9q2'
    existing.mkdir(parents=True)
    args = Arguments(workdir=str(existing), workdir_given=given)
    assert select_workdir(config, args, paths.home, ids()) == str(existing.resolve())
    assert sorted(p.name for p in root.iterdir()) == ['agent-eval-k3f9q2']


def test_a_workdir_name_inside_an_existing_workdir_refuses(paths):
    config, root = rooted(paths)
    existing = root / 'agent-k3f9q2'
    existing.mkdir(parents=True)
    with pytest.raises(Refusal, match='already is one'):
        select_workdir(
            config, Arguments(workdir=str(existing), workdir_name='eval'), paths.home, ids()
        )


def test_a_folder_deeper_in_the_root_refuses(paths):
    config, root = rooted(paths)
    deeper = root / 'agent-k3f9q2' / 'sessions'
    deeper.mkdir(parents=True)
    with pytest.raises(Refusal, match=re.escape(f'not in {deeper.resolve()}')):
        select_workdir(config, Arguments(workdir=str(deeper)), paths.home, ids())


@pytest.mark.parametrize('where', ['outside', 'root', 'missing'])
def test_an_explicit_workdir_must_be_an_existing_folder_in_the_root(paths, where):
    config, root = rooted(paths)
    root.mkdir()
    target = {
        'outside': paths.home,
        'root': root,
        'missing': root / 'agent-missing',
    }[where]
    with pytest.raises(Refusal, match='--workdir must be an existing folder'):
        select_workdir(
            config, Arguments(workdir=str(target), workdir_given=True), paths.home, ids()
        )
    assert list(root.iterdir()) == []


def test_ids_are_six_lowercase_letters_or_digits_and_vary():
    sample = list(itertools.islice(iter(workdir_id, None), 50))
    assert all(re.fullmatch(r'[a-z0-9]{6}', value) for value in sample)
    assert len(set(sample)) > 45
