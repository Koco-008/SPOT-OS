"""Bounded host inventory, delegated only for the module-hidden OTA namespace.

No module loading, boot writes, networking or caller-controlled paths. Facts
are not a blanket exemption: require a fresh root-owned record for THIS boot
and kernel. Missing modules/config/images fail exactly as on the host.
"""
import os
import platform
import shutil
import stat
import time
from .common import atomic, read

FACTS = '/run/spot-os/boot-prerequisites.json'


def collect(system):
    kernel = platform.release()
    config = ''
    for name in ('/boot/config-'+kernel, '/lib/modules/'+kernel+'/build/.config'):
        path = system.path(name)
        if path.is_file():
            config = path.read_text()
            break
    if not config and system.path('/proc/config.gz').is_file():
        import gzip
        config = gzip.decompress(system.path('/proc/config.gz').read_bytes()).decode()
    kernels = [p.name for p in system.path('/lib/modules').glob('*') if p.is_dir()]
    initrds = list(system.path('/boot').glob('initrd.img-*')) + list(system.path('/boot/firmware').glob('initramfs*'))
    images = [p for p in initrds if p.is_file() and p.stat().st_size > 0]
    return dict(schema=1, boot=system.path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        kernel=kernel, created=time.time(), kernels=kernels,
        initramfs=bool(kernel in kernels and images and shutil.which('update-initramfs') and
            system.path('/usr/share/initramfs-tools/hooks/plymouth').exists()),
        kernel_logo='CONFIG_LOGO=y' in config and any('CONFIG_DRM_VC4='+v in config for v in ('m','y')))


def publish(system):
    if os.geteuid() != 0: raise PermissionError('Root host inventory required')
    # Runtime directory is normally prepared before session start.
    from .runtime import root_readable_directory
    root_readable_directory(system.path(FACTS).parent, 0o711)
    data = collect(system)
    atomic(system.path(FACTS), data)
    return data


def inspect(system):
    if os.environ.get('SPOT_OS_ISOLATED_PREFLIGHT') != '1':
        return collect(system)
    try:
        system.command('/usr/bin/systemctl', 'start', 'spot-os-preflight.service', timeout=25)
    except RuntimeError as exc:
        raise RuntimeError('Host-Kernelprüfung fehlgeschlagen; spot-os doctor und das Journal von spot-os-preflight.service prüfen') from exc
    path = system.path(FACTS)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise PermissionError('Untrusted host preflight facts')
    data = read(path)
    if (data.get('schema') != 1 or data.get('kernel') != platform.release() or
            data.get('boot') != system.path('/proc/sys/kernel/random/boot_id').read_text().strip() or
            not 0 <= time.time()-data.get('created', 0) <= 30):
        raise ValueError('Host preflight facts stale or from another kernel/boot')
    return data
