#!/usr/bin/env python3
"""Job to Paid toolkit. Usage: python3 jtp.py <command> [options]   (try: python3 jtp.py --help)

Standard library only (Python 3.9+). Reads and writes local files in your data
folder; makes no network connections.
"""

import os
import sys

if sys.version_info < (3, 9):
    sys.stdout.write('{"ok": false, "error": "Job to Paid needs Python 3.9 or newer."}\n')
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jobtopaid.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
