"""Original engine, signed temporary artifacts, isolated filesystem/backend."""
import base64
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import zipfile
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from cryptography.exceptions import InvalidSignature
from spot_os import update_contract as c
from spot_os.common import read,atomic
from spot_os.update_engine import Engine,MUTATING
from spot_os.package import build
from spot_os.update_source import check_url


@pytest.fixture(scope='module')
def signed(tmp_path_factory):
    root=tmp_path_factory.mktemp('ephemeral-os-signing-test')
    source=root/'source/spot_os'
    shutil.copytree(Path(__file__).parents[1],source,ignore=shutil.ignore_patterns('tests','__pycache__','outputs'))
    (source/'__init__.py').write_text("VERSION='0.1.5'\nREVISION='signed-os-updates-1'\n")
    (source/'VERSION').write_text('0.1.5\n')
    package=build(root/'release',source,target=('0.1.5','signed-os-updates-1'))
    archive=Path(package['artifact']).read_bytes()
    data=dict(schema=1,kind='spot-os',repository=c.REPOSITORY,platform=c.PLATFORM,tag='os-v0.1.5',
        version='0.1.5',revision='signed-os-updates-1',sequence=105,asset='spot-os-0.1.5.zip',size=len(archive),
        sha256=hashlib.sha256(archive).hexdigest(),source_commit='1'*40,channel='stable',notes='Test artifact only',
        requires_os_min='0.1.4',requires_os_max='0.1.4',requires_app_min='0.28.12',reader_protocol=1,engine=1,reboot=False)
    raw=(json.dumps(data,sort_keys=True)+'\n').encode()
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo)
    return dict(raw=raw,signature=key.sign(raw),manifest=data,release={'tag_name':'os-v0.1.5'},public=public,archive=archive)


def test_signed_original_os_package_full_file_set(signed,tmp_path):
    data=c.verify(signed['raw'],signed['signature'],signed['public'])
    assert c.compatible(data,'0.1.4','0.28.12',{})==data
    folder=c.unpack(signed['archive'],data,tmp_path/'validated')
    assert (folder/'__init__.py').read_text().startswith("VERSION='0.1.5'")
    assert not (folder/'tests').exists()


@pytest.mark.parametrize('field,value',[
    ('schema',True),('kind','app'),('repository','evil/repo'),('platform','x86_64'),('tag','v0.1.5'),
    ('version','0.01.5'),('asset','../update.zip'),('revision','$(touch /tmp/x)'),('sequence',True),
    ('size',True),('size',c.MAX_ARCHIVE+1),('sha256','z'*64),('source_commit','1'*39),('channel','beta'),
    ('notes','a'*1501),('reboot',True),('engine',2),('reader_protocol',0),('requires_os_min','1.0.0'),
    ('requires_app_min','bad')])
def test_manifest_strict_fields(signed,field,value):
    data=dict(signed['manifest']);data[field]=value
    with pytest.raises(ValueError):c.manifest(json.dumps(data).encode())


def test_duplicate_extra_and_oversized_manifest_rejected(signed):
    for raw in (signed['raw'].replace(b'"schema": 1',b'"schema": 1,"schema": 1'),
                json.dumps(dict(signed['manifest'],command='reboot')).encode(),b' '*65537):
        with pytest.raises(ValueError):c.manifest(raw)


def test_signature_tamper_wrong_key(signed):
    other=Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo)
    for raw,sig,key in ((signed['raw']+b' ',signed['signature'],signed['public']),
                        (signed['raw'],b'x'*64,signed['public']),
                        (signed['raw'],signed['signature'],other)):
        with pytest.raises(InvalidSignature):c.verify(raw,sig,key)


@pytest.mark.parametrize('installed,app,floor,pre',[
    ('0.1.5','0.28.12',{},False),('0.1.3','0.28.12',{},False),('0.1.4','0.28.11',{},False),
    ('0.1.4','0.28.12',{'version':'0.1.5','sequence':105},False),
    ('0.1.4','0.28.12',{'version':'0.1.4','sequence':105},False)])
def test_downgrade_replay_bootstrap_app_compatibility(signed,installed,app,floor,pre):
    with pytest.raises(ValueError):c.compatible(signed['manifest'],installed,app,floor,pre)


def test_prerelease_is_explicit(signed):
    data=dict(signed['manifest'],channel='prerelease')
    with pytest.raises(ValueError):c.compatible(data,'0.1.4','0.28.12',{})
    assert c.compatible(data,'0.1.4','0.28.12',{},True)


@pytest.mark.parametrize('name,mode',[('../etc/shadow',0o100644),('spot_os/../../x',0o100644),
    ('spot_os/link',0o120777),('spot_os\\bad',0o100644),('spot_os/.hidden',0o100644),
    ('spot_os/tests/private.py',0o100644),('spot_os/unlisted.py',0o100644)])
def test_archive_traversal_links_extra_members_rejected(signed,tmp_path,name,mode):
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(signed['archive'])) as original,zipfile.ZipFile(output,'w') as archive:
        for item in original.infolist():archive.writestr(item,original.read(item))
        info=zipfile.ZipInfo(name);info.external_attr=mode<<16;archive.writestr(info,b'bad')
    raw=output.getvalue();data=dict(signed['manifest'],size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):c.unpack(raw,data,tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_hash_or_partial_download_never_extracts(signed,tmp_path):
    for raw in (signed['archive'][:-1],b'x'+signed['archive'][1:]):
        with pytest.raises(ValueError):c.unpack(raw,signed['manifest'],tmp_path/'target')
    assert not (tmp_path/'target').exists()


@pytest.mark.parametrize('url',['http://api.github.com/x','https://evil.test/x',
    'https://githubusercontent.com.evil.test/x','https://user@api.github.com/x','https://api.github.com:444/x'])
def test_download_redirects_bounded_to_github(url):
    with pytest.raises(ValueError):check_url(url)


class Backend:
    def __init__(self):self.events=[];self.version='0.1.4';self.fail=None;self.busy=False;self.exited=True
    def installed(self):return self.version,'signed-os-updates-1','0.28.12'
    def preflight(self,folder,data):self.inject('preflight')
    @contextmanager
    def update_lock(self):
        self.events.append('lock');yield;self.events.append('unlock')
    @contextmanager
    def transaction_guard(self):yield
    def consent(self,pid):
        if self.busy or pid!=123:raise RuntimeError('busy/wrong GUI')
        return dict(session_invocation='old',camera=True)
    def cover(self):self.events.append('cover')
    def cancel_cover(self):self.events.append('cancel')
    def wait_exit(self,pid):
        if not self.exited:raise RuntimeError('GUI busy; not exited')
        self.events.append('exit')
    def stop(self):self.events.append('stop');self.inject('stop')
    def upgrade(self,folder,data):
        assert (folder/'PACKAGE.json').is_file()
        self.events.append('upgrade');self.inject('upgrade');self.version=data['version']
    def start(self):self.events.append('start');self.inject('start')
    def healthy(self,*args):
        self.events.append('health');self.inject('health');return dict(camera=True,gui=True)
    def activate_service(self):self.events.append('activate-service');self.inject('activate-service')
    def process_identity(self):return dict(pid=10,boot='boot',start='1',invocation='old')
    def confirm_handover(self,expected,origin,camera_required):
        self.inject('confirm-handover')
        assert self.installed()[0]==expected
        return dict(pid=11,boot='boot',start='2',invocation='new',systemd=dict(ActiveState='active',SubState='running'))
    def rollback(self,expected,previous):
        self.events.append('rollback');self.inject('rollback');self.version=previous
    def inject(self,point):
        if self.fail==point:self.fail=None;raise RuntimeError('injected '+point)


@pytest.fixture
def engine(signed,tmp_path):
    class Source:
        def __init__(self,*args):pass
        def check(self,*args):return signed
        def download(self,*args):return signed['archive']
    e=Engine(Backend(),tmp_path/'root-only-os-state',lambda:signed['public'],Source)
    e.dispatch(dict(action='check',prerelease=False),123,False)
    e.dispatch(dict(action='prepare',candidate=e.data['candidate']),123,False)
    assert e.data['phase']=='prepared'
    return e


def install(engine):
    engine.dispatch(dict(action='install',candidate=engine.data['candidate']),123,False)
    if engine.data['phase']=='handover-pending':engine.resume()


def test_one_click_root_transaction_outlives_gui(engine):
    install(engine)
    assert engine.data['phase']=='success' and engine.backend.version=='0.1.5'
    assert engine.backend.events==['lock','cover','exit','stop','upgrade','unlock','start','health','activate-service']
    assert read(engine.root/'floor.json')['sequence']==105
    assert not (engine.root/'session-blocked').exists()


def test_updater_replacement_only_after_durable_success(engine):
    def activate():
        assert read(engine.root/'transaction.json')['phase']=='handover-pending'
        raise RuntimeError('restart failed')
    engine.backend.activate_service=activate
    install(engine)
    assert engine.data['phase']=='handover-failed' and 'administrative Prüfung' in engine.data['message']
    assert 'rollback' not in engine.backend.events


@pytest.mark.parametrize('failure',['stop','upgrade','start','health'])
def test_failure_restores_previous_os_without_app_replacement(engine,failure):
    engine.backend.fail=failure;install(engine)
    assert engine.data['phase']=='rolled-back'
    assert engine.backend.version=='0.1.4' and engine.backend.installed()[2]=='0.28.12'
    assert engine.backend.events.count('rollback')==1


@pytest.mark.parametrize('busy,exited',[(True,True),(False,False)])
def test_busy_or_nonclosing_gui_never_stopped(engine,busy,exited):
    engine.backend.busy=busy;engine.backend.exited=exited;install(engine)
    assert engine.data['phase']=='error' and 'stop' not in engine.backend.events
    assert engine.backend.version=='0.1.4'


@pytest.mark.parametrize('phase',sorted(MUTATING))
def test_interrupted_transaction_resumes_through_rollback(engine,phase):
    engine.save(phase,'simulated power loss',previous='0.1.4',version='0.1.5',old_invocation='old',camera_required=True)
    recovered=Engine(engine.backend,engine.root,engine.key_provider,engine.source_factory)
    recovered.resume()
    assert recovered.data['phase']=='rolled-back'
    assert engine.backend.version=='0.1.4'


def test_second_interrupted_rollback_has_finite_budget(engine):
    engine.save('rolling-back','interrupted',previous='0.1.4',version='0.1.5',rollback_attempts=1)
    engine.resume();assert engine.data['phase']=='rolled-back'
    engine.save('rolling-back','interrupted again',rollback_attempts=2)
    before=list(engine.backend.events);engine.resume()
    assert engine.data['phase']=='recovery-failed' and engine.backend.events==before


@pytest.mark.parametrize('message',[{'action':'shell','command':'id'},{'action':'status','path':'/etc/shadow'},
    {'action':'prepare','candidate':'../bad'},{'action':'check','prerelease':1},{'action':'install','candidate':'x'*64},
    {'action':'install','candidate':'1'*64,'root':'/'}])
def test_ipc_protocol_has_no_paths_commands_or_extra_fields(engine,message):
    with pytest.raises((ValueError,RuntimeError)):engine.dispatch(message,123,False)


def test_insufficient_space_refuses_before_download(engine,monkeypatch):
    monkeypatch.setattr(shutil,'disk_usage',lambda _:type('Space',(),dict(free=1))())
    engine.dispatch(dict(action='prepare',candidate=engine.data['candidate']),123,False)
    assert engine.data['phase']=='error' and engine.backend.events==[]


@pytest.mark.parametrize('failure',[TimeoutError('offline'),OSError('partial transfer'),ValueError('bad download')])
def test_network_or_download_failure_never_stops_gui(engine,failure):
    class Broken:
        def __init__(self,*args):pass
        def download(self,*args):raise failure
    engine.source_factory=Broken
    engine.dispatch(dict(action='prepare',candidate=engine.data['candidate']),123,False)
    assert engine.data['phase']=='error' and not engine.backend.events
    assert not (engine.root/'floor.json').exists()


def test_signature_failure_is_reported_without_blank_error(engine):
    engine.key_provider=lambda:Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo)
    engine.dispatch(dict(action='prepare',candidate=engine.data['candidate']),123,False)
    assert engine.data['phase']=='error' and 'Signatur ungültig' in engine.data['message']


def test_preflight_failure_happens_before_gui_exit(engine):
    engine.backend.fail='preflight';install(engine)
    assert engine.data['phase']=='error' and not engine.backend.events


def test_failed_recovery_cannot_be_bypassed_by_check_or_prepare(engine):
    engine.save('recovery-failed','unverified')
    for message in (dict(action='check',prerelease=False),dict(action='prepare',candidate=engine.data['candidate'])):
        with pytest.raises(RuntimeError):engine.dispatch(message,123,False)
    assert engine.dispatch(dict(action='status'),123,False)['phase']=='recovery-failed'


def test_concurrent_operation_reserved_before_worker_starts(engine):
    engine.operation='install'
    with pytest.raises(RuntimeError):engine.dispatch(dict(action='check',prerelease=False),123)


def test_missing_anchor_is_explicit_not_a_default_test_key(engine):
    def absent():raise FileNotFoundError('independent OS key missing')
    engine.key_provider=absent
    result=engine.status()
    assert result['configured'] is False and 'noch nicht eingerichtet' in result['setup_error']


def test_status_with_long_unicode_release_notes_fits_ipc_budget(engine):
    from spot_os.common import encode
    engine.save('available','😀'*1000,notes='😀'*1500)
    assert len(encode(engine.status()))<16384
