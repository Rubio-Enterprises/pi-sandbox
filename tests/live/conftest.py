import fcntl
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from pi_sandbox.platform import Platform
from tests.support import HOST_PATH, make_fixture

CACHE = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'pi-sandbox-tests'
NONO = shutil.which('nono', path=HOST_PATH)


@pytest.fixture(scope='session')
def pack_cache():
    if NONO is None:
        pytest.skip('real nono is absent')
    platform = Platform.detect()
    if platform.os == 'linux':
        try:
            platform.preflight()
        except Exception as error:
            pytest.skip(str(error))
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / 'pack-home'
    with (CACHE / 'pack.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = cache / '.config/nono/packages/lockfile.json'
        valid = (
            marker.is_file()
            and json.loads(marker.read_text())['packages']['nolabs-ai/pi']['version'] == '0.2.2'
        )
        if not valid:
            with tempfile.TemporaryDirectory(dir=CACHE, prefix='pull.') as name:
                home = Path(name)
                env = {'HOME': name, 'PATH': HOST_PATH, 'NONO_NO_UPDATE_CHECK': '1'}
                result = subprocess.run(
                    [NONO, 'pull', '-s', 'nolabs-ai/pi@0.2.2'],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                assert result.returncode == 0, result.stderr
                if cache.exists():
                    shutil.rmtree(cache)
                (cache / '.config').mkdir(parents=True)
                shutil.move(home / '.config/nono', cache / '.config/nono')
    return cache


@pytest.fixture
def live(pack_cache):
    with tempfile.TemporaryDirectory(prefix='live.', dir=CACHE) as name:
        fixture = make_fixture(Path(name))
        shutil.rmtree(fixture.paths.nono)
        shutil.copytree(pack_cache / '.config/nono', fixture.paths.nono)
        for binary in ('nono', 'tmux', 'agent-browser'):
            (fixture.bins / binary).unlink()
        yield fixture
