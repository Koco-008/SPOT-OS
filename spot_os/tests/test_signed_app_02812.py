"""Actual published Ed25519 delivery, not a rebuilt/re-signed app double.

Offline, pinned fixture; no production private key, network, camera or service
mutation. Imports run in fresh interpreters so the legacy app stays untouched.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
from cryptography.exceptions import InvalidSignature
from spot_update import verify_signature, extract_verified
from spot_os import reader_contract
from spot_os.installer import Installer
from test_installer import system

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures')/'spot-0.28.12'
COMMIT = 'adb8aa549393b40c3e00e95423be23f8a3715761'
ZIP_SHA = '221f586c335fe47118c8d08f67ca1c81344234cdaa5ef8d967a9903197bfdb19'
KEY = ROOT/'assets/trust/spot-release.pub'


@pytest.fixture(scope='module')
def published_app(tmp_path_factory):
    provenance=json.loads((FIXTURE/'provenance.json').read_text(encoding='utf-8'))
    assert provenance['source_commit']==COMMIT
    assert provenance['files']['spot-0.28.12.zip']==ZIP_SHA
    assert hashlib.sha256(KEY.read_bytes()).hexdigest()==provenance['trust_key_sha256']
    for name,digest in provenance['files'].items():
        assert hashlib.sha256((FIXTURE/name).read_bytes()).hexdigest()==digest,name
    manifest=verify_signature((FIXTURE/'update.json').read_bytes(),
        (FIXTURE/'update.json.sig').read_bytes(),KEY)
    assert manifest['version']=='0.28.12' and manifest['repository']=='Koco-008/SPOT-Releases'
    archive=FIXTURE/manifest['asset']
    assert manifest['sha256']==ZIP_SHA and archive.stat().st_size==manifest['size']
    folder=tmp_path_factory.mktemp('signed-unchanged-02812')
    internal=extract_verified(archive,folder,'0.28.12')
    return folder,internal,provenance


def test_actual_signed_02812_is_accepted_by_os_preflight(published_app,system):
    folder,internal,_=published_app
    assert reader_contract.inspect(folder,internal)
    system.data['app']='0.28.12'
    # Real contract result, hardware inventory remains a double.
    system.data['producer_gated']=reader_contract.inspect(folder,internal)
    result=Installer(system).preflight('admin')
    assert result['ok'] and result['checks']['producer_gated_reader']
    assert not system.calls  # no system service or device actions


def test_published_payload_matches_actual_released_commit(published_app):
    folder,internal,_=published_app
    git=['git','-c','safe.directory='+ROOT.as_posix()]
    result=subprocess.run(git+['archive','--format=zip',COMMIT],cwd=ROOT,
        capture_output=True,check=True,timeout=30)
    with zipfile.ZipFile(io.BytesIO(result.stdout)) as source:
        for name in internal['files']:
            raw=source.read(name)
            if Path(name).suffix in {'.py','.sh','.md','.txt','.svg','.json'}:
                raw=raw.replace(b'\r\n',b'\n')
            assert (folder/name).read_bytes()==raw,name


@pytest.mark.parametrize('part',['manifest','signature'])
def test_changed_signature_or_manifest_is_rejected(part):
    raw=(FIXTURE/'update.json').read_bytes()
    sig=(FIXTURE/'update.json.sig').read_bytes()
    if part=='manifest':raw=raw.replace(b'0.28.12',b'0.28.13')
    else:sig=bytes([sig[0]^1])+sig[1:]
    with pytest.raises(InvalidSignature):verify_signature(raw,sig,KEY)


def test_modified_reader_is_rejected_without_touching_fixture(published_app,tmp_path):
    folder,internal,_=published_app
    for name in ('SPOT_v27.py','spot_v4l2.py'):
        (tmp_path/name).write_bytes((folder/name).read_bytes())
    (tmp_path/'spot_v4l2.py').write_bytes(b'# modified reader\n')
    assert not reader_contract.inspect(tmp_path,internal)


@pytest.fixture(scope='module')
def legacy_app(tmp_path_factory):
    folder=tmp_path_factory.mktemp('actual-02810-source')
    result=subprocess.run(['git','-c','safe.directory='+ROOT.as_posix(),'archive','--format=zip',
        '4c31b0f27f7ce9c90bd57d73f31669b63af59719'],cwd=ROOT,capture_output=True,check=True)
    with zipfile.ZipFile(io.BytesIO(result.stdout)) as source:source.extractall(folder)
    return folder


def test_real_02810_legacy_reader_is_rejected_by_contract(system,legacy_app):
    internal={'files':{'SPOT_v27.py':hashlib.sha256((legacy_app/'SPOT_v27.py').read_bytes()).hexdigest()}}
    assert not reader_contract.inspect(legacy_app,internal)
    system.data['producer_gated']=reader_contract.inspect(legacy_app,internal)
    assert not Installer(system).preflight('admin')['checks']['producer_gated_reader']


@pytest.mark.parametrize('kind',['released_02812','legacy_02810'])
def test_real_app_guard_leaves_02812_unchanged_and_disables_legacy(published_app,tmp_path,kind,legacy_app):
    folder=published_app[0] if kind=='released_02812' else legacy_app
    code=r'''
import sys,threading
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from spot_os.reader_contract import guard
sys.path.insert(0,sys.argv[2])
import SPOT_v27 as app
assert Path(app.__file__).resolve().parent==Path(sys.argv[2]).resolve()
def forbidden(*args,**kwargs):raise AssertionError('Camera access during guard test')
app.cv2.VideoCapture=forbidden
original=(app.LeptonStream.start,app.LeptonStream.reopen,app.LeptonStream.request_reopen)
if sys.argv[3]=='released_02812':
    assert guard(app) is True
    assert original==(app.LeptonStream.start,app.LeptonStream.reopen,app.LeptonStream.request_reopen)
    import spot_v4l2
    assert Path(spot_v4l2.__file__).resolve().parent==Path(sys.argv[2]).resolve()
else:
    assert guard(app) is False
    reader=app.LeptonStream('/dev/forbidden-test-device')
    reader.start();reader.request_reopen()
    assert not reader.thread.is_alive() and reader.capture is None and reader.raw_frame is None
    assert reader.reopen_done.is_set() and 'Kameraschutz' in reader.error
    try:reader.reopen()
    except RuntimeError:pass
    else:raise AssertionError('Legacy reopen must fail closed')
print('real_app_guard_ok')
'''
    result=subprocess.run([sys.executable,'-c',code,str(ROOT),str(folder),kind],cwd=tmp_path,
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'real_app_guard_ok' in result.stdout
