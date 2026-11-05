"""Real released OS 0.1.4 -> new installer with signed-engine fault regression."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import zipfile
import pytest
from spot_os.common import read
from spot_os.installer import Installer
from spot_os.update_engine import Engine
from test_installer import system
from test_boot_012 import snapshot
from test_os_updates_014 import signed, Backend

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Actual Unix installer/rollback fidelity')
BASE='c8c10f2c016a6a8a46ac313b75a51d8d79979889'


@pytest.fixture(scope='module')
def released_014(tmp_path_factory):
    root=Path(__file__).resolve().parents[2];folder=tmp_path_factory.mktemp('published-os014')
    raw=subprocess.check_output(['git','-c','safe.directory='+root.as_posix(),'archive','--format=zip',BASE,'spot_os'],cwd=root)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:archive.extractall(folder)
    spec=importlib.util.spec_from_file_location('spot_os._released014',folder/'spot_os/installer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.VERSION='0.1.4';module.REVISION='signed-os-updates-1';module.INSTALL='/usr/local/lib/spot-os/0.1.4'
    return module.Installer,folder/'spot_os'


@pytest.mark.parametrize('failure',[None,'make','health'])
def test_real_published_014_ota_does_not_call_driver_setup(system,released_014,signed,monkeypatch,failure):
    from spot_os.lepton_provision import Provisioner
    monkeypatch.setattr(Provisioner,'run',lambda *a,**k:pytest.fail('OTA invoked driver provisioning'))
    cls,source=released_014;old=cls(system,source);old.install('admin');old.activate('admin')
    sentinels=['/home/admin/.local/share/spot/releases/0.28.13/SPOT_v27.py',
        '/home/admin/.local/share/spot/current.json','/home/admin/DCIM/video.raw',
        '/home/admin/.config/spot/settings.json','/etc/spot-os/os-release.pub',
        '/etc/spot-os/os-trust.json','/etc/spot-os/lepton-compat.json','/run/lock/spot-lepton/cci.lock',
        '/usr/local/bin/lepton_streamer','/usr/local/libexec/lepton/rpi_recovery_app',
        '/lib/modules/test-kernel/extra/lepton.ko','/boot/firmware/overlays/flir-lepton-rpi5.dtbo',
        '/etc/systemd/system/lepton-streamer.service','/etc/modprobe.d/lepton-streams.conf']
    for name in sentinels:
        path=system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'KEEP-'+name.encode())
    before=snapshot(system)
    metadata={n:(system.path(n).stat().st_ino,system.path(n).stat().st_mtime_ns) for n in sentinels}
    class RealBackend(Backend):
        def installed(self):
            c=read(system.path('/etc/spot-os/config.json'));return c['os_version'],c['os_revision'],'0.28.13'
        def upgrade(self,folder,data):
            if failure=='make':system.fail='make'
            try:Installer(system,folder,target=(data['version'],data['revision']),online=True).upgrade('admin')
            finally:system.fail=None
        def rollback(self,expected,previous):
            Installer(system,target=(expected,'signed-os-updates-1')).rollback_upgrade()
    class Source:
        def __init__(self,*args):pass
        def check(self,*args):return signed
        def download(self,*args):return signed['archive']
    backend=RealBackend();backend.fail='health' if failure=='health' else None
    engine=Engine(backend,system.path('/var/lib/spot-os/updates'),lambda:signed['public'],Source)
    engine.dispatch(dict(action='check',prerelease=True),123,False)
    for action in ('prepare','install'):
        engine.dispatch(dict(action=action,candidate=engine.data.get('candidate')),123,False)
    if engine.data['phase']=='handover-pending':engine.resume()
    assert engine.data['phase']==('success' if failure is None else 'rolled-back')
    assert backend.installed()[0]==('0.1.5' if failure is None else '0.1.4')
    assert all(system.path(n).read_bytes()==b'KEEP-'+n.encode() for n in sentinels)
    assert metadata=={n:(system.path(n).stat().st_ino,system.path(n).stat().st_mtime_ns) for n in sentinels}
    assert not system.path('/var/lib/spot-os/lepton-provision.json').exists()
    assert not any(any(x in args for x in ('apt-get','/usr/bin/apt-get','/usr/sbin/dkms','modprobe','reboot')) for args in system.calls)
    assert not any('lepton-streamer.service' in args and any(x in args for x in ('start','stop','restart','reload')) for args in system.calls)
    if failure is None:Installer(system).rollback_upgrade()
    assert snapshot(system)==before
