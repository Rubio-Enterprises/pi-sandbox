"""Platform injection belongs to tests, never to the installed command."""

import sys

from pi_sandbox.cli import main
from pi_sandbox.platform import Platform

os_name, release, wsl, *args = sys.argv[1:]
raise SystemExit(main(args, platform=Platform(os_name, release, wsl == 'yes')))
