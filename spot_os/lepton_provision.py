"""Explicit first-install Lepton provisioning. Never imported by the OTA engine.

Reuse Installer.Journal for all controlled files. apt/DKMS/account operations are
recorded but cannot be rolled back transactionally; see LEPTON_PROVISIONING.md.
Existing compatible installations return before locks, writes or package access.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import time

from .common import atomic, read
from .installer import Journal, STATE
from .lepton import BINARY, LOCK, MARKERS, PROTOCOL, RECORD, REVIEWED_COMMIT, inspect
from . import lepton_source

KERNELS = ('6.18.50+rpt-rpi-2712',)
DKMS_VERSION = '1.0.0.spot20be84ea'
DKMS_ROOT = '/usr/src/lepton-' + DKMS_VERSION
JOURNAL = 'lepton-provision'
SERVICE = '/etc/systemd/system/lepton-streamer.service'
PACKAGES = ('build-essential', 'dkms', 'device-tree-compiler', 'i2c-tools',
            'v4l-utils', 'v4l2loopback-dkms', 'kmod', 'util-linux')
MANAGED = (BINARY, RECORD, SERVICE, '/usr/local/libexec/lepton/rpi_vsync_app',
    '/usr/local/libexec/lepton/rpi_recovery_app', '/usr/local/libexec/lepton/recover_lepton_rpi5.sh',
    '/etc/modprobe.d/lepton-streams.conf', '/etc/modules-load.d/lepton-streams.conf',
    '/etc/modules-load.d/spot-lepton-i2c.conf', '/etc/udev/rules.d/70-lepton-vspi.rules',
    '/etc/tmpfiles.d/spot-lepton.conf', SERVICE + '.d/zzzz-spot-cci-lock.conf',
    '/etc/systemd/system/multi-user.target.wants/lepton-streamer.service',
    '/usr/local/share/doc/lepton-pi5/UPSTREAM-NOTICES.txt',
    '/boot/firmware/overlays/flir-lepton-rpi5.dtbo')


class ProvisionError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def secure_path(system, name, *, directory=False, create=False):
    """Reject writable/foreign parents, links and special files before root I/O."""
    path = system.path(name)
    chain = [path, *path.parents]
    for item in reversed(chain):
        if item == system.root.parent or not item.is_relative_to(system.root):
            continue
        if not item.exists() and not item.is_symlink():
            if create and (item != path or directory):
                item.mkdir(mode=0o700)
            else:
                continue
        info = item.lstat()
        # Debian usr-merge and /var/run are system-owned aliases, not arbitrary links.
        alias = item in (system.path('/lib'), system.path('/bin'), system.path('/sbin'), system.path('/var/run'))
        if stat.S_ISLNK(info.st_mode):
            if not alias or not item.resolve().is_relative_to(system.path('/usr') if item.name != 'run' else system.path('/run')):
                raise ProvisionError('unsafe_path:' + name)
            info = item.stat()
        expected_uid = 0 if system.root == Path('/') else (os.getuid() if os.name == 'posix' else info.st_uid)
        if os.name == 'posix' and (info.st_uid != expected_uid or info.st_mode & 0o022):
            raise ProvisionError('unsafe_permissions:' + name)
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise ProvisionError('unsafe_parent:' + name)
        if item == path and not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ProvisionError('unsafe_type:' + name)
    return path


@contextmanager
def admin_guard(system):
    import fcntl
    secure_path(system, STATE, directory=True, create=True)
    path = secure_path(system, STATE + '/installer.lock')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ProvisionError('unsafe_installer_lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProvisionError('operation_busy') from exc
        yield
    finally:
        os.close(fd)


def probe(system, user, *, identity=None, compatibility=inspect, format_probe=None):
    """Read-only and usable before SPOT OS is installed or activated."""
    from .camera_ready import wait_for_format
    if identity is None:
        try:
            model = system.path('/proc/device-tree/model').read_text().rstrip('\0')
            release = system.path('/etc/os-release').read_text()
        except OSError:
            model, release = '', ''
        identity = dict(model=model, arch=platform.machine(), os13=bool(re.search(r'^VERSION_ID="?13"?$', release, re.M)), kernel=platform.release())
    kernel = identity['kernel']
    result = dict(schema=1, source_commit=REVIEWED_COMMIT, kernel=kernel,
                  hardware_validated=False, state='unsupported', reason='unsupported_hardware')
    if 'Raspberry Pi 5' not in identity['model'] or identity['arch'] != 'aarch64' or not identity['os13']:
        return result
    if kernel not in KERNELS:
        return dict(result, reason='unsupported_kernel')
    result['headers_exact'] = headers_exact(system, kernel)
    cci = compatibility(system, user)
    result['cci'] = cci
    if cci.get('compatible'):
        ready = (format_probe or (lambda: wait_for_format(system, timeout=1)))()
        if ready.get('format_ready') is True:
            return dict(result, state='compatible', reason='', ready=ready)
        return dict(result, state='incomplete', reason='producer_or_format_not_ready', ready=ready)
    # Any foreign service/drop-in, module, source, overlay, lock or device means
    # this is NOT a blank system. Never overwrite it under a "missing" diagnosis.
    markers = [name for name in (*MANAGED, LOCK, '/run/lock/spot-lepton', '/dev/lepton-vspi', '/dev/video10', '/dev/video11')
               if system.path(name).exists() or system.path(name).is_symlink()]
    markers += [p.as_posix() for p in system.path('/usr/src').glob('lepton*')]
    markers += [p.as_posix() for p in system.path('/etc/systemd/system').glob('lepton*')]
    markers += [p.as_posix() for p in system.path('/sys/module').glob('lepton*')]
    markers += [p.as_posix() for p in system.path('/var/lib/dkms').glob('lepton*')]
    if system.path('/sys/module/v4l2loopback').exists():
        markers.append('loaded_foreign_loopback')
    for config in system.path('/etc/modprobe.d').glob('*.conf'):
        if 'v4l2loopback' in config.read_text(errors='replace'):
            markers.append(str(config))
    if system.command('/usr/bin/getent', 'passwd', 'lepton', check=False):
        markers.append('existing_lepton_account')
    module = system.command('/usr/sbin/modinfo', '-k', kernel, '-n', 'lepton', check=False)
    unit = system.command('/usr/bin/systemctl', 'cat', 'lepton-streamer.service', check=False)
    boot = system.path('/boot/firmware/config.txt')
    if boot.is_file() and re.search(r'^\s*dtoverlay=.*lepton', boot.read_text(), re.M):
        markers.append('boot_overlay')
    try:
        owned = read(system.path(STATE + '/' + JOURNAL + '.json'), {})
        record = read(system.path(RECORD), {})
    except (OSError, ValueError):
        owned, record = {}, {}
    state = 'incomplete' if owned.get('source_commit') == REVIEWED_COMMIT or record.get('commit') == REVIEWED_COMMIT else 'incompatible'
    if not markers and not module and not unit:
        state = 'missing'
    return dict(result, state=state, reason='driver_absent' if state == 'missing' else 'explicit_administrative_review_required', markers=markers)


def headers_exact(system, kernel):
    path = system.path('/lib/modules/' + kernel + '/build')
    try:
        allowed = system.path('/usr/src').resolve()
        if path.is_symlink():
            if not path.resolve().is_relative_to(allowed):
                return False
            # Only the package-managed build -> /usr/src alias is accepted.
            secure_path(system, '/lib/modules/' + kernel, directory=True)
        for name in ('Makefile', 'Module.symvers', 'include/config/kernel.release'):
            candidate = path/name
            resolved = candidate.resolve()
            if candidate.is_symlink() and not resolved.is_relative_to(allowed):
                return False
            relative = '/' + resolved.relative_to(system.root.resolve()).as_posix()
            secure_path(system, relative)
        return ((path/'Makefile').is_file() and (path/'Module.symvers').is_file()
            and (path/'include/config/kernel.release').read_text().strip() == kernel)
    except (OSError, ValueError, ProvisionError):
        return False


def package_plan(text):
    """Reject removals, upgrades and kernel/firmware changes, including dependencies."""
    for line in text.splitlines():
        if line.startswith('Remv '):
            raise ProvisionError('package_removal_refused')
        if line.startswith('Inst '):
            name = line.split()[1].split(':')[0]
            if '[' in line or re.match(r'(linux-image|linux-base|raspberrypi-kernel|raspberrypi-bootloader|raspi-firmware|rpi-eeprom|firmware-)', name):
                raise ProvisionError('package_upgrade_or_kernel_change_refused')
    return text


class Provisioner:
    def __init__(self, system, *, observer=probe, downloader=lepton_source.download):
        self.system, self.observer, self.downloader = system, observer, downloader

    def event(self, phase, **values):
        self.journal.data.update(phase=phase, utc=time.time(), **values)
        self.journal.save()

    def write(self, name, data, mode=0o644):
        secure_path(self.system, name)
        self.journal.write(name, data, mode)

    def run(self, user, *, resume=False, include_os=False):
        initial = self.observer(self.system, user)
        # Critical no-op: even the installer lock/journal is untouched.
        if initial['state'] == 'compatible':
            return dict(ok=True, unchanged=True, driver=initial, reboot_performed=False)
        if initial['state'] == 'unsupported':
            raise ProvisionError(initial['reason'])
        if not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user) or user == 'root':
            raise ProvisionError('unprivileged_login_user_required')
        with admin_guard(self.system):
            import pwd
            account = pwd.getpwnam(user)
            prefix = self.system.path(str(Path(account.pw_dir)/'.local/share/spot'))
            # Existing launcher/update locks share the normal OS lock order.
            # Driver-only setup can precede both OS and app installation. Once
            # a launcher prefix exists, use its exact normal maintenance locks.
            from contextlib import nullcontext
            guard = self.system.maintenance_guard(prefix) if prefix.exists() else nullcontext()
            with guard:
                return self._run(user, self.observer(self.system, user), resume, include_os=include_os)

    def _run(self, user, initial, resume, *, include_os=False):
        if initial['state'] == 'compatible':
            return dict(ok=True, unchanged=True, driver=initial, reboot_performed=False)
        if initial['state'] == 'unsupported':
            raise ProvisionError(initial['reason'])
        for name in (*MANAGED, STATE + '/' + JOURNAL + '.json', '/boot/firmware/config.txt'):
            # The only allowed symlink is our own journalled systemd enable link.
            if name.endswith('/multi-user.target.wants/lepton-streamer.service') and self.system.path(name).is_symlink():
                secure_path(self.system, str(Path(name).parent), directory=True)
                continue
            secure_path(self.system, name)
        self.journal = Journal(self.system, JOURNAL)
        old = self.journal.data
        if old.get('phase') == 'restored':
            raw = self.journal.path.read_bytes()
            atomic(self.system.path(STATE + '/history/' + hashlib.sha256(raw).hexdigest() + '/lepton-provision.json'), old)
            self.journal.data = dict(schema=1, phase='new', entries=[])
            old = self.journal.data
        if initial['state'] != 'missing':
            if not resume or old.get('source_commit') != REVIEWED_COMMIT:
                raise ProvisionError('existing_driver_refused: inspect driver-status; only an owned interrupted setup can resume')
            if old.get('user') != user:
                raise ProvisionError('interrupted_setup_user_changed')
            if old.get('kernel') != initial['kernel']:
                raise ProvisionError('interrupted_setup_kernel_changed')
            self.verify_owned()
        elif old.get('phase') not in ('new', 'restored') and not resume:
            raise ProvisionError('interrupted_setup_requires_resume')
        kernel = initial['kernel']
        if old.get('phase') in ('awaiting-reboot', 'verifying', 'complete'):
            return self.finish(user, kernel)
        if initial['state'] != 'missing':
            # Do not guess whether a failed apt/DKMS call committed. Restore the
            # controlled files explicitly and inspect package logs before retry.
            raise ProvisionError('interrupted_setup_requires_rollback: see LEPTON_PROVISIONING.md')
        self.journal.data.update(source_commit=REVIEWED_COMMIT, kernel=kernel, user=user,
            source_sha256=lepton_source.SHA256, package_transactional=False)
        try:
            self.event('packages')
            packages = list(PACKAGES) + ['linux-headers-' + kernel]
            if include_os:
                for line in Path(__file__).with_name('packages.txt').read_text().splitlines():
                    if line and not line.startswith('#'):
                        if not re.fullmatch('[a-z0-9][a-z0-9+.-]+', line):
                            raise ProvisionError('invalid_os_package_name')
                        packages.append(line)
            self.system.command('/usr/bin/apt-get', 'update', timeout=180)
            args = ('--no-upgrade', '--no-remove', 'install', *packages)
            plan = self.system.command('/usr/bin/apt-get', '--simulate', *args, timeout=60)
            package_plan(plan)
            # Persist package names/versions only, never mirror APT source URLs,
            # warnings or credentials from a locally configured repository.
            selected = re.findall(r'^Inst ([a-zA-Z0-9+.:_-]+) \(([^\s)]+)', plan, re.M)
            self.event('packages', package_plan=[dict(name=n, version=v) for n, v in selected])
            self.system.command('/usr/bin/env', 'DEBIAN_FRONTEND=noninteractive', '/usr/bin/apt-get',
                '-o', 'DPkg::Lock::Timeout=30', '-o', 'Dpkg::Options::=--force-confold', '-y', *args, timeout=900)
            if not headers_exact(self.system, kernel):
                raise ProvisionError('matching_headers_missing_or_wrong: no kernel upgrade attempted')
            self.event('source')
            cache = secure_path(self.system, STATE + '/lepton-source', directory=True, create=True)
            import uuid
            destination = cache/(REVIEWED_COMMIT + '-' + uuid.uuid4().hex)
            # Each retry freshly verifies the archive; interrupted trees stay as evidence.
            source = lepton_source.extract(self.downloader(), destination)
            self.event('building')
            self.build(source, kernel)
            self.event('configuring')
            self.configure(source, user, kernel)
            self.event('awaiting-reboot', boot_id=self.system.path('/proc/sys/kernel/random/boot_id').read_text().strip())
            return dict(ok=True, pending=True, reboot_required=True, reboot_performed=False,
                reason='New SPI device-tree overlay and login groups require a manually approved reboot. Resume the SAME explicit setup with --resume-lepton afterwards.')
        except BaseException as exc:
            phase = self.journal.data['phase']
            code = getattr(exc, 'code', str(exc) if isinstance(exc, lepton_source.SourceError)
                else phase + '_failed:' + type(exc).__name__)
            self.event('failed', error=code, failed_phase=phase)
            if isinstance(exc, Exception):
                raise ProvisionError(code) from exc
            raise

    def build(self, source, kernel):
        reported = self.system.command('/usr/bin/make', '-s', '-C',
            str(self.system.path('/lib/modules/' + kernel + '/build')), 'kernelrelease', timeout=60)
        if reported != kernel:
            raise ProvisionError('kbuild_release_mismatch')
        for folder, targets in (('lepton_sdk', ('all',)),
            ('lepton_control', ('rpi_vsync_app', 'rpi_recovery_app', 'check')),
            ('lepton_streamer', ('all', 'check')), ('lepton_module', ('overlay',))):
            self.system.command('/usr/bin/make', '-C', str(source/folder), *targets, timeout=600)
        binary = (source/'lepton_streamer/lepton_streamer').read_bytes()
        if not all(m in binary for m in MARKERS):
            raise ProvisionError('built_streamer_contract_missing')
        # The old standalone DKMS migration requires an installed module and pins
        # f031edf. Reuse its source-file layout, but bind this fresh build to the
        # current reviewed source, with no --force and no automatic future build.
        for name in ('Makefile', 'Kbuild', 'flir_lepton.c', 'flir_lepton.h', 'lepton_vospi_funcs.c', 'lepton_vospi_funcs.h'):
            self.write(DKMS_ROOT + '/' + name, (source/'lepton_module'/name).read_bytes())
        config = ('PACKAGE_NAME="lepton"\nPACKAGE_VERSION="' + DKMS_VERSION + '"\n'
            'BUILT_MODULE_NAME[0]="lepton"\nDEST_MODULE_LOCATION[0]="/extra"\nAUTOINSTALL="no"\n'
            'MAKE[0]="make KDIR=/lib/modules/${kernelver}/build"\nCLEAN="make clean"\n')
        self.write(DKMS_ROOT + '/dkms.conf', config.encode())
        self.event('dkms-add', dkms_attempted=True)
        for action in ('add', 'build', 'install'):
            self.system.command('/usr/sbin/dkms', action, '-m', 'lepton', '-v', DKMS_VERSION, '-k', kernel, timeout=900)
        vermagic = self.system.command('/usr/sbin/modinfo', '-k', kernel, '-F', 'vermagic', 'lepton')
        if not vermagic.split() or vermagic.split()[0] != kernel:
            raise ProvisionError('module_vermagic_mismatch')
        self.system.command('/usr/sbin/depmod', '-a', kernel)

    def configure(self, source, user, kernel):
        notices = ['Upstream Lepton-Pi5 commit ' + REVIEWED_COMMIT + '\nSource SHA256: ' + lepton_source.SHA256]
        for path in sorted((source/'lepton_sdk').glob('*')):
            if path.suffix in ('.c', '.h'):
                raw = path.read_text(errors='replace')
                if raw.lstrip().startswith('/*'):
                    notices.append(path.name + '\n' + raw[:raw.find('*/') + 2])
        self.write('/usr/local/share/doc/lepton-pi5/UPSTREAM-NOTICES.txt', '\n\n'.join(notices).encode())
        for src, dst in (('lepton_streamer/lepton_streamer', BINARY),
            ('lepton_control/rpi_vsync_app', '/usr/local/libexec/lepton/rpi_vsync_app'),
            ('lepton_control/rpi_recovery_app', '/usr/local/libexec/lepton/rpi_recovery_app'),
            ('tools/recover_lepton_rpi5.sh', '/usr/local/libexec/lepton/recover_lepton_rpi5.sh')):
            self.write(dst, (source/src).read_bytes(), 0o755)
        self.write(SERVICE, (source/'systemd/lepton-streamer.service').read_bytes())
        # Fresh installs fail visibly rather than looping on a missing camera.
        # Existing compatible services never reach this write. App-controlled,
        # already bounded recovery remains available through its existing API.
        self.write(SERVICE + '.d/zzzz-spot-cci-lock.conf', b'[Service]\nRestart=no\nExecStartPre=\nExecStartPre=+/usr/bin/flock -w 15 /run/lock/spot-lepton/cci.lock /usr/local/libexec/lepton/rpi_recovery_app --configure --boot-timeout-ms 6000\nExecStopPost=\nExecStopPost=+/usr/bin/flock -w 15 /run/lock/spot-lepton/cci.lock /usr/local/libexec/lepton/recover_lepton_rpi5.sh\nTimeoutStartSec=45\nTimeoutStopSec=60\n')
        self.write('/etc/modprobe.d/lepton-streams.conf', b'options v4l2loopback devices=2 video_nr=10,11 card_label="FLIR Lepton Raw,FLIR Lepton False Color" exclusive_caps=0,0 max_buffers=4\n')
        self.write('/etc/modules-load.d/lepton-streams.conf', b'v4l2loopback\n')
        self.write('/etc/modules-load.d/spot-lepton-i2c.conf', b'i2c-dev\n')
        self.write('/etc/udev/rules.d/70-lepton-vspi.rules', b'SUBSYSTEM=="video4linux", ATTR{name}=="lepton", SYMLINK+="lepton-vspi", GROUP="video", MODE="0660"\n')
        # Root-owned directory prevents group members from replacing the lock inode.
        self.write('/etc/tmpfiles.d/spot-lepton.conf', b'd /run/lock/spot-lepton 0755 root root -\nf /run/lock/spot-lepton/cci.lock 0660 root i2c -\n')
        if not self.system.command('/usr/bin/getent', 'group', 'lepton', check=False):
            self.system.command('/usr/sbin/groupadd', '--system', 'lepton')
        if not self.system.command('/usr/bin/id', '-u', 'lepton', check=False):
            self.system.command('/usr/sbin/useradd', '--system', '--gid', 'lepton', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', 'lepton')
        for account in ('lepton', user):
            self.system.command('/usr/sbin/usermod', '-a', '-G', 'video,i2c', '--', account)
        self.write('/boot/firmware/overlays/flir-lepton-rpi5.dtbo', (source/'lepton_module/flir-lepton-rpi5.dtbo').read_bytes())
        boot = self.system.path('/boot/firmware/config.txt').read_bytes()
        self.write('/boot/firmware/config.txt', boot + b'\n[all]\n# SPOT explicit Lepton first-install setup\ndtparam=i2c_arm=on\ndtparam=spi=on\ndtoverlay=flir-lepton-rpi5\n')
        self.system.command('/usr/bin/systemd-analyze', 'verify', SERVICE)
        self.system.command('/usr/bin/systemctl', 'daemon-reload')
        # Enable for next MANUAL reboot; never start a not-yet-applied overlay.
        secure_path(self.system, '/etc/systemd/system/multi-user.target.wants/lepton-streamer.service')
        self.journal.write('/etc/systemd/system/multi-user.target.wants/lepton-streamer.service', b'', link=SERVICE)

    def verify_owned(self):
        for item in self.journal.data['entries']:
            path = self.system.path(item['path'])
            expected = item.get('desired')
            if expected is None:
                raise ProvisionError('unfinished_controlled_write')
            if 'link' in expected:
                secure_path(self.system, str(Path(item['path']).parent), directory=True)
                if not path.is_symlink() or os.readlink(path) != expected['link']:
                    raise ProvisionError('owned_files_changed')
            else:
                secure_path(self.system, item['path'])
                if (not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected['blob']
                    or os.name == 'posix' and stat.S_IMODE(path.stat().st_mode) != expected['mode']):
                    raise ProvisionError('owned_files_changed')

    def finish(self, user, kernel):
        if self.journal.data.get('phase') == 'awaiting-reboot' and self.journal.data.get('boot_id') == self.system.path('/proc/sys/kernel/random/boot_id').read_text().strip():
            return dict(ok=True, pending=True, reboot_required=True, reboot_performed=False)
        self.event('verifying')
        from .camera_ready import wait_for_format, read_format
        self.system.command('/usr/bin/systemd-tmpfiles', '--create', '/etc/tmpfiles.d/spot-lepton.conf')
        import grp
        directory = self.system.path('/run/lock/spot-lepton').lstat()
        lock = self.system.path(LOCK).lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or directory.st_mode & 0o022
            or not stat.S_ISREG(lock.st_mode) or lock.st_uid != 0
            or lock.st_gid != grp.getgrnam('i2c').gr_gid or stat.S_IMODE(lock.st_mode) != 0o660):
            raise ProvisionError('unsafe_cci_lock')
        if self.system.command('/usr/bin/systemctl', 'is-active', 'lepton-streamer.service', check=False) != 'active':
            raise ProvisionError('streamer_not_active: inspect journal; no automatic restart')
        ready = wait_for_format(self.system, timeout=12)
        color = read_format('/dev/video11')
        if not ready.get('format_ready') or (color['width'], color['height'], color['fourcc']) != (640, 480, 'YUYV'):
            raise ProvisionError('capture_format_not_ready')
        # --status only reads camera configuration under the established CCI lock.
        status = self.system.command('/usr/bin/flock', '-w', '15', LOCK,
            '/usr/local/libexec/lepton/rpi_recovery_app', '--status', '--boot-timeout-ms', '6000', timeout=22)
        if 'temperature_contract=kelvin_x100' not in status:
            raise ProvisionError('radiometry_contract_unverified')
        binary = self.system.path(BINARY).read_bytes()
        self.write(RECORD, (json.dumps(dict(schema=1, commit=REVIEWED_COMMIT,
            binary_sha256=hashlib.sha256(binary).hexdigest(), protocol=PROTOCOL,
            attested_by='SPOT OS verified first-install source build'))+'\n').encode())
        cci = inspect(self.system, user)
        if not cci.get('compatible'):
            raise ProvisionError('live_cci_contract_failed')
        self.event('complete', confirmation=dict(kernel=kernel, source_commit=REVIEWED_COMMIT,
            raw=ready, color=color, radiometry_status=status, cci=cci,
            module_vermagic=self.system.command('/usr/sbin/modinfo', '-k', kernel, '-F', 'vermagic', 'lepton')))
        return dict(ok=True, provisioned=True, confirmation=self.journal.data['confirmation'], reboot_performed=False)

    def rollback(self):
        """Explicit interrupted-setup rollback only; never stops a live producer."""
        with admin_guard(self.system):
            self.journal = Journal(self.system, JOURNAL)
            if self.journal.data.get('source_commit') != REVIEWED_COMMIT:
                raise ProvisionError('no_owned_provisioning_journal')
            if self.system.command('/usr/bin/systemctl', 'is-active', 'lepton-streamer.service', check=False) == 'active':
                raise ProvisionError('rollback_refused_live_streamer')
            if self.system.path('/sys/module/lepton').exists():
                raise ProvisionError('rollback_refused_loaded_module: review manual recovery first')
            for item in self.journal.data['entries']:
                if 'link' in item.get('desired', {}):
                    secure_path(self.system, str(Path(item['path']).parent), directory=True)
                else:
                    secure_path(self.system, item['path'])
            if self.journal.data.get('dkms_attempted'):
                self.system.command('/usr/sbin/dkms', 'remove', '-m', 'lepton', '-v', DKMS_VERSION,
                    '-k', self.journal.data['kernel'], timeout=120)
            self.journal.restore()
            self.system.command('/usr/bin/systemctl', 'daemon-reload')
            return dict(ok=True, controlled_files_restored=True, packages_and_accounts_retained=True,
                source_cache_retained=True, reboot_performed=False)
