"""Root-installed OS adapter, only for the stable launcher's signed GUI child.

This does not edit release files, signature checks, locks or Handoff readiness.
Other Python programs (including the launcher and updater) remain untouched.
"""
import os
from pathlib import Path
import sys

if (os.environ.get('SPOT_OS_SOCKET') and Path(sys.argv[0]).name == 'SPOT_v27.py'
        and os.environ.get('SPOT_LAUNCH_PREFIX') and os.environ.get('SPOT_LAUNCH_PID')):
    from spot_os.qt_bridge import install
    install()
