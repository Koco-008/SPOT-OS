"""Fresh-device entrypoint: sudo python3 -I /root/verified/spot_os/setup_entry.py.

The administrator verifies the OS signature BEFORE extracting into a root-owned
tree. No installed OS shim is needed. CWD and PYTHONPATH are ignored by -I.
"""
import os
from pathlib import Path
import stat
import sys


def main():
    if not sys.flags.isolated or os.geteuid() != 0:
        raise PermissionError('Use root and python3 -I with a verified root-owned package')
    folder = Path(__file__).absolute().parent
    for path in (*folder.parents, folder, *folder.rglob('*')):
        info = path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise PermissionError('Fresh setup package/parents must be root-owned, unwritable and without symlinks')
    sys.path.insert(0, str(folder.parent))
    from spot_os.package import verify_package
    verify_package(folder)
    from spot_os.cli import main as execute
    return execute()


if __name__ == '__main__':
    sys.exit(main())
