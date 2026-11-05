"""Read-only device inventory, with injectable roots/runner for installer tests."""
import json
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess


class System:
    def __init__(self, root=Path('/'), runner=subprocess.run):
        self.root, self.runner = Path(root), runner

    def path(self, path):
        value = PurePosixPath(path)
        if not value.is_absolute() or '..' in value.parts:
            raise ValueError('System paths must be absolute without traversal')
        return self.root / str(value).lstrip('/')

    def command(self, *args, check=True, timeout=30):
        result = self.runner(list(args), capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError('Command failed: ' + args[0])
        return result.stdout.strip() if result.returncode == 0 else ''

    @contextmanager
    def maintenance_guard(self, prefix):
        """Lock existing launcher files in the same order as the signed installer."""
        import fcntl
        handles=[]
        try:
            for name in ('update.lock','app.lock'):
                path=Path(prefix)/name
                if not path.is_file() or path.is_symlink():
                    raise RuntimeError('Launcher locks missing; start the stable launcher once and close it normally')
                stream=path.open('rb')
                handles.append(stream)
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        except BlockingIOError as exc:
            raise RuntimeError('Application/update became active; no OS changes performed') from exc
        finally:
            for stream in reversed(handles):
                stream.close()

    def inventory(self, user):
        import pwd
        account = pwd.getpwnam(user)
        prefix = Path(account.pw_dir) / '.local/share/spot'
        model = self.path('/proc/device-tree/model').read_text().rstrip('\0')
        release = dict(line.split('=', 1) for line in self.path('/etc/os-release').read_text().splitlines() if '=' in line)
        displays = []
        for connector in self.path('/sys/class/drm').glob('card*-*'):
            if (connector / 'status').is_file() and (connector / 'status').read_text().strip() == 'connected':
                displays.append(dict(name=connector.name, modes=(connector / 'modes').read_text().split()))
        touches = []
        for device in self.path('/sys/class/input').glob('event*/device'):
            if int((device / 'properties').read_text().strip(), 16) & 2:
                touches.append((device / 'name').read_text().strip())
        backlight = self.path('/sys/class/backlight/11-0045')
        driver = next((parent.joinpath('driver').resolve().name for parent in
                       (backlight.resolve(), *backlight.resolve().parents)
                       if parent.joinpath('driver').is_symlink()), '')
        # Record only installed versions; never silently apt-upgrade the Pi.
        package_file = Path(__file__).with_name('packages.txt')
        packages = {}
        for name in package_file.read_text().splitlines():
            if name and not name.startswith('#'):
                packages[name] = self.command('/usr/bin/dpkg-query', '-W', '-f=${Version}', name, check=False)
        from .common import read
        from .manager import locked
        current = read(prefix / 'current.json', {})
        version = current.get('version', '')
        if not version or any(c not in '0123456789.' for c in version):
            raise ValueError('No valid installed application')
        folder = prefix / 'releases' / version
        internal = read(folder / 'release.json', {})
        from .reader_contract import inspect as inspect_reader
        producer_gated=inspect_reader(folder,internal)
        import hashlib
        launch_valid = (prefix / 'bin/spot').is_file() and os.access(prefix / 'bin/spot', os.X_OK)
        for name in ('SPOT_v27.py', 'tools/launch.py', 'spot_handoff.py', 'spot_runtime.py'):
            launch_valid &= internal.get('files', {}).get(name) == hashlib.sha256((folder / name).read_bytes()).hexdigest()
        launch_valid &= (prefix / 'bin/launch.py').read_bytes() == (folder / 'tools/launch.py').read_bytes()
        # Real interpreter and Qt Wayland plugin, without opening a camera or GUI.
        qt = self.command('/usr/sbin/runuser', '-u', user, '--', current['python'], '-c',
            "from PySide6.QtCore import QLibraryInfo;from pathlib import Path;"
            "p=Path(QLibraryInfo.path(QLibraryInfo.PluginsPath));"
            "assert list((p/'platforms').glob('*wayland*'));print('qt-wayland-ok')", check=False)
        qt_network = qt_network_probe(self, user, current['python'])
        from .lepton import inspect
        cci = inspect(self, user)
        kernel = platform.release()
        from .boot_facts import inspect as inspect_boot
        boot = inspect_boot(self)
        logo_config = boot['kernel_logo']
        service_text = self.command('/usr/bin/systemctl','cat','lepton-streamer.service',check=False)
        initrds = list(self.path('/boot').glob('initrd.img-*')) + list(self.path('/boot/firmware').glob('initramfs*'))
        need_backup = sum(p.stat().st_size for p in initrds if p.is_file())
        session = self.command('/usr/bin/loginctl', 'show-user', user, '-p', 'Sessions', '--value', check=False)
        processes=[]
        for proc in self.path('/proc').glob('[0-9]*'):
            try:
                args=(proc/'cmdline').read_bytes().split(b'\0')
                if any(Path(os.fsdecode(arg)).name.lower().startswith('spot_v') and arg.endswith(b'.py') for arg in args):
                    processes.append(int(proc.name))
            except (OSError,UnicodeError):
                continue
        return dict(user=user, uid=account.pw_uid, gid=account.pw_gid, home=account.pw_dir,
            prefix=str(prefix), model=model, os=release.get('VERSION_ID', '').strip('"'),
            arch=platform.machine(), kernel=kernel, displays=displays, touches=touches,
            backlight=dict(name='11-0045', driver=driver, value=int((backlight/'brightness').read_text()),
                           maximum=int((backlight/'max_brightness').read_text())),
            drm=bool(list(self.path('/dev/dri').glob('card*'))), packages=packages,
            launcher=bool(launch_valid), producer_gated=producer_gated, qt=qt == 'qt-wayland-ok', qt_network=qt_network, lepton_cci=cci, app=version,
            app_locked=locked(prefix / 'app.lock'), update_locked=locked(prefix / 'update.lock'),
            transaction_state=read(prefix/'transaction.json',{}).get('state'),
            lepton=self.command('/usr/bin/systemctl', 'is-active', 'lepton-streamer.service', check=False),
            ssh=self.command('/usr/bin/systemctl', 'is-active', 'ssh.service', check=False),
            ssh_connection=bool(os.environ.get('SSH_CONNECTION')),
            free=shutil.disk_usage(self.path('/var/lib')).free, backup_bytes=need_backup,
            boot_free=shutil.disk_usage(self.path('/boot/firmware')).free,
            initramfs=boot['initramfs'], boot_prerequisites=boot,
            fullscreen_support=self.path('/usr/share/rpi-splash-screen-support/default-hook.sh').exists(),
            kernel_logo=logo_config,
            lepton_driver=all(marker in service_text for marker in
                ('/usr/local/libexec/lepton/rpi_recovery_app --configure --boot-timeout-ms 6000',
                 '/usr/local/libexec/lepton/recover_lepton_rpi5.sh')),
            root=os.geteuid() == 0, sessions=session, app_processes=processes,
            default_target=self.command('/usr/bin/systemctl','get-default',check=False))


def validate(data, maintenance=True, *, online=False):
    checks = {
        'root': data.get('root') is True,
        'raspberry_pi5': 'Raspberry Pi 5' in data.get('model', ''),
        'debian13_aarch64': data.get('os') == '13' and data.get('arch') == 'aarch64',
        'single_dsi_800x480': len(data.get('displays', [])) == 1 and 'DSI' in data['displays'][0]['name'] and '800x480' in data['displays'][0]['modes'],
        'ft5x06_touch': any('ft5x06' in name.lower() for name in data.get('touches', [])),
        'backlight': data.get('backlight', {}).get('driver') == 'rpi_touchscreen_attiny',
        'drm': data.get('drm') is True,
        'packages': bool(data.get('packages')) and all(data['packages'].values()),
        'launcher': data.get('launcher') is True,
        'producer_gated_reader': data.get('producer_gated') is True,
        'qt_wayland': data.get('qt') is True,
        'qt_network': data.get('qt_network') is True,
        'lepton_cci': data.get('lepton_cci', {}).get('compatible') is True,
        'lepton_service': data.get('lepton') == 'active',
        'lepton_driver': data.get('lepton_driver') is True,
        'ssh_maintenance': data.get('ssh') == 'active' and (online or data.get('ssh_connection') is True),
        'space': data.get('free', 0) >= max(512 * 1024**2, data.get('backup_bytes', 0) * 2 + 128 * 1024**2),
        'boot_space': data.get('boot_free', 0) >= 128 * 1024**2,
        'early_splash': data.get('fullscreen_support') is True and data.get('initramfs') is True,
        'kernel_logo_kms': data.get('kernel_logo') is True,
        'graphical_target': data.get('default_target') == 'graphical.target',
        'no_app_or_update': not maintenance or not (data.get('app_locked') or data.get('update_locked') or data.get('app_processes')),
        'no_pending_transaction': not maintenance or data.get('transaction_state') in (None,'committed','recovered','rolled-back'),
    }
    return dict(ok=all(checks.values()), checks=checks, device=data)


def qt_network_probe(system, user, interpreter):
    return system.command('/usr/sbin/runuser', '-u', user, '--', interpreter, '-c',
        "from PySide6.QtNetwork import QLocalSocket; s=QLocalSocket();print('qt-network-ok')",
        check=False) == 'qt-network-ok'
