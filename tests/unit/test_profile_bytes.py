import hashlib

import pytest

from pi_sandbox import Refusal, profile
from pi_sandbox import paths as path_tools
from pi_sandbox.config import Config, Layer


def configuration(paths):
    return Config(
        paths,
        [
            Layer('10-org', paths.root, {}, {'filesystem': {'deny': ['/example/secret']}}),
            Layer(
                '50-user',
                paths.root,
                {},
                {
                    'meta': {'description': 'Unicode: café'},
                    'filesystem': {'read': ['/example/context']},
                },
            ),
        ],
    )


@pytest.mark.parametrize('separator', [b'\r\n', b'\r'], ids=['crlf', 'cr'])
@pytest.mark.parametrize('kind', ['base', 'layer'])
def test_existing_profile_newline_mutation_is_refused(paths, separator, kind):
    config = configuration(paths)
    profile.sync(config)
    name = profile.chain(config)[0 if kind == 'base' else 1]
    target = paths.nono / 'profiles' / (name + '.json')
    original = target.read_bytes()
    mutated = original.replace(b'\n', separator)
    assert mutated != original
    target.write_bytes(mutated)
    assert not name.endswith(hashlib.sha256(mutated).hexdigest()[:12])
    with pytest.raises(Refusal, match='integrity mismatch'):
        profile.sync(config)
    assert target.read_bytes() == mutated


def test_every_installed_profile_name_hashes_its_stored_bytes(paths):
    config = configuration(paths)
    profile.sync(config)
    profile.sync(config)
    installed = sorted((paths.nono / 'profiles').glob('pi-sandbox-*.json'))
    assert {path.stem for path in installed} == set(profile.chain(config))
    for target in installed:
        assert target.stem.rsplit('-', 1)[1] == hashlib.sha256(target.read_bytes()).hexdigest()[:12]


def test_trusted_binary_reader_preserves_bytes_without_text_decoding(paths):
    target = paths.home / 'binary-policy'
    content = b'{"line": 1}\r\n\r\x00\xff'
    target.write_bytes(content)
    assert path_tools.trusted_bytes(target) == content
    with pytest.raises(UnicodeDecodeError):
        path_tools.trusted_text(target)
