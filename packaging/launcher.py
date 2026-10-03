"""Entry point of the packaged app: opens the tray app unless told otherwise."""

import sys

from homestream.cli import main

if len(sys.argv) == 1:
    sys.argv.append("tray")
sys.exit(main())
