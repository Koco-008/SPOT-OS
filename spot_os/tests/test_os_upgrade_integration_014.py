"""Real Installer+Journal reached THROUGH the signed OTA engine; fake target root."""
import importlib.util
import os
from pathlib import Path
import subprocess
import pytest
from spot_os.common import read
from spot_os.installer import Installer,Journal,STATE
from spot_os.update_engine import Engine
from test_installer import system
from test_boot_012 import snapshot
from test_os_updates_014 import signed,Backend

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Atomic Unix symlink/metadata journal')


@pytest.fixture(scope='module')
def installed_013(tmp_path_factory):
    root=Path(__file__).resolve().parents[2]
    folder=tmp_path_factory.mktemp('exact-installed-013')
    commit='7cd5b7efe79e74fd4148ee586749be7935fd05d4'
    import io,zipfile
    raw=subprocess.check_output(['git','-c','safe.directory='+root.as_posix(),'archive','--format=zip',commit,'spot_os'],cwd=root)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:archive.extractall(folder)
    spec=importlib.util.spec_from_file_location('spot_os._installed013',folder/'spot_os/installer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.VERSION='0.1.3';module.REVISION='producer-gate-1';module.INSTALL='/usr/local/lib/spot-os/0.1.3'
    return module.Installer,folder/'spot_os'


@pytest.mark.parametrize('fail',[False,True])
def test_actual_013_bootstrap_upgrade_and_exact_rollback(system,installed_013,fail):
    cls,source=installed_013
    cls(system,source).install('admin');cls(system,source).activate('admin')
    before=snapshot(system);new=Installer(system)
    if fail:
        system.fail='make'
        with pytest.raises(RuntimeError):new.upgrade('admin')
        assert snapshot(system)==before
    else:
        new.upgrade('admin')
        assert read(system.path('/etc/spot-os/config.json'))['os_version']==__import__('spot_os').VERSION
        assert b'python3 -I /usr/local/lib/spot-os/admin_entry.py' in system.path('/usr/local/bin/spot-os').read_bytes()
        assert b'StartLimitIntervalSec=120' in system.path('/etc/systemd/system/spot-os-session.service').read_bytes()
        assert new.upgrade('admin')['unchanged']
        assert new.rollback_upgrade()['version']=='0.1.3'
        assert snapshot(system)==before


@pytest.mark.parametrize('failure',[None,'make','health'])
def test_automatic_014_to_015_uses_real_existing_installer_and_preserves_all_data(system,signed,tmp_path,failure):
    old=Installer(system,target=('0.1.4','signed-os-updates-1'));old.install('admin');old.activate('admin')
    sentinels=['/home/admin/DCIM/video.raw','/home/admin/.config/spot/settings.json','/etc/ssh/sshd_config',
               '/etc/systemd/system/lepton-streamer.service','/etc/spot-os/lepton-compat.json']
    for name in sentinels:
        path=system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'KEEP-EXACT')
    before=snapshot(system)
    class RealJournalBackend(Backend):
        def installed(self):
            config=read(system.path('/etc/spot-os/config.json'))
            return config['os_version'],config['os_revision'],'0.28.12'
        def upgrade(self,folder,data):
            self.events.append('upgrade')
            if failure=='make':system.fail='make'
            try:Installer(system,folder,target=(data['version'],data['revision']),online=True).upgrade('admin')
            finally:system.fail=None
        def rollback(self,expected,previous):
            self.events.append('rollback')
            Installer(system,target=(expected,'signed-os-updates-1')).rollback_upgrade()
    class Source:
        def __init__(self,*args):pass
        def check(self,*args):return signed
        def download(self,*args):return signed['archive']
    backend=RealJournalBackend();backend.fail='health' if failure=='health' else None
    # Match the real state location: audit, archive and monotone attempt-floor
    # deliberately survive rollback, like the existing installer backup history.
    engine=Engine(backend,system.path(STATE+'/updates'),lambda:signed['public'],Source)
    engine.dispatch(dict(action='check',prerelease=False),123,False)
    engine.dispatch(dict(action='prepare',candidate=engine.data['candidate']),123,False)
    engine.dispatch(dict(action='install',candidate=engine.data['candidate']),123,False)
    if engine.data['phase']=='handover-pending':engine.resume()
    assert engine.data['phase']==('success' if failure is None else 'rolled-back')
    assert backend.installed()[0]==('0.1.5' if failure is None else '0.1.4')
    assert all(system.path(name).read_bytes()==b'KEEP-EXACT' for name in sentinels)
    assert (engine.root/'audit.jsonl').is_file()
    assert (engine.root/'floor.json').is_file()
    assert not any(any(part in args for part in ('reboot','shutdown','modprobe','reset-failed')) for args in system.calls)
    assert not any('lepton-streamer.service' in args and any(a in args for a in ('start','stop','restart')) for args in system.calls)
    if failure is not None:assert snapshot(system)==before
    else:
        Installer(system,target=('0.1.5','signed-os-updates-1')).rollback_upgrade()
        assert snapshot(system)==before
