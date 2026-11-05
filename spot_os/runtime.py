"""Root-owned public boot status; private state stays under /var/lib/spot-os."""
import os
from pathlib import Path
import stat
from .common import atomic


def root_readable_directory(folder, mode):
    folder = Path(folder)
    folder.mkdir(mode=mode, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise PermissionError('Public OS directory must be a root-owned real directory')
    # mkdir(mode) is filtered by UMask=0077 and does not repair an existing leaf.
    os.chmod(folder, mode, follow_symlinks=False)
    return folder


def publish_boot_status(value, folder=Path('/run/spot-os')):
    folder = root_readable_directory(folder,0o711)
    # Explicit allowlist prevents accidental publication of private state.
    public = {key: value[key] for key in ('attempts', 'boot', 'blocked') if key in value}
    atomic(folder/'boot-blocked.json', public, 0o644)
