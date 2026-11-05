import base64
import copy
from pathlib import Path
import os
import pytest
from contextlib import nullcontext
from spot_os.common import read
from spot_os.installer import Installer, Journal, INSTALL, STATE
from spot_os.system import System, validate


def device():
    return dict(root=True, user='admin', uid=1000, gid=1000, home='/home/admin', prefix='/home/admin/.local/share/spot',
        model='Raspberry Pi 5 Model B', os='13', arch='aarch64', kernel='test-kernel',
        displays=[dict(name='DSI-1', modes=['800x480'])], touches=['11-0038 generic ft5x06 (79)'],
        backlight=dict(driver='rpi_touchscreen_attiny'), drm=True, packages=dict(labwc='0.8.4'),
        launcher=True, producer_gated=True, qt=True, qt_network=True, lepton_cci=dict(compatible=True), app='0.28.12', app_locked=False, update_locked=False,
        lepton='active', ssh='active', ssh_connection=True, free=2**31, boot_free=2**30,
        backup_bytes=12, initramfs=True, fullscreen_support=True, default_target='graphical.target',
        kernel_logo=True,lepton_driver=True)


class FakeSystem(System):
    def __init__(self, root):
        super().__init__(root)
        self.data = device()
        self.calls = []
        self.fail = None
        self.live = False

    def inventory(self, user):
        return copy.deepcopy(self.data)

    def maintenance_guard(self, prefix):
        return nullcontext()

    def command(self, *args, check=True, timeout=30):
        self.calls.append(args)
        if self.fail and self.fail in args[0]:
            raise RuntimeError('injected command failure')
        if args[0].endswith('make'):
            folder = Path(args[2])
            for name in ('spot-splash', 'presentation.h', 'presentation.c','foreign.h','foreign.c'):
                (folder/name).write_bytes(b'native-build')
        if 'is-active' in args:
            return 'active' if self.live else 'inactive'
        if 'show' in args and 'LoadState' in args:
            return 'LoadState=loaded\nActiveState='+('active' if self.live else 'inactive')+'\nMainPID=0'
        return ''


@pytest.fixture
def system(tmp_path):
    result = FakeSystem(tmp_path)
    for name, data in [('/boot/firmware/cmdline.txt', b'console=serial0,115200 console=tty1 root=PARTUUID=abc rw splash\n'),
        ('/boot/firmware/config.txt', b'[all]\nauto_initramfs=1\n'), ('/boot/initrd.img-test', b'original-initramfs'),
        ('/etc/plymouth/plymouthd.conf', b'[Daemon]\nTheme=pix\n')]:
        path = result.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return result


@pytest.mark.parametrize('key,value', [('model','PC'), ('os','12'), ('arch','x86_64'), ('displays',[]),
    ('touches',[]), ('drm',False), ('packages',{}), ('launcher',False), ('qt',False),
    ('lepton','inactive'), ('ssh','inactive'), ('ssh_connection',False), ('free',10),
    ('initramfs',False), ('fullscreen_support',False), ('app_locked',True), ('update_locked',True)])
def test_preflight_fails_closed(key, value):
    data = device()
    data[key] = value
    assert not validate(data)['ok']


@pytest.mark.skipif(os.name != 'posix', reason='Linux installer symlinks and Unix modes')
def test_prepare_activate_idempotent_restore_exactly(system):
    i = Installer(system)
    original = {p.relative_to(system.root).as_posix(): p.read_bytes() for p in system.root.rglob('*') if p.is_file()}
    assert i.install('admin')['prepared']
    assert not system.path('/etc/systemd/system/display-manager.service').exists()
    assert i.install('admin')['unchanged']
    assert i.activate('admin')['reboot_performed'] is False
    assert system.path('/etc/systemd/system/display-manager.service').is_symlink()
    assert i.activate('admin')['unchanged']
    cmd = system.path('/boot/firmware/cmdline.txt').read_text()
    assert 'console=serial0,115200' in cmd and 'console=tty1' not in cmd
    assert i.check()['ok']
    i.deactivate()
    for name, data in original.items():
        assert system.path('/'+name).read_bytes() == data
    i.uninstall()
    assert not system.path('/usr/local/bin/spot-os').exists()
    assert not system.path(INSTALL+'/native/spot-splash').exists()
    assert not any('reboot' in args or 'stop' in args for args in system.calls)


@pytest.mark.skipif(os.name != 'posix', reason='Linux installer symlinks and Unix modes')
def test_failed_build_and_interrupted_journal_repair(system):
    i = Installer(system)
    system.fail = 'make'
    with pytest.raises(RuntimeError): i.install('admin')
    assert Journal(system, 'installation').data['phase'] == 'restored'
    system.fail = None
    i.install('admin')
    j = Journal(system, 'activation')
    j.data['phase'] = 'activating'
    j.write('/boot/firmware/cmdline.txt', b'broken')
    i.repair('admin')
    assert b'console=serial0' in system.path('/boot/firmware/cmdline.txt').read_bytes()


@pytest.mark.skipif(os.name != 'posix', reason='Linux installer symlinks and Unix modes')
def test_initramfs_failure_reverses_all_boot_configuration(system):
    i = Installer(system)
    i.install('admin')
    original = system.path('/boot/firmware/cmdline.txt').read_bytes()
    system.fail = 'update-initramfs'
    with pytest.raises(RuntimeError): i.activate('admin')
    assert system.path('/boot/firmware/cmdline.txt').read_bytes() == original
    assert not system.path('/etc/systemd/system/display-manager.service').is_symlink()
    assert system.path('/boot/initrd.img-test').read_bytes() == b'original-initramfs'


@pytest.mark.skipif(os.name != 'posix', reason='Linux installer symlinks and Unix modes')
def test_repair_damaged_config_and_refuse_live_uninstall(system):
    i = Installer(system)
    i.install('admin')
    path = system.path('/etc/spot-os/labwc/rc.xml')
    original = path.read_bytes()
    path.write_bytes(b'broken')
    assert not i.check()['ok']
    assert i.repair('admin')['check']['ok']
    assert path.read_bytes() == original
    system.live = True
    with pytest.raises(RuntimeError, match='Deactivate'): i.uninstall()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX symlink/owner metadata')
def test_symlink_and_metadata_restore(system):
    path = system.path('/etc/systemd/system/display-manager.service')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to('/lib/systemd/system/lightdm.service')
    journal = Journal(system, 'metadata')
    journal.write('/etc/systemd/system/display-manager.service', b'', link='/dev/null')
    journal.restore()
    assert os.readlink(path) == '/lib/systemd/system/lightdm.service'
