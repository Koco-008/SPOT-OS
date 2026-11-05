"""Two-phase, write-ahead, byte/metadata preserving system configuration journal.

Root filesystem injection is for tests only. Public CLI always operates on /.
Every controlled write is recorded BEFORE mutation. No user data, app releases,
network configuration, drivers, kernel packages or firmware are replaced.
"""
import base64
import contextlib
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

from .common import atomic, read
from .system import System, validate
from . import VERSION, REVISION

INSTALL = '/usr/local/lib/spot-os/'+VERSION
STATE = '/var/lib/spot-os'


class Journal:
    def __init__(self, system, name):
        self.system = system
        self.path = system.path(STATE + '/' + name + '.json')
        self.data = read(self.path, dict(schema=1, phase='new', entries=[]))

    def save(self):
        atomic(self.path, self.data)

    def blob(self, data):
        digest = hashlib.sha256(data).hexdigest()
        path = self.system.path(STATE+'/backups/'+digest)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            if path.is_symlink() or path.read_bytes() != data:
                raise ValueError('Backup blob collision/tampering')
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        return digest

    def contents(self, item):
        digest = item['blob']
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Invalid backup blob')
        path = self.system.path(STATE+'/backups/'+digest)
        if path.is_symlink():
            raise ValueError('Backup blob cannot be symlink')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Backup hash mismatch')
        return data

    def capture(self, name):
        if name in [e['path'] for e in self.data['entries']]:
            return
        path = self.system.path(name)
        # Parent symlinks may escape a test root or redirect writes on the real Pi.
        for parent in path.parents:
            if parent == self.system.root:
                break
            usr_merge = parent in [self.system.path('/lib'), self.system.path('/bin'), self.system.path('/sbin')]
            if parent.is_symlink() and not (usr_merge and parent.resolve().is_relative_to(self.system.path('/usr').resolve())):
                raise ValueError('Managed parent may not be symlink: ' + str(parent))
        item = dict(path=name, existed=path.exists() or path.is_symlink())
        if item['existed']:
            info = path.lstat()
            item.update(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
            if path.is_symlink():
                item['link'] = os.readlink(path)
            elif path.is_file():
                item['blob'] = self.blob(path.read_bytes())
            else:
                raise ValueError('Refusing to replace non-file: ' + name)
        self.data['entries'].append(item)
        self.save()

    def write(self, name, data, mode=0o644, link=None):
        self.capture(name)
        original = next(e for e in self.data['entries'] if e['path'] == name)
        # The Pi's FAT boot mount synthesizes modes (often 0755). Do not attempt
        # an unsupported chmod to ext4-style 0644 on an existing boot file.
        if name.startswith('/boot/') and original['existed'] and 'link' not in original:
            mode = original['mode']
        path = self.system.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name('.' + path.name + '.spot-os-new')
        if temporary.exists() or temporary.is_symlink():
            raise ValueError('Unexpected unfinished configuration write')
        if link is not None:
            temporary.symlink_to(link)
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, mode)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        if os.name == 'posix':
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        item = next(e for e in self.data['entries'] if e['path'] == name)
        item['desired'] = dict(link=link) if link is not None else dict(blob=self.blob(data), mode=mode)
        self.save()

    def restore(self):
        # Validate ALL original blobs before removing/changing any destination.
        for item in self.data['entries']:
            self.system.path(item['path'])
            if 'blob' in item:
                self.contents(item)
        self.data['phase'] = 'restoring'
        self.save()
        for item in reversed(self.data['entries']):
            path = self.system.path(item['path'])
            if self.matches_original(item):
                continue
            if item['existed']:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name('.'+path.name+'.spot-os-restore')
                temporary.unlink(missing_ok=True)
                if 'link' in item:
                    temporary.symlink_to(item['link'])
                    if hasattr(os, 'lchown'):
                        os.lchown(temporary, item['uid'], item['gid'])
                else:
                    with temporary.open('wb') as stream:
                        stream.write(self.contents(item))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.chmod(temporary, item['mode'])
                    if hasattr(os, 'chown'):
                        os.chown(temporary, item['uid'], item['gid'])
                os.replace(temporary, path)
            else:
                path.unlink(missing_ok=True)
            if os.name=='posix':
                descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
            # Leave parent directories: removing user-populated directories is unsafe.
        self.data['phase'] = 'restored'
        self.save()

    def matches_original(self,item):
        path=self.system.path(item['path'])
        if not item['existed']:return not (path.exists() or path.is_symlink())
        try:
            info=path.lstat()
            metadata=(stat.S_IMODE(info.st_mode),info.st_uid,info.st_gid)==(item['mode'],item['uid'],item['gid'])
            if 'link' in item:return metadata and path.is_symlink() and os.readlink(path)==item['link']
            return metadata and path.is_file() and not path.is_symlink() and path.read_bytes()==self.contents(item)
        except FileNotFoundError:return False

    def remove(self,name):
        self.capture(name)
        path=self.system.path(name)
        path.unlink(missing_ok=True)
        if os.name=='posix':
            descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        next(e for e in self.data['entries'] if e['path']==name)['desired']={'absent':True}
        self.save()


class UpgradeJournal(Journal):
    """Keep original pre-OS backups, but rollback a failed upgrade to 0.1.1."""
    def __init__(self, system, rollback):
        super().__init__(system, 'installation')
        self.rollback = rollback

    def capture(self, name):
        self.rollback.capture(name)
        super().capture(name)


class Installer:
    def __init__(self, system=None, source=Path(__file__).parent, *, target=None, online=False):
        self.system = system or System()
        self.source = Path(source)
        self.version,self.revision = target or (VERSION,REVISION)
        from .update_contract import version
        version(self.version)
        if not re.fullmatch("[a-z0-9-]{1,64}",self.revision):raise ValueError("OS revision")
        self.install_path = "/usr/local/lib/spot-os/"+self.version
        self.online = online

    def supported_previous(self,value):
        from .update_contract import version
        try:return value in ('0.1.1','0.1.2','0.1.3') or (0,1,4)<=version(value)<=version(self.version)
        except ValueError:return False

    def preflight(self, user, maintenance=True):
        from .assets import verify
        verify(self.source / 'artwork')
        return validate(self.system.inventory(user), maintenance, online=self.online)

    def require(self, user):
        result = self.preflight(user)
        if not result['ok']:
            raise RuntimeError('Preflight failed: ' + ', '.join(k for k, v in result['checks'].items() if not v))
        return result['device']

    def install(self, user):
        device = self.require(user)
        with self.system.maintenance_guard(device['prefix']):
            return self._install(user,device)

    def archive_journals(self):
        """Keep the exact previous journals AND their content-addressed backups.

        Validate every original blob before ANY rollback/migration write. History
        is append-only by digest; no backup referenced by it is garbage-collected.
        """
        journals = [Journal(self.system, name) for name in ('installation','activation','backlight')]
        for journal in journals:
            for item in journal.data['entries']:
                self.system.path(item['path'])
                if 'blob' in item:
                    journal.contents(item)
        archived = []
        paths=[j.path for j in journals]+[self.system.path(STATE+'/'+name+'.json') for name in ('boot-attempts','health')]
        for source in paths:
            if source.is_file():
                if source.is_symlink():raise ValueError('History source cannot be symlink')
                data = source.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                folder = self.system.path(STATE+'/history/'+digest)
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = folder/source.name
                if path.exists():
                    if path.is_symlink() or path.read_bytes() != data:
                        raise ValueError('History collision/tampering')
                else:
                    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                archived.append(str(path))
        return archived

    def require_stopped(self):
        text = self.system.command('/usr/bin/systemctl','show','spot-os-session.service',
            '-p','LoadState','-p','ActiveState','-p','MainPID')
        info = dict(line.split('=',1) for line in text.splitlines() if '=' in line)
        if (info.get('LoadState') not in ('loaded','not-found') or
                not info.get('MainPID','').isdigit() or not info.get('ActiveState')):
            raise RuntimeError('Cannot prove stopped session state; inspect systemd before maintenance')
        if info['ActiveState'] not in ('inactive','failed') or int(info['MainPID']) > 0:
            raise RuntimeError('Deactivate/stop the session before uninstall/migration; session still live')

    def migrate(self, user):
        """Explicit inactive/failed 0.1 -> restored original desktop -> 0.1.1.

        Deliberately leaves the new version PREPARED, not activated. On a failed
        new install the original desktop remains restored, not broken OS 0.1.
        """
        saved = read(self.system.path('/etc/spot-os/config.json'), {})
        if saved.get('os_version') in ('0.1.1', '0.1.2', '0.1.3', '0.1.4', self.version):
            return self.upgrade(user)
        self.require_stopped()
        device = self.require(user)
        saved = read(self.system.path('/etc/spot-os/config.json'), {})
        if saved and (saved.get('os_version') not in ('0.1', self.version) or saved.get('user') != user):
            raise RuntimeError('Unsupported OS version/user; no migration performed')
        with self.system.maintenance_guard(device['prefix']):
            history = self.archive_journals()
            if saved.get('os_version') == self.version:
                return self._install(user, device)
            self.uninstall()
            result = self._install(user, device)
            # An explicitly migrated fixed version gets a fresh bounded boot
            # budget; the old counters/health records have been archived above.
            atomic(self.system.path(STATE+'/boot-attempts.json'),dict(attempts=0))
            self.system.path('/run/spot-os/boot-blocked.json').unlink(missing_ok=True)
            result.update(migrated_from=saved.get('os_version'), history=history,
                          active=False, activation_required=True, reboot_performed=False)
            return result

    def upgrade(self, user):
        """Stopped service, active boot configuration retained, reversible switch.

        No uninstall/deactivate, service stop/start, apt, driver or boot-image
        rewrite. Build in the new version directory before the import shim is
        atomically replaced. A separate write-ahead journal backs up the exact
        previous configured state, including its uninstall/migration history.
        """
        self.require_stopped()
        device = self.require(user)
        with self.system.maintenance_guard(device['prefix']):
            rollback = Journal(self.system, 'upgrade')
            if rollback.data['phase'] not in ('new','committed','restored'):
                rollback.restore()
                self.system.command('/usr/bin/systemctl','daemon-reload')
            saved = read(self.system.path('/etc/spot-os/config.json'), {})
            if saved.get('os_version') == self.version and saved.get('os_revision') == self.revision:
                if saved.get('user') != user or not self.check(allow_transaction=True)['ok']:
                    raise RuntimeError('Current OS user/integrity differs; inspect check/repair before retry')
                return dict(upgraded=True, unchanged=True, active=self.check(allow_transaction=True)['active'])
            if not self.supported_previous(saved.get('os_version')) or saved.get('user') != user:
                raise RuntimeError('Upgrade requires configured SPOT OS 0.1.1/0.1.2/0.1.3 for the same user')
            if not self.check(allow_transaction=True)['ok']:
                raise RuntimeError('Previous OS integrity failed; repair 0.1.1 before upgrading')
            history = self.archive_journals()
            previous_version = saved['os_version']
            # Archive a previous completed upgrade instead of overwriting its evidence.
            if rollback.path.exists():
                data = rollback.path.read_bytes()
                atomic(self.system.path(STATE+'/history/'+hashlib.sha256(data).hexdigest()+'/upgrade.json'),
                    rollback.data)
            rollback.data = dict(schema=1, phase='upgrading', from_version=previous_version, to_version=self.version, to_revision=self.revision, entries=[])
            rollback.save()
            try:
                for name in ('installation','activation','backlight','boot-attempts','health'):
                    rollback.capture(STATE+'/'+name+'.json')
                # Activation/boot files are unchanged by an OTA. Capture only
                # its journal, not unrelated boot assets for redundant rewrites.
                journal = UpgradeJournal(self.system, rollback)
                result = self._install(user, device, journal=journal)
                atomic(self.system.path(STATE+'/boot-attempts.json'),dict(attempts=0))
                self.system.path('/run/spot-os/boot-blocked.json').unlink(missing_ok=True)
                if not self.check(allow_transaction=True)['ok']:
                    raise RuntimeError('New OS integrity failed')
                rollback.data['phase'] = 'committed'
                rollback.save()
                result.update(upgraded=True, from_version=previous_version, active=self.check(allow_transaction=True)['active'],
                    history=history, reboot_performed=False, session_started=False)
                return result
            except BaseException:
                rollback.restore()
                self.system.command('/usr/bin/systemctl','daemon-reload',check=False)
                raise

    def rollback_upgrade(self):
        self.require_stopped()
        config = read(self.system.path('/etc/spot-os/config.json'), {})
        with self.system.maintenance_guard(config['prefix']):
            rollback = Journal(self.system,'upgrade')
            if rollback.data['phase'] == 'restored':
                pending=read(self.system.path(STATE+'/updates/transaction.json'),{}).get('phase') in ('rollback-stopping','rollback-starting')
                return dict(rolled_back=not pending, files_restored=True, health_pending=pending, unchanged=True)
            if not self.supported_previous(rollback.data.get('from_version')):
                raise RuntimeError('No supported OS upgrade rollback available')
            transaction=self.system.path(STATE+'/updates/transaction.json')
            ota=read(transaction,{})
            pending=ota.get('phase')=='success' and ota.get('version')==rollback.data.get('to_version')
            if pending:
                # Administrative file rollback must not leave a stale OTA
                # success for the now-previous version. Only recovery/health
                # may subsequently declare this transaction rolled-back.
                ota.update(phase='rollback-stopping',message='Administrative Dateiwiederherstellung; Gesundheitsprüfung ausstehend',
                    previous=rollback.data['from_version'],rollback_attempts=0)
                atomic(transaction,ota)
            rollback.restore()
            self.system.command('/usr/bin/systemctl','daemon-reload')
            if pending:
                ota.update(phase='rollback-starting');atomic(transaction,ota)
            return dict(rolled_back=not pending, files_restored=True, health_pending=pending,
                version=rollback.data['from_version'], session_started=False, reboot_performed=False)

    def _install(self, user, device, journal=None):
        if (self.source/'PACKAGE.json').is_file():
            from .package import verify_package
            verify_package(self.source, expected=(self.version,self.revision))
        if not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user) or user == 'root':
            raise ValueError('Expected unprivileged local login user')
        upgrading = journal is not None
        journal = journal or Journal(self.system, 'installation')
        if not upgrading and journal.data['phase'] == 'prepared':
            saved = read(self.system.path('/etc/spot-os/config.json'))
            if saved['user'] != user:
                raise RuntimeError('Prepared for another user; uninstall first')
            if saved.get('os_version') != self.version:
                raise RuntimeError('Existing OS version differs; use the NEW package: python3 -m spot_os.cli migrate --user '+user)
            if saved.get('os_revision') != self.revision:
                raise RuntimeError('Existing OS candidate differs; use upgrade from the NEW package')
            return dict(prepared=True, unchanged=True)
        if not upgrading:
            if journal.data['phase'] not in ('new', 'restored'):
                journal.restore()
            self.archive_journals()
            journal.data = dict(schema=1, phase='preparing', entries=[])
        else:
            journal.data['phase'] = 'preparing'
        journal.save()
        # Public module/config parents must remain traversable by the session
        # even when an administrator installs with umask 0077. Private journals
        # and blobs always use their own explicit 0700/0600 modes.
        previous_mask = os.umask(0o022)
        try:
            if upgrading and self.version == '0.1.6':
                from .legacy_bridge import retire
                retire(self.system,journal)
            for path in sorted(self.source.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Package symlinks are not accepted')
                if (not path.is_file() or '__pycache__' in path.parts or 'tests' in path.relative_to(self.source).parts
                        or path.suffix == '.pyc' or path.name in ('spot-splash', 'presentation.h', 'presentation.c','foreign.h','foreign.c','PACKAGE.json')):
                    continue
                name = self.install_path + '/' + path.relative_to(self.source).as_posix()
                journal.write(name, path.read_bytes(), 0o755 if path.suffix == '.sh' or path.name == 'bus_daemon.py' else 0o644)
            # Build the small native Wayland client on the TARGET, not on Windows.
            for generated in ('spot-splash', 'presentation.h', 'presentation.c','foreign.h','foreign.c'):
                journal.capture(self.install_path+'/native/'+generated)
            self.system.command('/usr/bin/make', '-C', str(self.system.path(self.install_path + '/native')), timeout=120)
            native = self.system.path(self.install_path + '/native/spot-splash')
            if not native.is_file():
                raise RuntimeError('Native splash build did not produce executable')
            journal.write(self.install_path+'/native/spot-splash', native.read_bytes(), 0o755)
            config = dict(schema=1, os_version=self.version, os_revision=self.revision, user=user, uid=device['uid'], home=device['home'],
                          prefix=device['prefix'], kernel=device['kernel'], packages=device['packages'])
            journal.write('/etc/spot-os/config.json', ( __import__('json').dumps(config, indent=2)+'\n').encode())
            journal.write('/usr/local/bin/spot-os',
                b'#!/bin/sh\nexec /usr/bin/python3 -I /usr/local/lib/spot-os/admin_entry.py "$@"\n', 0o755)
            journal.write('/usr/local/lib/spot-os/admin_entry.py', (self.source/'admin_entry.py').read_bytes())
            # Versioned module dir uses spot_os import name: install a root-owned import shim.
            journal.write('/usr/local/lib/spot-os/spot_os', b'', link=self.version)
            unit = (self.source/'config/spot-os-session.service').read_text().replace('@USER@', user).replace('@HOME@', device['home'])
            journal.write('/etc/systemd/system/spot-os-session.service', unit.encode())
            journal.write('/etc/systemd/system/spot-os-session.service.d/user.conf', b'[Service]\nUMask=0077\n')
            for name in ('spot-os-prepare.service','spot-os-health.service','spot-os-failure.service','spot-os-lepton-ready.service','spot-os-preflight.service'):
                journal.write('/etc/systemd/system/'+name, (self.source/'config'/name).read_bytes())
            journal.write('/etc/pam.d/spot-os', (self.source/'config/pam-spot-os').read_bytes())
            for name in ('rc.xml', 'menu.xml', 'autostart', 'environment'):
                journal.write('/etc/spot-os/labwc/'+name, (self.source/'config/labwc'/name).read_bytes(),
                              0o755 if name == 'autostart' else 0o644)
            # Separate root updater; no GUI sudo path. Enabling does not start it here.
            for name in ('spot-os-update.service','spot-os-update.socket'):
                content=(self.source/'config'/name).read_text().replace('@GID@',str(device['gid']))
                journal.write('/etc/systemd/system/'+name,content.encode())
                target='sockets.target' if name.endswith('.socket') else 'multi-user.target'
                journal.write('/etc/systemd/system/'+target+'.wants/'+name,b'',link='/etc/systemd/system/'+name)
            # Pin observed package versions as evidence for reproduction; never guess Debian revisions.
            journal.write('/etc/spot-os/package-lock.json', (__import__('json').dumps(device['packages'], indent=2)+'\n').encode())
            self.system.command('/usr/bin/systemd-analyze', 'verify', '/etc/systemd/system/spot-os-session.service',
                '/etc/systemd/system/spot-os-prepare.service','/etc/systemd/system/spot-os-health.service',
                '/etc/systemd/system/spot-os-failure.service','/etc/systemd/system/spot-os-lepton-ready.service')
            journal.data['phase'] = 'prepared'
            journal.save()
            self.system.command('/usr/bin/systemctl', 'daemon-reload')
            return dict(prepared=True, active=False, device=device)
        except BaseException:
            if not upgrading:
                journal.restore()
            raise
        finally:
            os.umask(previous_mask)

    def activate(self, user):
        device = self.require(user)
        with self.system.maintenance_guard(device['prefix']):
            return self._activate(user)

    def _activate(self, user):
        prepared = Journal(self.system, 'installation')
        if prepared.data['phase'] != 'prepared':
            raise RuntimeError('Run install first')
        config = read(self.system.path('/etc/spot-os/config.json'))
        if config['user'] != user:
            raise ValueError('Wrong session user')
        if config.get('os_version') != self.version:
            raise RuntimeError('Migrate existing OS before activation')
        journal = Journal(self.system, 'activation')
        if journal.data['phase'] == 'active':
            return dict(active=True, unchanged=True, reboot_required=True)
        if journal.data['phase'] not in ('new', 'restored'):
            journal.restore()
        self.archive_journals()
        journal.data = dict(schema=1, phase='activating', entries=[])
        journal.save()
        try:
            cmd = self.system.path('/boot/firmware/cmdline.txt')
            tokens = cmd.read_text().split()
            prefixes = ('fullscreen_logo=', 'fullscreen_logo_name=', 'vt.global_cursor_default=',
                        'systemd.show_status=', 'loglevel=', 'rd.udev.log_level=')
            tokens = [v for v in tokens if v not in ('console=tty1', 'console=tty0', 'quiet', 'splash')
                      and not v.startswith(prefixes)]
            tokens += ['quiet', 'splash', 'loglevel=3', 'systemd.show_status=false', 'rd.udev.log_level=3',
                       'vt.global_cursor_default=0', 'fullscreen_logo=1', 'fullscreen_logo_name=spot-os.tga']
            journal.write('/boot/firmware/cmdline.txt', (' '.join(tokens)+'\n').encode())
            boot = self.system.path('/boot/firmware/config.txt').read_bytes()
            journal.write('/boot/firmware/config.txt', boot+('\n# SPOT OS v'+self.version+'\n[all]\nauto_initramfs=1\ndisable_splash=1\n').encode())
            journal.write('/lib/firmware/spot-os.tga', (self.source/'artwork/splash.tga').read_bytes())
            journal.write('/etc/initramfs-tools/hooks/spot-os-logo',
                b'#!/bin/sh\nset -e\nPREREQ=""\ncase "$1" in prereqs) echo "$PREREQ"; exit 0;; esac\n. /usr/share/initramfs-tools/hook-functions\nadd_firmware spot-os.tga\n', 0o755)
            for name in ('spot.plymouth', 'spot.script'):
                journal.write('/usr/share/plymouth/themes/spot/'+name, (self.source/'config/plymouth'/name).read_bytes())
            journal.write('/usr/share/plymouth/themes/spot/splash.png', (self.source/'artwork/splash.png').read_bytes())
            journal.write('/etc/plymouth/plymouthd.conf', b'[Daemon]\nTheme=spot\nShowDelay=0\nDeviceTimeout=8\n')
            journal.write('/etc/systemd/system/plymouth-quit.service.d/spot-os.conf',
                b'[Service]\nExecStart=\nExecStart=/usr/bin/plymouth quit --retain-splash\n')
            # Preserve masks/aliases byte-for-byte and leave the CURRENT desktop running.
            for name in ('display-manager.service', 'getty@tty1.service'):
                journal.write('/etc/systemd/system/'+name, b'', link='/dev/null')
            journal.write('/etc/systemd/system/graphical.target.wants/spot-os-session.service', b'',
                          link='/etc/systemd/system/spot-os-session.service')
            # Back up the exact existing generated initrds before regenerating.
            for name in ('initramfs','initramfs8','initramfs_2712','initramfs7','initramfs7l'):
                journal.capture('/boot/firmware/'+name)
            for kernel in self.system.path('/lib/modules').glob('*'):
                journal.capture('/boot/initrd.img-'+kernel.name)
            for parent, pattern in (('/boot', 'initrd.img-*'), ('/boot/firmware', 'initramfs*')):
                for path in sorted(self.system.path(parent).glob(pattern)):
                    if path.is_file() or path.is_symlink():
                        journal.capture('/'+path.relative_to(self.system.root).as_posix())
            self.system.command('/usr/sbin/update-initramfs', '-u', '-k', 'all', timeout=300)
            # Record outputs newly created by a distro initramfs hook, so rollback
            # can also remove them. Existing image bytes were captured above.
            for parent, pattern in (('/boot', 'initrd.img-*'), ('/boot/firmware', 'initramfs*')):
                known = {item['path'] for item in journal.data['entries']}
                for path in self.system.path(parent).glob(pattern):
                    name = '/'+path.relative_to(self.system.root).as_posix()
                    if name not in known:
                        journal.data['entries'].append(dict(path=name, existed=False))
            journal.save()
            self.system.command('/usr/bin/systemctl', 'daemon-reload')
            # Verify journal can really be decoded before declaring activation complete.
            for item in journal.data['entries']:
                if 'blob' in item:
                    journal.contents(item)
            journal.data['phase'] = 'active'
            journal.save()
            return dict(active=True, reboot_required=True, reboot_performed=False)
        except BaseException:
            journal.restore()
            self.system.command('/usr/bin/systemctl', 'daemon-reload', check=False)
            raise

    def deactivate(self):
        backlight = Journal(self.system, 'backlight')
        if backlight.data['phase'] != 'new':
            backlight.restore()
        journal = Journal(self.system, 'activation')
        if journal.data['phase'] != 'new':
            journal.restore()
        self.system.command('/usr/bin/systemctl', 'daemon-reload')
        return dict(active=False, reboot_required=True, live_session_stopped=False)

    def uninstall(self):
        # Do not unmap/kill a live app, recording or Handoff. Deactivate first, reboot to desktop.
        self.require_stopped()
        self.archive_journals()
        self.deactivate()
        journal = Journal(self.system, 'installation')
        if journal.data['phase'] != 'new':
            journal.restore()
        self.system.command('/usr/bin/systemctl', 'daemon-reload')
        return dict(uninstalled=True, app_untouched=True, audit_backups_retained=STATE)

    def check(self, *, allow_transaction=False):
        installation = Journal(self.system, 'installation')
        activation = Journal(self.system, 'activation')
        bridge = Journal(self.system,'ota-bridge-installation')
        damaged = []
        for journal in (installation, activation,bridge):
            if journal.data['phase'] not in ('prepared', 'active'):
                continue
            for item in journal.data['entries']:
                desired = item.get('desired')
                if not desired:
                    continue
                path = self.system.path(item['path'])
                if desired.get('absent'):
                    valid=not (path.exists() or path.is_symlink())
                elif 'link' in desired:
                    valid = path.is_symlink() and os.readlink(path) == desired['link']
                else:
                    valid = (path.is_file() and not path.is_symlink()
                        and path.read_bytes() == journal.contents(desired)
                        and stat.S_IMODE(path.stat().st_mode) == desired['mode'])
                if not valid:
                    damaged.append(item['path'])
        upgrade=Journal(self.system,'upgrade').data['phase']
        transaction=read(self.system.path(STATE+'/updates/transaction.json'),{})
        ota=transaction.get('phase','idle')
        config=read(self.system.path('/etc/spot-os/config.json'),{})
        complete=installation.data['phase']=='prepared' and activation.data['phase'] in ('new','restored','active')
        upgrade_complete=upgrade in ('new','committed','restored') or (allow_transaction and upgrade=='upgrading')
        bridge_phase=bridge.data['phase']
        bridge_complete=bridge_phase in ('new','prepared','retired','restored')
        bridge_complete &= (bridge_phase!='prepared' or self.system.path(STATE+'/ota-bridge.json').is_file())
        from .update_engine import TERMINAL
        transaction_complete=ota in TERMINAL-{'recovery-failed'} and not self.system.path(STATE+'/updates/session-blocked').exists()
        if ota in ('success','rolled-back'):
            expected=transaction.get('version' if ota=='success' else 'previous')
            transaction_complete &= bool(expected) and config.get('os_version')==expected
        return dict(prepared=installation.data['phase'] == 'prepared', active=activation.data['phase'] == 'active',
                    installation_phase=installation.data['phase'], activation_phase=activation.data['phase'],
                    files_ok=not damaged and installation.data['phase']=='prepared', installation_complete=complete and upgrade_complete and bridge_complete,
                    bridge_phase=bridge_phase,
                    upgrade_phase=upgrade, transaction_phase=ota, transaction_complete=transaction_complete,
                    ok=not damaged and complete and upgrade_complete and bridge_complete and (allow_transaction or transaction_complete), damaged=damaged,
                    config=config)

    def repair(self, user):
        device = self.require(user)
        with self.system.maintenance_guard(device['prefix']):
            return self._repair()

    def _repair(self):
        recovered = []
        changed_boot = False
        for name in ('activation', 'installation'):
            journal = Journal(self.system, name)
            if journal.data['phase'] not in ('new', 'prepared', 'active', 'restored'):
                journal.restore()
                recovered.append(name)
            elif journal.data['phase'] in ('prepared', 'active'):
                for item in journal.data['entries'][:]:
                    desired = item.get('desired')
                    if not desired:
                        continue
                    if desired.get('absent'):
                        journal.remove(item['path'])
                        continue
                    journal.write(item['path'], journal.contents(desired) if 'blob' in desired else b'',
                                  desired.get('mode', 0o644), desired.get('link'))
                    changed_boot |= name == 'activation'
        if changed_boot:
            self.system.command('/usr/sbin/update-initramfs', '-u', '-k', 'all', timeout=300)
        self.system.command('/usr/bin/systemctl', 'daemon-reload')
        return dict(recovered=recovered, repaired=True, check=self.check())
