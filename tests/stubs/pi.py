import json
import os
import signal
import sys
from pathlib import Path

out = Path(os.environ['TEST_OUT'])
(out / 'pi.json').write_text(json.dumps(dict(argv=sys.argv[1:], env=dict(os.environ))))
if os.environ.get('TEST_HOLD'):
    (out / 'ready').touch()
    while True:
        signal.pause()
sys.stdout.write('pi-stdout\n')
sys.stderr.write('pi-stderr\n')
raise SystemExit(int(os.environ.get('TEST_STATUS', '0')))
