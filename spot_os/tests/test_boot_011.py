"""Fault policy and migration regressions; real unit integration lives in run_units.py."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from spot_os.common import atomic, read
from spot_os.health import check
from spot_os.installer import Installer, Journal, STATE
from test_installer import system  # shared fake filesystem, never the real machine

BASE = 'dfac098f7613c34427a4f2b3df7a337d02ebd8df'
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('status', [dict(state='waiting_for_app_ready'), dict(state='recovery',reason='startup_timeout'),
    dict(state='failed'),dict(state='running',gui=False),dict(state='running',gui=True,milestones={})])
def test_boot_timeout_and_recovery_are_failures(tmp_path, status):
    ticks = [0.]
    result = check({}, lambda *_: status, tmp_path/'health.json', timeout=2,
        clock=lambda:ticks[0],pause=lambda n:ticks.__setitem__(0,ticks[0]+n))
    assert result['boot_verified'] is False
    assert read(tmp_path/'health.json') == result


def test_boot_health_requires_stable_independent_presentation_and_gui(tmp_path):
    ticks = [0.]
    status = dict(state='running',gui=True,milestones=dict(splash_presented=1,app_revealed=2))
    result = check({}, lambda *_: status, tmp_path/'health.json', timeout=3, stable=1,
        clock=lambda:ticks[0],pause=lambda n:ticks.__setitem__(0,ticks[0]+n))
    assert result['boot_verified'] and ticks[0]>=1


def test_manager_missing_does_not_return_success(tmp_path):
    ticks = [0.]
    def absent(*_):raise ConnectionRefusedError()
    assert not check({},absent,tmp_path/'health.json',timeout=1,
        clock=lambda:ticks[0],pause=lambda n:ticks.__setitem__(0,ticks[0]+n))['boot_verified']


def test_unit_has_one_tty_owner_and_independent_health():
    unit = (ROOT/'spot_os/config/spot-os-session.service').read_text()
    assert 'ExecStartPost=' not in unit and 'ExecStartPre=' not in unit
    assert 'TTYVHangup=no' in unit and 'StandardInput=tty-fail' in unit
    assert 'Type=notify' in unit and 'NotifyAccess=main' in unit
    assert 'StartLimitIntervalSec=120' in unit and 'StartLimitBurst=3' in unit
    assert 'StartLimitIntervalSec=infinity' not in unit
    assert 'RestartForceExitStatus=SIGHUP' in unit
    assert 'PAMName=spot-os' in unit


@pytest.mark.parametrize('state,expected',[(None,True),('committed',True),('recovered',True),
    ('rolled-back',True),('pending',False),('unknown',False)])
def test_pending_transaction_blocks_maintenance(state,expected):
    from test_installer import device
    from spot_os.system import validate
    data=device();data['transaction_state']=state
    assert validate(data)['ok'] is expected


def test_preparation_refuses_competing_tty1_before_chvt(monkeypatch):
    from spot_os import health
    calls=[]
    class System:
        def command(self,*args):
            calls.append(args)
            return 'c9 1000 admin seat0 tty1'
    monkeypatch.setattr('spot_os.supervisor.session_info',lambda _:dict(VTNr='1',Leader='123',State='active'))
    with pytest.raises(RuntimeError,match='tty1 still belongs'):health.prepare(System())
    assert not any('chvt' in arg for call in calls for arg in call)


@pytest.fixture(scope='module')
def legacy(tmp_path_factory):
    folder = tmp_path_factory.mktemp('actual-os-0.1')
    git = ['git','-c','safe.directory='+ROOT.as_posix()]
    names = subprocess.run(git+['ls-tree','-r','--name-only',BASE,'spot_os'],cwd=ROOT,
        capture_output=True,check=True,text=True).stdout.splitlines()
    assert names
    for name in names:
        path = folder/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(subprocess.run(git+['show',BASE+':'+name],cwd=ROOT,capture_output=True,check=True).stdout)
    spec = importlib.util.spec_from_file_location('spot_os._legacy_installer',folder/'spot_os/installer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Installer, folder/'spot_os'


@pytest.mark.skipif(os.name!='posix',reason='Linux filesystem owner/symlink fidelity')
@pytest.mark.parametrize('phase',['prepared','active_failed_gui','rolled_back','uninstalled','fresh'])
def test_migrate_actual_01_all_five_states_and_restore_desktop(system, legacy, phase):
    original = {str(p.relative_to(system.root)):p.read_bytes() for p in system.root.rglob('*') if p.is_file()}
    # Sentinels for exactly the data an OS migration must never write.
    sentinels = ['/home/admin/.local/share/spot/releases/0.28.10/SPOT_v27.py',
        '/home/admin/DCIM/measurement.raw','/home/admin/.config/spot.json',
        '/etc/systemd/system/lepton-streamer.service','/etc/ssh/sshd_config']
    for name in sentinels:
        path = system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'unchanged')
    old_class, old_source = legacy
    old = old_class(system,old_source)
    if phase!='fresh':old.install('admin')
    if phase in ('active_failed_gui','rolled_back','uninstalled'):old.activate('admin')
    if phase=='rolled_back':old.deactivate()
    if phase=='uninstalled':old.uninstall()
    snapshots = {j:system.path(STATE+'/'+j+'.json').read_bytes() for j in ('installation','activation')
                 if system.path(STATE+'/'+j+'.json').exists()}
    new = Installer(system)
    if phase in ('prepared','active_failed_gui','rolled_back'):
        with pytest.raises(RuntimeError,match='version differs'):new.install('admin')
    result = new.migrate('admin')
    assert result['prepared'] and not result['active']
    assert new.check()['config']['os_version']==__import__('spot_os').VERSION and new.check()['ok']
    assert os.readlink(system.path('/usr/local/lib/spot-os/spot_os'))==__import__('spot_os').VERSION
    assert not system.path('/usr/local/lib/spot-os/0.1/native/spot-splash').exists()
    for name,data in snapshots.items():
        assert system.path(STATE+'/history/'+hashlib.sha256(data).hexdigest()+'/'+name+'.json').read_bytes()==data
    new.activate('admin')
    new.deactivate()
    new.uninstall()
    for name,data in original.items():assert system.path('/'+name).read_bytes()==data
    for name in sentinels:assert system.path(name).read_bytes()==b'unchanged'
    assert not any('reboot' in args or 'stop' in args for args in system.calls)


@pytest.mark.skipif(os.name!='posix',reason='Linux filesystem')
def test_migration_build_failure_keeps_original_desktop_and_archives(system, legacy):
    cls,source=legacy
    old=cls(system,source);old.install('admin');old.activate('admin')
    system.fail='make'
    with pytest.raises(RuntimeError):Installer(system).migrate('admin')
    assert b'console=tty1' in system.path('/boot/firmware/cmdline.txt').read_bytes()
    assert not system.path('/etc/systemd/system/display-manager.service').is_symlink()
    assert list(system.path(STATE+'/history').glob('*/activation.json'))


@pytest.mark.skipif(os.name!='posix',reason='Linux filesystem')
def test_migration_refuses_live_and_corrupted_backup_before_mutation(system, legacy):
    cls,source=legacy
    old=cls(system,source);old.install('admin');old.activate('admin')
    system.live=True
    with pytest.raises(RuntimeError,match='still live'):Installer(system).migrate('admin')
    system.live=False
    item=next(e for e in Journal(system,'activation').data['entries'] if 'blob' in e)
    system.path(STATE+'/backups/'+item['blob']).write_bytes(b'corrupt')
    before=system.path('/boot/firmware/cmdline.txt').read_bytes()
    with pytest.raises(ValueError,match='hash mismatch'):Installer(system).migrate('admin')
    assert system.path('/boot/firmware/cmdline.txt').read_bytes()==before
