import json
import shutil

import pytest

from tests.support import REPO, file

pytestmark = pytest.mark.live


def test_examples_validate_and_setup_is_idempotent(live):
    fixture = live
    shutil.rmtree(fixture.paths.root / 'layers')
    shutil.copytree(REPO / 'examples/layers', fixture.paths.root / 'layers')
    file(
        fixture.paths.home / '.pi/agent/settings.json', json.dumps({'packages': ['unrelated-pack']})
    )
    for _ in range(2):
        result = fixture.ctl('setup')
        assert result.returncode == 0, result.stderr
    settings = json.loads((fixture.paths.home / '.pi/agent/settings.json').read_text())
    assert settings['packages'][0] == 'unrelated-pack'
    assert settings['packages'][1]['skills'] == []
    result = fixture.ctl('validate')
    assert result.returncode == 0, result.stderr
    result = fixture.ctl('doctor')
    assert result.returncode == 0, result.stderr
    assert 'nono: 0.80.0' in result.stdout and 'configuration: valid' in result.stdout
