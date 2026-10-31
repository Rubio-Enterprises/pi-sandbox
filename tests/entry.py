"""Platform injection belongs to tests, never to the installed command."""

import sys
from unittest.mock import patch

from pi_sandbox import cli, ctl
from pi_sandbox.platform import Platform

command, os_name, release, wsl, *args = sys.argv[1:]
platform = Platform(os_name, release, wsl == 'yes')
if command == 'ctl':
    with patch.object(Platform, 'detect', return_value=platform):
        raise SystemExit(ctl.main(args))
raise SystemExit(cli.main(args, platform=platform))
