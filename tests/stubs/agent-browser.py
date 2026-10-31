import os
import sys
from pathlib import Path

with Path(os.environ['TEST_OUT'], 'cleanup').open('a') as stream:
    stream.write(
        'browser|'
        + '|'.join(sys.argv[1:])
        + '|'
        + os.environ['AGENT_BROWSER_SOCKET_DIR']
        + '|'
        + os.environ.get('AGENT_BROWSER_NAMESPACE', '<unset>')
        + '|'
        + str(Path(os.environ['AGENT_BROWSER_SOCKET_DIR']).is_dir())
        + '\n'
    )
