"""Installed with a fixed absolute path; invoke ONLY using python3 -I."""
import os
from pathlib import Path
import re
import stat
import sys


def trusted_tree(base):
    base=Path(base)
    for parent in (base,*base.parents):
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise PermissionError('Untrusted administrative import directory')
    link=base/'spot_os'
    target=os.readlink(link)
    if not re.fullmatch(r'0\.[0-9]+\.[0-9]+',target):
        raise PermissionError('Unexpected installed OS target')
    folder=base/target
    for path in (folder,*folder.rglob('*')):
        info=path.lstat()
        if info.st_uid!=0 or info.st_mode & 0o022 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise PermissionError('Untrusted installed OS code')
    return base


def main():
    if not sys.flags.isolated:
        raise RuntimeError('Administrative entrypoint requires python3 -I')
    base=trusted_tree(Path(__file__).resolve().parent.parent if Path(__file__).parent.name[0].isdigit()
                      else Path(__file__).resolve().parent)
    sys.path.insert(0,str(base))
    if sys.argv[1:]==['--update-service']:
        from spot_os.update_service import main as execute
        return execute()
    if sys.argv[1:]==['--boot-facts']:
        from spot_os.boot_facts import publish
        from spot_os.system import System
        publish(System())
        return 0
    from spot_os.cli import main as execute
    return execute()


if __name__=='__main__':
    sys.exit(main())
