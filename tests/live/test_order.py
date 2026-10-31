import json
import subprocess

import pytest

from pi_sandbox.config import resolve
from pi_sandbox.paths import atomic_json
from pi_sandbox.profile import chain, sync
from pi_sandbox.seatbelt import static_rules

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        __import__('sys').platform != 'darwin', reason='Seatbelt resolution is macOS-only'
    ),
]


def test_native_resolver_preserves_legacy_effective_order(live):
    fixture = live
    config = resolve(fixture.paths, 'macos')
    sync(config)
    preview = fixture.ctl('profile', '--workdir=' + str(fixture.repo))
    assert preview.returncode == 0, preview.stderr
    current = json.loads(preview.stdout)
    floors = static_rules(fixture.paths, config.entries('grants', 'protected_checkouts'))
    rules = current['unsafe_macos_seatbelt_rules']
    assert rules[-len(floors) :] == floors
    dynamic = rules[: -len(floors)]
    assert dynamic[0].startswith('(deny file-write* (literal ')
    assert dynamic[-1].startswith('(deny file-write* (require-all')
    # nono 0.80 appends inherited platform raw rules after child top-level raw rules
    atomic_json(
        fixture.paths.nono / 'profiles/legacy-fixture.json',
        {
            'extends': chain(config),
            'meta': {'name': 'legacy-fixture'},
            'platform_overrides': {'macos': {'unsafe_macos_seatbelt_rules': floors}},
        },
    )
    new = fixture.out / 'new.json'
    old = fixture.out / 'old.json'
    atomic_json(new, current)
    atomic_json(old, {'extends': 'legacy-fixture', 'unsafe_macos_seatbelt_rules': dynamic})
    resolved = []
    for path in (new, old):
        result = subprocess.run(
            ['nono', 'profile', 'show', str(path), '--json'],
            env=fixture.env,
            cwd=fixture.repo,
            capture_output=True,
            text=True,
            check=True,
        )
        resolved.append(json.loads(result.stdout)['unsafe_macos_seatbelt_rules'])
    assert resolved[0] == resolved[1] == list(dict.fromkeys([*dynamic, *floors]))
    assert resolved[0][-len(floors) :] == floors
