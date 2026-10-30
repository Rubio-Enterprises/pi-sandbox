import hashlib
import json
import subprocess

import pytest

from pi_sandbox import Refusal, pack
from pi_sandbox.paths import Paths
from pi_sandbox.seatbelt import static_rules
from tests.support import file


def test_pack_pin_and_missing(paths):
    assert pack.REFERENCE == 'nolabs-ai/pi@0.2.2'
    with pytest.raises(Refusal, match='pi-sandbox-ctl setup'):
        pack.require(paths)
    file(paths.nono / 'packages/lockfile.json', '{"packages":{"nolabs-ai/pi":{"version":"0.2.1"}}}')
    assert pack.installed(paths) == '0.2.1'
    with pytest.raises(Refusal):
        pack.require(paths)


@pytest.mark.parametrize('entry', ['string', 'object'])
def test_setup_skill_filter_is_idempotent_and_preserves_other_packs(fixture, monkeypatch, entry):
    paths = fixture.paths
    monkeypatch.setenv('PATH', fixture.env['PATH'])
    monkeypatch.setenv('TEST_OUT', str(fixture.out))
    source = str(pack.directory(paths))
    original = source if entry == 'string' else {'source': source, 'extensions': ['extra']}
    settings = file(
        paths.home / '.pi/agent/settings.json',
        json.dumps({'other': True, 'packages': [original, 'other-pack']}),
    )
    pack.setup(paths)
    first = settings.read_text()
    pack.setup(paths)
    assert first == settings.read_text()
    data = json.loads(first)
    assert data['other'] and data['packages'][1] == 'other-pack'
    assert data['packages'][0]['skills'] == []
    if entry == 'object':
        assert data['packages'][0]['extensions'] == ['extra']


def test_setup_pulls_only_when_missing(paths, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == 'pull':
            file(
                paths.nono / 'packages/lockfile.json',
                json.dumps(
                    {
                        'packages': {
                            'nolabs-ai/pi': {
                                'version': '0.2.2',
                                'artifacts': {
                                    'profiles/pi.json': {
                                        'sha256': hashlib.sha256(b'{}').hexdigest()
                                    }
                                },
                            }
                        }
                    }
                ),
            )
            file(pack.directory(paths) / 'profiles/pi.json', '{}')
        return subprocess.CompletedProcess(argv, 0, 'nono 0.80.0', '')

    monkeypatch.setattr(pack.subprocess, 'run', run)
    pack.setup(paths)
    pack.setup(paths)
    assert sum(argv[1] == 'pull' for argv in calls) == 1


@pytest.mark.parametrize(
    'manifest,roots',
    [
        (None, ['skills']),
        ('{}', ['skills']),
        ('{"pi":{"skills":["guides","absent"]}}', ['guides']),
        ('{"pi":{"skills":[]}}', []),
        ('not json', []),
        ('{"pi":{"skills":[1]}}', []),
    ],
)
def test_skill_root_manifest(paths, manifest, roots):
    directory = paths.home / 'pack'
    (directory / 'skills').mkdir(parents=True)
    (directory / 'guides').mkdir()
    if manifest is not None:
        file(directory / 'package.json', manifest)
    assert pack.skills([directory]) == [str(directory / root) for root in roots]


def test_xdg_override_static_floors(paths):
    changed = Paths.from_env(
        {'HOME': str(paths.home), 'XDG_CONFIG_HOME': str(paths.home / 'config2')}
    )
    rule = static_rules(changed, [])[0]
    for root in (paths.config, changed.config):
        assert str(root / 'zsh') in rule
        assert str(root / 'nono/profiles') in rule
        assert str(root / 'nono/packages') in rule


def test_private_directory_name_mode_and_realpath(paths):
    import re

    from pi_sandbox.paths import temporary_dir

    path = temporary_dir(paths.state, 'launch.')
    assert re.fullmatch(r'launch\.[A-Za-z0-9]{8}', path.name)
    assert path.stat().st_mode & 0o777 == 0o700
    assert path == path.resolve()


def test_golden_static_rules(paths):
    from pathlib import Path

    expected = json.loads((Path(__file__).parent.parent / 'fixtures/static-rules.json').read_text())
    actual = static_rules(Paths.from_env({'HOME': '/fixture/home'}), ['/fixture/protected'])
    assert actual == expected
