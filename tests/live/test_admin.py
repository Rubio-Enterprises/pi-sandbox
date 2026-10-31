import json
import shutil
from functools import partial

import pytest

from pi_sandbox.platform import Platform
from tests.support import REPO, file

pytestmark = pytest.mark.live


def test_examples_validate_and_setup_is_idempotent(live):
    fixture = live
    platform = Platform.detect()
    ctl = partial(fixture.ctl, os_name=platform.os, release=platform.release, wsl=platform.wsl)
    shutil.rmtree(fixture.paths.root / 'layers')
    shutil.copytree(REPO / 'examples/layers', fixture.paths.root / 'layers')
    file(
        fixture.paths.home / '.pi/agent/settings.json', json.dumps({'packages': ['unrelated-pack']})
    )
    for _ in range(2):
        result = ctl('setup')
        assert result.returncode == 0, result.stderr
    settings = json.loads((fixture.paths.home / '.pi/agent/settings.json').read_text())
    assert settings['packages'][0] == 'unrelated-pack'
    assert settings['packages'][1]['skills'] == []
    result = ctl('validate')
    assert result.returncode == 0, result.stderr
    result = ctl('doctor')
    assert result.returncode == 0, result.stderr
    assert 'nono: 0.80.0' in result.stdout and 'configuration: valid' in result.stdout
