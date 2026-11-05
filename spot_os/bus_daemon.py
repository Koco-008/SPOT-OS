#!/usr/bin/python3
"""dbus-run-session daemon adapter: broker lifetime follows its actual parent.

dbus-run-session only kills its broker on the normal waitpid(app) path. PAM
places that broker outside the service cgroup. Set PDEATHSIG in the broker
itself (fork clears it), then exec the system daemon without a shell.
"""
import ctypes
import os
from pathlib import Path
import signal
import sys


def main():
    parent = os.getppid()
    if parent == 1 or Path('/proc', str(parent), 'exe').resolve() != Path('/usr/bin/dbus-run-session').resolve():
        raise RuntimeError('Private broker requires dbus-run-session parent')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'broker PR_SET_PDEATHSIG failed')
    if os.getppid() != parent:
        raise RuntimeError('Private broker parent exited')
    os.execv('/usr/bin/dbus-daemon', ['/usr/bin/dbus-daemon', *sys.argv[1:]])


if __name__ == '__main__':
    main()
