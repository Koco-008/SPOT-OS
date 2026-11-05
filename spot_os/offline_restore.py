"""Restore the boot path on a mounted SD card, without executing target binaries.

Run on a separate Linux maintenance computer, sudo, with both ext4 root and FAT
boot partition mounted. Refuses / and any parent symlink escaping the mount.
"""
import argparse
import os
from pathlib import Path
from .installer import Journal
from .system import System


class OfflineSystem(System):
    def path(self, path):
        value = str(path)
        # Debian usrmerge symlinks are absolute relative to the TARGET filesystem.
        for old, new in (('/lib/', '/usr/lib/'), ('/bin/', '/usr/bin/'), ('/sbin/', '/usr/sbin/')):
            if value.startswith(old) and (self.root/old.strip('/')).is_symlink():
                value = new+value[len(old):]
        result = super().path(value)
        if not result.parent.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Offline path escapes mounted card')
        return result

    def command(self, *args, **kwargs):
        raise RuntimeError('Offline restoration never executes target commands')


def restore(root):
    root = Path(root).resolve()
    if root == Path('/').resolve() or not root.is_dir():
        raise ValueError('Provide mounted SD root, never the maintenance host root')
    system = OfflineSystem(root)
    journals = [Journal(system, name) for name in ('backlight', 'activation')]
    allowed = ('/boot/', '/etc/systemd/system/', '/etc/initramfs-tools/', '/etc/plymouth/',
               '/usr/share/plymouth/themes/spot/', '/lib/firmware/spot-os.tga', '/etc/spot/device-power.json')
    for journal in journals:
        for item in journal.data['entries']:
            if not item['path'].startswith(allowed):
                raise ValueError('Unexpected offline restore destination')
            system.path(item['path'])
            if 'blob' in item:
                journal.contents(item)
    for journal in journals:
        if journal.data['phase'] != 'new':
            journal.restore()
    return dict(restored=True, system_commands_executed=False)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args=parser.parse_args()
    if os.name!='posix' or os.geteuid()!=0:
        raise PermissionError('Run as root on a separate Linux maintenance host')
    root=args.root.resolve()
    if not os.path.ismount(root) or not os.path.ismount(root/'boot/firmware'):
        raise ValueError('Mount the SD root AND its FAT boot partition first')
    print(restore(root))
