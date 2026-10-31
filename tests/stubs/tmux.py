import os
import sys
from pathlib import Path

with Path(os.environ['TEST_OUT'], 'cleanup').open('a') as stream:
    stream.write('tmux|' + '|'.join(sys.argv[1:]) + '\n')
