"""Committed OTA handover, exact 0.1.6 engine and simulated boots; no Pi access."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from spot_os import update_contract as c
from spot_os.common import atomic,read
from spot_os.package import build
from spot_os.update_engine import Engine
from spot_os.update_handover import evidence
from spot_os.system import System
from test_os_updates_014 import Backend
from test_installer import system
from test_boot_012 import snapshot


@pytest.fixture(scope='module')
def signed017(tmp_path_factory):
    folder=tmp_path_factory.mktemp('signed017-test-only')
    package=build(folder)
    archive=Path(package['artifact']).read_bytes()
    data=dict(schema=1,kind='spot-os',repository=c.REPOSITORY,platform=c.PLATFORM,tag='os-v0.1.7',
        version='0.1.7',revision='signed-os-updates-1',sequence=107,asset='spot-os-0.1.7.zip',
        size=len(archive),sha256=hashlib.sha256(archive).hexdigest(),source_commit='7'*40,
        channel='stable',notes='TEST IDENTITY ONLY',requires_os_min='0.1.6',requires_os_max='0.1.6',
        requires_app_min='0.28.13',reader_protocol=1,engine=1,reboot=False)
    raw=json.dumps(data).encode();key=Ed25519PrivateKey.generate()
    return dict(manifest=data,raw=raw,signature=key.sign(raw),archive=archive,release={'tag_name':'os-v0.1.7'},
        public=key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))


@pytest.fixture
def engine017(tmp_path,signed017):
    class B(Backend):
        def __init__(self):super().__init__();self.version='0.1.6'
        def installed(self):return self.version,'signed-os-updates-1','0.28.13'
    class Source:
        def __init__(self,*args):pass
        def check(self,*args):return signed017
        def download(self,*args):return signed017['archive']
    e=Engine(B(),tmp_path/'state',lambda:signed017['public'],Source)
    atomic(e.root/'floor.json',dict(version='0.1.6',sequence=106))
    e.dispatch(dict(action='check',prerelease=False),123,False)
    e.dispatch(dict(action='prepare',candidate=e.data['candidate']),123,False)
    return e


def install(e):e.dispatch(dict(action='install',candidate=e.data['candidate']),123,False)


def test_committed_016_to_017_waits_for_verified_successor(engine017):
    e=engine017;install(e)
    assert e.data['phase']=='handover-pending' and e.status()['busy']
    assert read(e.root/'floor.json')==dict(version='0.1.7',sequence=107)
    assert 'rollback' not in e.backend.events and e.backend.installed()[2]=='0.28.13'
    successor=Engine(e.backend,e.root,e.key_provider);successor.resume()
    assert successor.data['phase']=='success' and successor.data['handover_confirmed'] is True
    assert successor.data['message']=='SPOT OS 0.1.7 wurde erfolgreich installiert.'
    persisted=(e.root/'transaction.json').read_bytes();audit=(e.root/'audit.jsonl').read_bytes()
    Engine(e.backend,e.root,e.key_provider).resume() # later service start / simulated reboot
    assert (e.root/'transaction.json').read_bytes()==persisted
    assert (e.root/'audit.jsonl').read_bytes()==audit


@pytest.mark.parametrize('phase',['handover-pending','handover-failed'])
def test_successor_failure_is_visible_and_cannot_start_another_update(engine017,phase):
    e=engine017;install(e);e.save(phase,'unconfirmed')
    e.backend.fail='confirm-handover';e.resume()
    assert e.data['phase']=='handover-failed' and 'administrative Prüfung' in e.status()['message']
    assert 'rollback' not in e.backend.events
    with pytest.raises(RuntimeError):e.dispatch(dict(action='check',prerelease=False),123,False)
    e.resume() # later genuinely healthy service start can confirm, retaining audit
    assert e.data['phase']=='success'
    assert 'unconfirmed' in (e.root/'audit.jsonl').read_text()


@pytest.mark.parametrize('phase',['verifying','handover-pending'])
def test_interruption_on_both_sides_of_commit(engine017,phase):
    e=engine017;install(e)
    e.save(phase,'power interrupted',previous='0.1.6',version='0.1.7',camera_required=True)
    successor=Engine(e.backend,e.root,e.key_provider);successor.resume()
    assert successor.data['phase']==('rolled-back' if phase=='verifying' else 'success')
    assert read(e.root/'floor.json')['sequence']==107 # never reset by recovery


@pytest.mark.parametrize('floor',[106,107])
def test_floor_106_accepts_new_107_but_107_rejects_replay(signed017,floor):
    if floor==106:assert c.compatible(signed017['manifest'],'0.1.6','0.28.13',dict(sequence=floor,version='0.1.6'))
    else:
        with pytest.raises(ValueError):c.compatible(signed017['manifest'],'0.1.6','0.28.13',dict(sequence=floor,version='0.1.7'))
    for installed in ('0.1.4','0.1.5','0.1.7'):
        with pytest.raises(ValueError):c.compatible(signed017['manifest'],installed,'0.28.13',{})


@pytest.fixture(scope='module')
def exact016(tmp_path_factory):
    root=Path(__file__).resolve().parents[2];folder=tmp_path_factory.mktemp('exact016')
    raw=subprocess.check_output(['git','-c','safe.directory='+root.as_posix(),'archive','--format=zip',
        '7af6009b1aecc3fdf9676ffa20c7414d390faa78','spot_os'],cwd=root)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:archive.extractall(folder)
    return folder/'spot_os'


@pytest.mark.parametrize('restart_error',[False,True])
def test_exact_016_engine_installs_signed017_without_bridge_and_successor_reconciles(engine017,exact016,restart_error):
    spec=importlib.util.spec_from_file_location('spot_os._engine016',exact016/'update_engine.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    e=engine017
    def restart():
        if restart_error:raise RuntimeError('Command failed: /usr/bin/systemctl')
    e.backend.activate_service=restart
    legacy=old.Engine(e.backend,e.root,e.key_provider,e.source_factory)
    install(legacy)
    assert legacy.data['phase']=='success'
    old_message=legacy.data['message'];history=(e.root/'audit.jsonl').read_bytes()
    successor=Engine(e.backend,e.root,e.key_provider);successor.resume()
    assert successor.data['phase']=='success' and successor.data['handover_confirmed']
    assert successor.data['handover_previous_message']==old_message
    assert (e.root/'audit.jsonl').read_bytes().startswith(history)
    assert 'systemctl' not in successor.status()['message']
    assert read(e.root/'floor.json')['sequence']==107


@pytest.mark.skipif(os.name!='posix',reason='Unix installer and exact historical source')
@pytest.mark.parametrize('failure',[None,'make','health'])
def test_real_016_installer_to_017_without_bridge_preserves_driver_app_and_rollback(system,exact016,signed017,failure):
    from spot_os.installer import Installer,Journal
    spec=importlib.util.spec_from_file_location('spot_os._installer016',exact016/'installer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.VERSION='0.1.6';module.INSTALL='/usr/local/lib/spot-os/0.1.6'
    module.Installer(system,exact016).install('admin');module.Installer(system,exact016).activate('admin')
    atomic(system.path('/home/admin/.local/share/spot/current.json'),dict(version='0.28.13'))
    sentinels=['/usr/local/bin/lepton_streamer','/run/lock/spot-lepton/cci.lock','/etc/spot-os/os-release.pub',
        '/home/admin/DCIM/video','/etc/systemd/system/lepton-streamer.service','/etc/ssh/sshd_config']
    for name in sentinels:
        p=system.path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'UNCHANGED')
    before=snapshot(system)
    metadata={n:system.path(n).stat().st_ino for n in sentinels}
    class B(Backend):
        def installed(self):return read(system.path('/etc/spot-os/config.json'))['os_version'],'signed-os-updates-1','0.28.13'
        def upgrade(self,folder,data):
            system.fail='make' if failure=='make' else None
            try:module.Installer(system,folder,target=('0.1.7','signed-os-updates-1'),online=True).upgrade('admin')
            finally:system.fail=None
        def rollback(self,*args):Installer(system).rollback_upgrade()
    class Source:
        def __init__(self,*args):pass
        def check(self,*args):return signed017
        def download(self,*args):return signed017['archive']
    b=B();b.fail='health' if failure=='health' else None
    oldspec=importlib.util.spec_from_file_location('spot_os._real_engine016',exact016/'update_engine.py')
    oldengine=importlib.util.module_from_spec(oldspec);oldspec.loader.exec_module(oldengine)
    e=oldengine.Engine(b,system.path('/var/lib/spot-os/updates'),lambda:signed017['public'],Source)
    atomic(e.root/'floor.json',dict(version='0.1.6',sequence=106))
    e.dispatch(dict(action='check',prerelease=False),123,False)
    for action in ('prepare','install'):e.dispatch(dict(action=action,candidate=e.data['candidate']),123,False)
    if e.data['phase'] in ('handover-pending','success'):
        e=Engine(b,e.root,lambda:signed017['public'],Source);e.resume()
    assert e.data['phase']==('success' if failure is None else 'rolled-back')
    assert not system.path('/var/lib/spot-os/ota-bridge.json').exists()
    assert metadata=={n:system.path(n).stat().st_ino for n in sentinels}
    assert all(system.path(n).read_bytes()==b'UNCHANGED' for n in sentinels)
    assert read(e.root/'floor.json')['sequence']==107
    assert not any(any(a in args for a in ('reboot','modprobe','try-restart')) for args in system.calls)
    assert Journal(system,'upgrade').data['phase']==('committed' if failure is None else 'restored')
    if failure is not None:assert snapshot(system)==before


@pytest.mark.parametrize('change',[{'MainPID':'999'},{'InvocationID':'wrong'},{'ActiveState':'failed'},
    {'Result':'exit-code'},{'SubState':'dead'}])
def test_zero_exit_without_matching_pid1_process_is_not_success(tmp_path,monkeypatch,change):
    monkeypatch.setattr('spot_os.update_handover.identity',lambda _:dict(pid=5,boot='b',start='1',invocation='new'))
    class S(System):
        def command(self,*args,**kwargs):
            values=dict(MainPID='5',InvocationID='new',ActiveState='active',SubState='running',Result='success')
            values.update(change);return '\n'.join(k+'='+v for k,v in values.items())
    with pytest.raises(RuntimeError):evidence(S(tmp_path),dict(invocation='old',boot='b'),timeout=0)


def test_same_process_cannot_confirm_its_own_handover(monkeypatch):
    current=dict(pid=5,boot='b',start='1',invocation='same')
    monkeypatch.setattr('spot_os.update_handover.identity',lambda _:current)
    with pytest.raises(RuntimeError,match='noch nicht'):evidence(None,current)


def test_simulated_new_boot_disambiguates_reused_pid_and_keeps_confirmed_history(engine017,monkeypatch):
    e=engine017;install(e)
    current=dict(pid=10,boot='new-boot',start='1',invocation='new-boot-invocation')
    monkeypatch.setattr('spot_os.update_handover.identity',lambda _:current)
    class S:
        def command(self,*args,**kwargs):
            return 'MainPID=10\nInvocationID=new-boot-invocation\nActiveState=active\nSubState=running\nResult=success'
    proof=evidence(S(),e.data['handover_origin'])
    e.backend.confirm_handover=lambda *args:proof
    successor=Engine(e.backend,e.root,e.key_provider);successor.resume()
    assert successor.data['handover_evidence']['boot']=='new-boot'
    saved=(e.root/'transaction.json').read_bytes()
    # A subsequent simulated boot reloads durable state without replaying the
    # old warning or rewriting the successful transaction/floor/audit.
    another=Engine(e.backend,e.root,e.key_provider);another.resume()
    assert another.status()['phase']=='success' and (e.root/'transaction.json').read_bytes()==saved


@pytest.mark.parametrize('fault',[None,'files','active','version','gui','camera','age','boot','session'])
def test_production_backend_checks_code_journals_and_fresh_health_before_success(system,monkeypatch,fault):
    from spot_os.update_backend import Backend as ActualBackend
    atomic(system.path('/etc/spot-os/config.json'),dict(user='admin',uid=1000,prefix='/home/admin/.local/share/spot'))
    b=ActualBackend(system)
    b.installed=lambda:('0.1.6' if fault=='version' else '0.1.7','signed-os-updates-1','0.28.13')
    b.status=lambda:dict(state='running',gui=fault!='gui',camera=fault!='camera',
                         evidence_age=2 if fault=='age' else 0,session_invocation='session')
    health=dict(boot_verified=True,boot='stale' if fault=='boot' else 'boot',
                session_invocation='old' if fault=='session' else 'session')
    atomic(system.path('/var/lib/spot-os/health.json'),health)
    class Checked:
        def __init__(self,*args):pass
        def check(self,**kwargs):return dict(files_ok=fault!='files',installation_complete=True,active=fault!='active')
    monkeypatch.setattr('spot_os.update_backend.Installer',Checked)
    monkeypatch.setattr('spot_os.update_handover.evidence',lambda *args:dict(boot='boot',pid=99,invocation='new'))
    if fault:
        with pytest.raises(RuntimeError):b.confirm_handover('0.1.7',{},True)
    else:assert b.confirm_handover('0.1.7',{},True)['pid']==99
