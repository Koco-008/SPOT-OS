"""Filesystem/inventory fault injection. Real PAM/Wayland is tested separately."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import pytest
from spot_os import VERSION, lepton, ownership
from spot_os.common import atomic, read
from spot_os.installer import Installer, Journal, STATE
from spot_os.system import System, validate, qt_network_probe
from test_installer import system, device, FakeSystem

ROOT = Path(__file__).resolve().parents[2]
BASE = '4c31b0f27f7ce9c90bd57d73f31669b63af59719'


@pytest.fixture(scope='module')
def previous(tmp_path_factory):
    folder = tmp_path_factory.mktemp('actual-os-011')/'spot_os'
    git = ['git','-c','safe.directory='+ROOT.as_posix()]
    names = subprocess.run(git+['ls-tree','-r','--name-only',BASE,'spot_os'],
        cwd=ROOT,capture_output=True,text=True,check=True).stdout.splitlines()
    for name in names:
        path = folder.parent/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(subprocess.run(git+['show',BASE+':'+name],cwd=ROOT,
            capture_output=True,check=True).stdout)
    spec = importlib.util.spec_from_file_location('spot_os._previous_installer',folder/'installer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Relative imports use the new fake inventory, but the actual previous
    # installer and all installed previous source bytes come from its commit.
    module.VERSION = '0.1.1'
    module.INSTALL = '/usr/local/lib/spot-os/0.1.1'
    return module.Installer, folder


def snapshot(system):
    result = {}
    for path in system.root.rglob('*'):
        name = '/'+path.relative_to(system.root).as_posix()
        if name.startswith(STATE+'/'):
            continue  # append-only backups/history are deliberately retained
        if path.is_symlink():result[name] = ('link',os.readlink(path))
        elif path.is_file():result[name] = ('file',path.read_bytes(),path.stat().st_mode & 0o777)
    return result


@pytest.mark.skipif(os.name!='posix',reason='Target Unix ownership and symlink fidelity')
@pytest.mark.parametrize('active',[False,True])
def test_actual_011_upgrade_preserves_boot_history_and_full_rollback(system, previous, active):
    cls, source = previous
    old = cls(system,source);old.install('admin')
    if active:old.activate('admin')
    sentinels = ['/home/admin/DCIM/video.raw','/home/admin/.config/spot/calibration.json',
        '/home/admin/.local/share/spot/releases/0.28.10/SPOT_v27.py','/etc/ssh/sshd_config',
        '/etc/systemd/system/lepton-streamer.service','/var/lib/spot-os/history/old/migration.json']
    for name in sentinels:
        path = system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'protected')
    before = snapshot(system)
    journals = {name:system.path(STATE+'/'+name+'.json').read_bytes()
        for name in ('installation','activation') if system.path(STATE+'/'+name+'.json').exists()}
    new = Installer(system)
    result = new.upgrade('admin')
    assert result['upgraded'] and result['active'] == active and not result['session_started']
    assert read(system.path('/etc/spot-os/config.json'))['os_version'] == VERSION
    assert os.readlink(system.path('/usr/local/lib/spot-os/spot_os')) == VERSION
    assert new.check()['ok'] and new.install('admin')['unchanged'] and new.upgrade('admin')['unchanged']
    if active: assert new.activate('admin')['unchanged']
    for name in sentinels:assert system.path(name).read_bytes() == b'protected'
    new.rollback_upgrade()
    assert snapshot(system) == before
    for name, data in journals.items():assert system.path(STATE+'/'+name+'.json').read_bytes() == data
    assert new.rollback_upgrade()['unchanged']
    assert not any('reboot' in args or 'stop' in args or 'restart' in args for args in system.calls)


@pytest.mark.skipif(os.name!='posix',reason='Target filesystem transaction')
@pytest.mark.parametrize('failure',['make','systemd-analyze','daemon-reload'])
def test_upgrade_failure_restores_exact_previous_active_011(system, previous, failure):
    cls, source = previous
    old = cls(system,source);old.install('admin');old.activate('admin')
    before = snapshot(system)
    journals = {n:system.path(STATE+'/'+n+'.json').read_bytes() for n in ('installation','activation')}
    system.fail = failure
    if failure == 'daemon-reload':
        original = system.command
        def command(*args,**kwargs):
            if failure in args and kwargs.get('check',True):raise RuntimeError('injected reload failure')
            return original(*args,**kwargs)
        system.command = command
    with pytest.raises(RuntimeError):Installer(system).upgrade('admin')
    assert snapshot(system) == before
    for name,data in journals.items():assert system.path(STATE+'/'+name+'.json').read_bytes() == data
    assert list(system.path(STATE+'/history').glob('*/installation.json'))
    assert Journal(system,'upgrade').data['phase'] == 'restored'


@pytest.mark.skipif(os.name!='posix',reason='Power-loss journal simulation on target filesystem')
def test_interrupted_upgrade_is_restored_before_retry(system,previous):
    cls,source=previous
    old=cls(system,source);old.install('admin');old.activate('admin')
    before=snapshot(system)
    # Simulate SIGKILL/power loss between write-ahead capture and final commit.
    # No exception cleanup is run; the next command must recover the journal.
    transaction=Journal(system,'upgrade')
    transaction.data.update(phase='upgrading',from_version='0.1.1',to_version=VERSION)
    transaction.save()
    transaction.capture(STATE+'/installation.json')
    transaction.write('/usr/local/lib/spot-os/spot_os',b'',link=VERSION)
    transaction.write('/etc/spot-os/config.json',('{"os_version":"'+VERSION+'","user":"admin"}').encode())
    assert read(system.path('/etc/spot-os/config.json'))['os_version']==VERSION
    new=Installer(system)
    assert new.upgrade('admin')['upgraded'] and new.check()['ok']
    new.rollback_upgrade()
    assert snapshot(system)==before


@pytest.mark.skipif(os.name!='posix',reason='Root Unix mode checks run in real CI helper')
def test_runtime_under_umask_repairs_existing_without_exposing_private_data(tmp_path, monkeypatch):
    from spot_os.runtime import publish_boot_status
    folder = tmp_path/'public'
    folder.mkdir(mode=0o700)
    # Unit test injects root ownership; run_units also tests a real root-owned
    # directory and actual unprivileged reads under systemd UMask=0077.
    original = Path.lstat
    def lstat(path,*args,**kwargs):
        info = original(path,*args,**kwargs)
        if path == folder:
            values = list(info);values[4] = 0;return os.stat_result(values)
        return info
    monkeypatch.setattr(Path,'lstat',lstat)
    mask = os.umask(0o077)
    try:
        for _ in range(3):publish_boot_status(dict(attempts=1,boot='test',blocked=False,secret='private'),folder)
    finally:os.umask(mask)
    assert folder.stat().st_mode & 0o777 == 0o711
    assert (folder/'boot-blocked.json').stat().st_mode & 0o777 == 0o644
    assert 'secret' not in read(folder/'boot-blocked.json')


def test_qt_network_missing_in_actual_interpreter_even_with_installed_package():
    data = device();data['packages']['python3-pyside6.qtnetwork']='6.8.2';data['qt_network']=False
    assert not validate(data)['checks']['qt_network']
    calls=[]
    class Probe:
        def command(self,*args,**kwargs):calls.append(args);return ''
    assert not qt_network_probe(Probe(),'admin','/home/admin/custom-python')
    assert calls[0][4:6] == ('/home/admin/custom-python','-c')
    assert 'from PySide6.QtNetwork import QLocalSocket' in calls[0][-1]
    data = device();data['packages']['python3-pyside6.qtnetwork']=''
    assert not validate(data)['checks']['packages']


@pytest.mark.parametrize('changed,value', [('Service','sshd'),('User','2000'),('State','active'),
    ('Scope','session-other.scope'),('Leader','999'),('TTY','tty2'),('TimestampMonotonic','0')])
def test_cleanup_never_touches_foreign_live_or_uncertain_session(tmp_path, monkeypatch, changed, value):
    config = dict(uid=1000,prefix='/home/admin/.local/share/spot')
    info = dict(User='1000',Leader='123',Service='spot-os',Seat='seat0',VTNr='1',TTY='tty1',
        Type='wayland',State='closing',Scope='session-7.scope',TimestampMonotonic='1000001')
    info[changed] = value
    class Fake(FakeSystem):
        def command(self,*args,**kwargs):
            self.calls.append(args)
            return 'MainPID=0\nExecMainPID=123\nInvocationID=inv\nActiveState=failed\nExecMainStartTimestampMonotonic=1000000'
    fake = Fake(tmp_path)
    boot = fake.path('/proc/sys/kernel/random/boot_id');boot.parent.mkdir(parents=True);boot.write_text('boot')
    monkeypatch.setattr(ownership,'sessions',lambda _: [('7',info)])
    with pytest.raises(RuntimeError):ownership.cleanup(fake,config)
    assert not any('stop' in args for args in fake.calls)


def test_cleanup_empty_own_scope_preserves_same_uid_ssh(tmp_path, monkeypatch):
    config = dict(uid=1000,prefix='/home/admin/.local/share/spot')
    info = dict(User='1000',Leader='123',Service='spot-os',Seat='seat0',VTNr='1',TTY='tty1',
        Type='wayland',State='closing',Scope='session-7.scope',TimestampMonotonic='1000001')
    ssh = dict(info,VTNr='0',Seat='',TTY='pts/8',Service='sshd',State='active',Scope='session-8.scope')
    group = '/user.slice/user-1000.slice/session-7.scope'
    fake = FakeSystem(tmp_path)
    boot = fake.path('/proc/sys/kernel/random/boot_id');boot.parent.mkdir(parents=True);boot.write_text('boot')
    members = fake.path('/sys/fs/cgroup'+group+'/cgroup.procs');members.parent.mkdir(parents=True);members.write_text('')
    def props(_system, tool, name, fields):
        return (dict(ControlGroup=group,LoadState='loaded',ActiveState='inactive') if name == 'session-7.scope' else
            dict(MainPID='0',ExecMainPID='123',InvocationID='inv',ActiveState='failed',ExecMainStartTimestampMonotonic='1000000'))
    monkeypatch.setattr(ownership,'properties',props)
    monkeypatch.setattr(ownership,'sessions',lambda _: [('7',info),('8',ssh)])
    assert ownership.cleanup(fake,config)['cleaned'] == ['session-7.scope']
    assert [args for args in fake.calls if 'stop' in args] == [('/usr/bin/systemctl','stop','session-7.scope')]


@pytest.mark.skipif(os.name!='posix',reason='Root ledger Unix permissions test double')
@pytest.mark.parametrize('current_boot',['same-boot','next-boot'])
def test_root_session_ledger_survives_new_invocation_but_never_a_new_boot(tmp_path,monkeypatch,current_boot):
    config=dict(uid=1000,prefix='/home/admin/.local/share/spot')
    info=dict(User='1000',Leader='123',Service='spot-os',Seat='seat0',VTNr='1',TTY='tty1',
        Type='wayland',State='active',Scope='session-7.scope',TimestampMonotonic='1000001')
    fake=FakeSystem(tmp_path)
    boot=fake.path('/proc/sys/kernel/random/boot_id');boot.parent.mkdir(parents=True);boot.write_text('same-boot')
    group='/user.slice/user-1000.slice/session-7.scope'
    members=fake.path('/sys/fs/cgroup'+group+'/cgroup.procs');members.parent.mkdir(parents=True);members.write_text('')
    unit=dict(MainPID='123',InvocationID='previous',ActiveState='active')
    def properties(_system,tool,name,fields):
        return dict(ControlGroup=group,LoadState='loaded',ActiveState='inactive') if name==info['Scope'] else unit
    monkeypatch.setattr(ownership,'properties',properties)
    monkeypatch.setattr(ownership,'sessions',lambda _: [('7',info)])
    assert ownership.remember(fake,config)['recorded']=='7'
    ledger=fake.path(ownership.LEDGER)
    actual=Path.lstat
    def lstat(path,*args,**kwargs):
        result=actual(path,*args,**kwargs)
        if path==ledger:
            values=list(result);values[4]=0;return os.stat_result(values)
        return result
    monkeypatch.setattr(Path,'lstat',lstat)
    unit.update(MainPID='0',ExecMainPID='0',InvocationID='next-invocation',ActiveState='activating')
    info['State']='closing'
    boot.write_text(current_boot)
    if current_boot=='same-boot':assert ownership.cleanup(fake,config)['cleaned']==[info['Scope']]
    else:
        with pytest.raises(RuntimeError,match='Cannot prove'):ownership.cleanup(fake,config)
        assert not any('stop' in args for args in fake.calls)


def test_lepton_unattested_or_missing_shared_lock_is_not_compatible(tmp_path):
    result = lepton.inspect(FakeSystem(tmp_path),'admin')
    assert not result['compatible'] and result['reviewed_commit'] == lepton.REVIEWED_COMMIT
    data = device();data['lepton_cci'] = result
    assert not validate(data)['ok']


@pytest.mark.skipif(os.name!='posix',reason='Live /proc executable and inode test double uses Unix symlinks')
@pytest.mark.parametrize('fault',['','commit','hash','inode','access','world_write','running_binary'])
def test_driver_prerequisite_checks_live_binary_and_same_inode(tmp_path,monkeypatch,fault):
    fake = FakeSystem(tmp_path)
    binary = fake.path(lepton.BINARY);binary.parent.mkdir(parents=True)
    data = b'ELF-test-double\0'+b'\0'.join(lepton.MARKERS)
    binary.write_bytes(data)
    record = dict(commit=lepton.REVIEWED_COMMIT,protocol=lepton.PROTOCOL,
        binary_sha256=hashlib.sha256(data).hexdigest())
    if fault=='commit':record['commit']='old-release'
    if fault=='hash':record['binary_sha256']='0'*64
    atomic(fake.path(lepton.RECORD),record)
    lock = fake.path(lepton.LOCK);lock.parent.mkdir(parents=True);lock.write_bytes(b'')
    os.chmod(lock,0o666 if fault=='world_write' else 0o660)
    proc = fake.path('/proc/42');(proc/'fd').mkdir(parents=True)
    (proc/'exe').symlink_to(binary)
    if fault=='running_binary':
        other = binary.with_name('old_streamer');other.write_bytes(b'old')
        (proc/'exe').unlink();(proc/'exe').symlink_to(other)
    if fault!='inode':os.link(lock,proc/'fd/5')
    def command(*args,**kwargs):
        fake.calls.append(args)
        if 'runuser' in args[0]:
            return '' if fault=='access' else str(lock.stat().st_dev)+':'+str(lock.stat().st_ino)
        return '42'
    fake.command=command
    monkeypatch.setattr(lepton,'trusted_file',lambda path:True)  # CI files owned by runner, not root
    result = lepton.inspect(fake,'admin')
    assert result['compatible'] == (fault=='')
    assert not any('restart' in args or 'stop' in args for args in fake.calls)


@pytest.mark.parametrize('reply',['','MainPID=0','LoadState=loaded\nMainPID=0\nActiveState=activating'])
def test_unknown_or_starting_systemd_state_cannot_authorize_upgrade(tmp_path,reply):
    fake=FakeSystem(tmp_path)
    fake.command=lambda *args,**kwargs:reply
    with pytest.raises(RuntimeError):Installer(fake).require_stopped()


@pytest.mark.skipif(os.name!='posix',reason='Explicit Unix installation umask')
def test_fresh_install_with_private_umask_keeps_public_modules_traversable(system):
    mask=os.umask(0o077)
    try:
        Installer(system).install('admin')
        assert os.umask(0o077)==0o077,'Installer must restore its caller umask'
    finally:os.umask(mask)
    for name in ('/etc/spot-os','/etc/spot-os/labwc','/usr/local/lib/spot-os/'+VERSION,
                 '/usr/local/lib/spot-os/'+VERSION+'/bootstrap'):
        assert system.path(name).stat().st_mode & 0o777 == 0o755
    assert system.path(STATE+'/installation.json').stat().st_mode & 0o777 == 0o600
