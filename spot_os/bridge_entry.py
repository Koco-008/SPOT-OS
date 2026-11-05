"""Root-owned verified-package entry. Verify signature with installed code FIRST.

No import from CWD/PYTHONPATH, no user-owned code, no unsigned bootstrap.
See MIGRATION_014_016.md for the mandatory independent verification step.
"""
import os
from pathlib import Path
import stat
import sys


def main():
    if not sys.flags.isolated or os.geteuid()!=0:raise PermissionError('Requires root and python3 -I')
    folder=Path(__file__).absolute().parent
    for path in (*folder.parents,folder,*folder.rglob('*')):
        info=path.lstat()
        if info.st_uid!=0 or info.st_mode & 0o022 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise PermissionError('Untrusted bridge code/parents')
    sys.path.insert(0,str(folder.parent))
    from spot_os.package import verify_package
    verify_package(folder)
    if sys.argv[1:]==['--update-service']:
        from spot_os.update_service import main as execute
        return execute()
    if sys.argv[1:]==['--boot-facts']:
        from spot_os.boot_facts import publish
        from spot_os.system import System
        publish(System());return 0
    if sys.argv[1:] in (['--recover-os-update'],['--doctor'],['--check']):
        from spot_os.cli import main as execute
        return execute([sys.argv[1][2:]])
    if sys.argv[1:]==['--undo-bridge']:
        import json
        from spot_os.legacy_bridge import undo
        from spot_os.system import System
        print(json.dumps(undo(System()),indent=2));return 0
    from spot_os.legacy_bridge import main as execute
    return execute(sys.argv[1:])


if __name__=='__main__':sys.exit(main())
