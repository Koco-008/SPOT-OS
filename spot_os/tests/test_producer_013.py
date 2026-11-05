"""OS 0.1.3 compatibility fuse and exact installed-0.1.2 upgrade regression."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
from spot_os import VERSION, REVISION
from spot_os import reader_contract
from spot_os.installer import Installer, STATE
from spot_os.common import read
from spot_os.system import validate
from test_installer import device, system
from test_boot_012 import snapshot


def test_package_version_matches_runtime():
    assert (Path(__file__).resolve().parents[1]/'VERSION').read_text().strip()==VERSION


def test_preflight_requires_checked_signed_app_reader_contract():
    data=device();data['producer_gated']=False
    assert validate(data)['checks']['producer_gated_reader'] is False
    data['producer_gated']=True
    assert validate(data)['ok']


def test_contract_checks_installed_payload_hashes_and_marker(tmp_path):
    (tmp_path/'SPOT_v27.py').write_text('class LeptonStream:\n    producer_protocol=1\n')
    (tmp_path/'spot_v4l2.py').write_text('# transport\n')
    manifest=dict(files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()})
    assert reader_contract.inspect(tmp_path,manifest)
    (tmp_path/'spot_v4l2.py').write_text('changed')
    assert not reader_contract.inspect(tmp_path,manifest)
    manifest['files'].pop('spot_v4l2.py')
    assert not reader_contract.inspect(tmp_path,manifest)


def test_legacy_rollback_gui_can_run_but_never_open_or_reopen_camera():
    calls=[]
    class Legacy:
        def start(self):raise AssertionError('unsafe capture open')
        def _clear_frames(self):calls.append('clear')
        def _set_error(self,error):calls.append(error)
    instance=Legacy();instance.reopen_done=SimpleNamespace(set=lambda:calls.append('done'))
    module=SimpleNamespace(LeptonStream=Legacy)
    assert reader_contract.guard(module) is False
    instance.start();instance.request_reopen()
    with pytest.raises(RuntimeError,match='0.28.11'):instance.reopen()
    assert calls.count('clear')==2 and any('Kameraschutz' in s for s in calls)
    # The safe app on OS 0.1.3 is left entirely unchanged.
    class Safe:
        producer_protocol=1
        def start(self):return 'producer gate'
    original=Safe.start
    assert reader_contract.guard(SimpleNamespace(LeptonStream=Safe)) is True
    assert Safe.start is original


@pytest.fixture(scope='module')
def installed_012(tmp_path_factory):
    root=Path(__file__).resolve().parents[2]
    folder=tmp_path_factory.mktemp('exact-012')
    git=['git','-c','safe.directory='+root.as_posix()]
    base='7effe01d8bbc2a5c68672af20acf66dc30273425'
    names=subprocess.check_output(git+['ls-tree','-r','--name-only',base,'spot_os'],cwd=root,text=True).splitlines()
    for name in names:
        path=folder/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(subprocess.check_output(git+['show',base+':'+name],cwd=root))
    spec=importlib.util.spec_from_file_location('spot_os._installed012',folder/'spot_os/installer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.VERSION='0.1.2';module.REVISION='lepton-ordering-1';module.INSTALL='/usr/local/lib/spot-os/0.1.2'
    return module.Installer,folder/'spot_os'


@pytest.mark.skipif(os.name!='posix',reason='Linux atomic symlinks and mode-preserving upgrade')
@pytest.mark.parametrize('failure',[False,True])
def test_exact_running_candidate_upgrade_idempotent_and_full_rollback(system,installed_012,failure):
    cls,source=installed_012
    old=cls(system,source);old.install('admin');old.activate('admin')
    sentinels=['/home/admin/DCIM/test.raw','/home/admin/.config/spot/measurement.json',
        '/etc/ssh/sshd_config','/usr/local/bin/lepton_streamer','/etc/spot-os/lepton-compat.json']
    for name in sentinels:
        path=system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'preserve-exact')
    before=snapshot(system)
    new=Installer(system)
    if failure:
        system.fail='make'
        with pytest.raises(RuntimeError):new.upgrade('admin')
        assert snapshot(system)==before
        return
    assert new.upgrade('admin')['upgraded']
    config=read(system.path('/etc/spot-os/config.json'))
    assert config['os_version']==VERSION and config['os_revision']==REVISION
    assert new.upgrade('admin')['unchanged']
    for name in sentinels:assert system.path(name).read_bytes()==b'preserve-exact'
    assert new.rollback_upgrade()['version']=='0.1.2'
    assert snapshot(system)==before
    assert not any(args[0].endswith(('reboot','shutdown','modprobe')) for args in system.calls)
