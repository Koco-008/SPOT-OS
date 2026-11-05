"""OTA ownership, durable state, exact 0.1.4 baseline and signed bridge tests.

Filesystem/health fault injection here; real namespaces/processes are exercised
by run_ota_sandbox.py. Ephemeral test identity is never a release identity.
"""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from spot_os.common import atomic,read
from spot_os.installer import Installer,Journal,STATE
from spot_os.update_engine import Engine,MUTATING
from spot_os.update_owner import owner,OwnerBusy
from spot_os import boot_facts,legacy_bridge as bridge
from spot_os import update_contract as contract
from spot_os.package import build
from test_installer import system
from test_os_updates_014 import engine,signed,Backend
from test_ota_protection_015 import released_014
from test_boot_012 import snapshot


@pytest.fixture(scope='module')
def signed016(tmp_path_factory):
    base=tmp_path_factory.mktemp('os016-test-signing')
    source=base/'historical-source/spot_os'
    shutil.copytree(Path(__file__).parents[1],source,ignore=shutil.ignore_patterns('tests','__pycache__'))
    (source/'VERSION').write_text('0.1.6\n')
    (source/'__init__.py').write_text("VERSION='0.1.6'\nREVISION='signed-os-updates-1'\n")
    result=build(base/'build',source,target=('0.1.6','signed-os-updates-1'))
    archive=Path(result['artifact']).read_bytes()
    data=dict(schema=1,kind='spot-os',repository=contract.REPOSITORY,platform=contract.PLATFORM,
        tag='os-v0.1.6',version='0.1.6',revision='signed-os-updates-1',sequence=106,
        asset='spot-os-0.1.6.zip',size=len(archive),sha256=result['sha256'],source_commit='6'*40,
        channel='prerelease',notes='EPHEMERAL TEST ONLY',requires_os_min='0.1.4',requires_os_max='0.1.4',
        requires_app_min='0.28.13',reader_protocol=1,engine=1,reboot=False)
    raw=json.dumps(data,sort_keys=True).encode();key=Ed25519PrivateKey.generate()
    return dict(raw=raw,signature=key.sign(raw),manifest=data,archive=archive,release={'tag_name':'os-v0.1.6'},
        public=key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))


def test_single_owner_stale_daemon_cannot_recover_or_save(engine):
    engine.save('rolling-back','owned',previous='0.1.4',version='0.1.5')
    before=(engine.root/'transaction.json').read_bytes()
    other=Engine(engine.backend,engine.root,engine.key_provider)
    with owner(engine.root):
        with pytest.raises(OwnerBusy):
            other.dispatch(dict(action='check',prerelease=False),123,False)
        other.resume()
        assert (engine.root/'transaction.json').read_bytes()==before
        assert not engine.backend.events
    other.resume()
    assert other.data['phase']=='rolled-back'


def test_owner_remains_locked_through_session_start_and_health(engine):
    original=engine.backend.start
    def start():
        with pytest.raises(OwnerBusy):
            with owner(engine.root):pytest.fail('second recovery owner')
        # Simulate socket activation triggered by the new GUI.
        Engine(engine.backend,engine.root,engine.key_provider).resume()
        original()
    engine.backend.start=start
    engine.dispatch(dict(action='install',candidate=engine.data['candidate']),123,False)
    if engine.data['phase']=='handover-pending':engine.resume()
    assert engine.data['phase']=='success' and 'rollback' not in engine.backend.events


@pytest.mark.skipif(os.name!='posix',reason='Unix lock and identity operations')
def test_owner_real_process_and_untrusted_inode(engine,tmp_path):
    script="from pathlib import Path;from spot_os.update_owner import owner,OwnerBusy\ntry:\n with owner(Path(__import__('sys').argv[1])):raise SystemExit(9)\nexcept OwnerBusy:pass"
    with owner(engine.root):
        assert subprocess.run([__import__('sys').executable,'-c',script,str(engine.root)]).returncode==0
    path=engine.root/'owner.lock';path.chmod(0o666)
    with pytest.raises(PermissionError):
        with owner(engine.root):pass
    path.unlink();path.symlink_to(tmp_path/'target')
    with pytest.raises(OSError):
        with owner(engine.root):pass


@pytest.mark.parametrize('phase',['preparing','restoring','new','restored'])
def test_incomplete_installation_is_not_integrity_success(system,phase):
    atomic(system.path(STATE+'/installation.json'),dict(phase=phase,entries=[]))
    result=Installer(system).check()
    assert not result['ok'] and not result['installation_complete'] and not result['files_ok']


@pytest.mark.parametrize('phase',sorted(MUTATING|{'recovery-failed','awaiting-exit'}))
@pytest.mark.skipif(os.name!='posix',reason='Target journal filesystem')
def test_inflight_or_failed_ota_never_reports_completed_installation(system,phase):
    installer=Installer(system);installer.install('admin')
    atomic(system.path(STATE+'/updates/transaction.json'),dict(phase=phase))
    result=installer.check()
    assert result['files_ok'] and result['installation_complete']
    assert not result['transaction_complete'] and not result['ok']


@pytest.mark.parametrize('phase',['upgrading','restoring'])
@pytest.mark.skipif(os.name!='posix',reason='Target journal filesystem')
def test_upgrade_journal_blocks_false_success(system,phase):
    installer=Installer(system);installer.install('admin')
    atomic(system.path(STATE+'/upgrade.json'),dict(phase=phase,entries=[]))
    assert not installer.check()['ok']


@pytest.mark.parametrize('result',[dict(gui=False,camera=True),dict(gui=True,camera=False)])
def test_unconfirmed_gui_or_camera_rolls_back_instead_of_success(engine,result):
    original=engine.backend.healthy
    attempts=[]
    def healthy(*args):
        attempts.append(1)
        return result if len(attempts)==1 else original(*args)
    engine.backend.healthy=healthy
    engine.dispatch(dict(action='install',candidate=engine.data['candidate']),123,False)
    assert engine.data['phase']=='rolled-back' and engine.backend.version=='0.1.4'


def test_recovery_wrong_installed_version_remains_failed(engine):
    engine.save('rolling-back','interrupted',previous='0.1.4',version='0.1.5')
    engine.backend.rollback=lambda *args:None
    engine.backend.version='0.1.5'
    engine.resume()
    assert read(engine.root/'transaction.json')['phase']=='recovery-failed'


@pytest.mark.skipif(os.name!='posix',reason='Real filesystem rollback')
@pytest.mark.parametrize('failure',['pam','replace'])
def test_pam_and_atomic_replace_failure_restore_exact_old_os(system,released_014,monkeypatch,failure):
    cls,source=released_014;cls(system,source).install('admin');cls(system,source).activate('admin')
    before=snapshot(system)
    replace=os.replace;armed=[True]
    def fault(src,dst):
        if armed[0] and ((failure=='pam' and '/etc/pam.d/' in str(dst)) or
                         (failure=='replace' and str(dst).endswith('/etc/spot-os/config.json'))):
            armed[0]=False;raise OSError(30,'injected read-only parent')
        return replace(src,dst)
    monkeypatch.setattr(os,'replace',fault)
    with pytest.raises(OSError):Installer(system).upgrade('admin')
    assert snapshot(system)==before and Journal(system,'upgrade').data['phase']=='restored'
    assert Installer(system).check()['ok']


def facts(system,monkeypatch,**change):
    monkeypatch.setenv('SPOT_OS_ISOLATED_PREFLIGHT','1')
    path=system.path('/proc/sys/kernel/random/boot_id');path.parent.mkdir(parents=True);path.write_text('this-boot')
    data=dict(schema=1,boot='this-boot',kernel=__import__('platform').release(),created=time.time(),initramfs=True,kernel_logo=True)
    data.update(change);atomic(system.path(boot_facts.FACTS),data)
    return data


@pytest.mark.skipif(os.name!='posix' or getattr(os,'geteuid',lambda:1)()!=0,reason='Root-only fact identity; real namespace CI runs this as root')
@pytest.mark.parametrize('change',[{},dict(created=0),dict(boot='other'),dict(kernel='other'),dict(created=time.time()+1000)])
def test_isolated_kernel_facts_are_fresh_root_owned_not_blanket_success(system,monkeypatch,change):
    data=facts(system,monkeypatch,**change)
    if change:
        with pytest.raises(ValueError):boot_facts.inspect(system)
    else:
        assert boot_facts.inspect(system)==data
        path=system.path(boot_facts.FACTS);path.chmod(0o644)
        with pytest.raises(PermissionError):boot_facts.inspect(system)


@pytest.mark.skipif(os.name!='posix',reason='Actual published installer/Unix filesystem')
@pytest.mark.parametrize('failure',[None,'make','health'])
def test_signed_014_bridge_to_016_preserves_app_driver_data_and_exact_rollback(system,released_014,signed016,tmp_path,failure):
    cls,source=released_014;old=cls(system,source);old.install('admin');old.activate('admin')
    atomic(system.path('/home/admin/.local/share/spot/current.json'),dict(version='0.28.13'))
    for name in ('/usr/local/bin/lepton_streamer','/run/lock/spot-lepton/cci.lock','/home/admin/DCIM/video','/etc/ssh/sshd_config'):
        path=system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'UNCHANGED')
    protected=snapshot(system)
    payload=contract.unpack(signed016['archive'],signed016['manifest'],tmp_path/'verified')
    bridge.prepare(system,payload,signed016['archive'],signed016['raw'],signed016['signature'],key_provider=lambda:signed016['public'])
    assert Installer(system).check()['ok'] and read(system.path('/etc/spot-os/config.json'))['os_version']=='0.1.4'
    before=snapshot(system)
    class JournalBackend(Backend):
        def installed(self):
            c=read(system.path('/etc/spot-os/config.json'));return c['os_version'],c['os_revision'],'0.28.13'
        def upgrade(self,folder,data):
            if failure=='make':system.fail='make'
            try:Installer(system,folder,target=(data['version'],data['revision']),online=True).upgrade('admin')
            finally:system.fail=None
        def rollback(self,*args):Installer(system).rollback_upgrade()
    class Source:
        def __init__(self,*args):pass
        def check(self,*args):return signed016
        def download(self,*args):return signed016['archive']
    b=JournalBackend();b.fail='health' if failure=='health' else None
    engine=Engine(b,system.path(STATE+'/updates'),lambda:signed016['public'],Source)
    engine.dispatch(dict(action='check',prerelease=True),123,False)
    for action in ('prepare','install'):engine.dispatch(dict(action=action,candidate=engine.data['candidate']),123,False)
    if engine.data['phase']=='handover-pending':engine.resume()
    assert engine.data['phase']==('success' if failure is None else 'rolled-back'),engine.data
    assert read(engine.root/'floor.json')['sequence']==106
    if failure is None:
        assert not system.path(bridge.UPDATE).exists() and b.installed()[0]=='0.1.6'
        outcome=Installer(system).rollback_upgrade()
        assert outcome['health_pending'] and not outcome['rolled_back']
        assert not Installer(system).check()['transaction_complete']
    else:
        assert Installer(system).check()['ok']
    assert snapshot(system)==before
    for name,value in protected.items():
        if name.startswith(('/home/','/run/lock/','/usr/local/bin/lepton','/etc/ssh/')):assert snapshot(system)[name]==value


def test_new_units_preserve_boundaries_and_do_not_pull_updater_on_session_start():
    source=Path(__file__).parents[1]/'config'
    text=(source/'spot-os-update.service').read_text()
    for line in ('ProtectKernelModules=yes','NoNewPrivileges=yes','RestrictSUIDSGID=yes','ProtectSystem=strict','ReadOnlyPaths=/run'):
        assert line in text
    rw=next(l for l in text.splitlines() if l.startswith('ReadWritePaths='))
    assert '/etc/pam.d' in rw and '/run/lock/spot-lepton/cci.lock' in rw
    assert '/run/lock/spot-lepton ' not in rw and '/boot' not in rw
    session=(source/'spot-os-session.service').read_text()
    assert 'spot-os-update.service' not in session
    assert 'spot-os-update.socket' in session


@pytest.mark.skipif(os.name!='posix',reason='Exact signed Unix bridge rollback')
def test_bridge_undo_restores_original_014_files(system,released_014,signed016,tmp_path):
    cls,source=released_014;cls(system,source).install('admin');cls(system,source).activate('admin')
    atomic(system.path('/home/admin/.local/share/spot/current.json'),dict(version='0.28.13'))
    payload=contract.unpack(signed016['archive'],signed016['manifest'],tmp_path/'verified')
    before=snapshot(system)
    bridge.prepare(system,payload,signed016['archive'],signed016['raw'],signed016['signature'],key_provider=lambda:signed016['public'])
    original=system.command
    system.command=lambda *a,**kw:'inactive' if '--value' in a else original(*a,**kw)
    assert bridge.undo(system)['ok']
    assert snapshot(system)==before and Installer(system).check()['ok']


@pytest.mark.skipif(os.name!='posix',reason='Target bridge safety checks')
@pytest.mark.parametrize('failure',['signature','archive','source','app','override','owner'])
def test_bridge_fails_closed_without_replacing_existing_os(system,released_014,signed016,tmp_path,failure):
    cls,source=released_014;cls(system,source).install('admin')
    atomic(system.path('/home/admin/.local/share/spot/current.json'),dict(version='0.28.12' if failure=='app' else '0.28.13'))
    payload=contract.unpack(signed016['archive'],signed016['manifest'],tmp_path/'verified')
    if failure=='source':(payload/'boot_facts.py').write_text('tampered')
    if failure=='override':
        p=system.path('/etc/systemd/system/spot-os-update.service.d/other.conf');p.parent.mkdir(parents=True);p.write_text('[Service]\nProtectSystem=no\n')
    before=snapshot(system)
    from cryptography.exceptions import InvalidSignature
    with (owner(system.path(STATE+'/updates')) if failure=='owner' else nullcontext()):
        with pytest.raises((ValueError,RuntimeError,InvalidSignature)):
            bridge.prepare(system,payload,signed016['archive']+(b'x' if failure=='archive' else b''),signed016['raw'],
                b'x'*64 if failure=='signature' else signed016['signature'],key_provider=lambda:signed016['public'])
    assert snapshot(system)==before


def test_host_inventory_distinguishes_missing_and_present_kernel(system,monkeypatch):
    monkeypatch.setattr('platform.release',lambda:'exact-kernel')
    monkeypatch.setattr(shutil,'which',lambda _: '/usr/sbin/update-initramfs')
    for name,text in (('/proc/sys/kernel/random/boot_id','boot'),('/boot/config-exact-kernel','CONFIG_LOGO=y\nCONFIG_DRM_VC4=m\n'),
                      ('/usr/share/initramfs-tools/hooks/plymouth','hook')):
        path=system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    data=boot_facts.collect(system)
    assert data['kernel_logo'] is True and data['initramfs'] is False
    system.path('/lib/modules/exact-kernel').mkdir(parents=True)
    assert boot_facts.collect(system)['initramfs'] is True
    system.path('/usr/share/initramfs-tools/hooks/plymouth').unlink()
    assert boot_facts.collect(system)['initramfs'] is False


@pytest.mark.skipif(os.name!='posix',reason='Exact old verifier in isolated Linux subprocess')
def test_installed_014_verifier_accepts_signed_016_before_any_new_code(released_014,signed016,tmp_path):
    _,source=released_014
    for name,data in (('manifest',signed016['raw']),('sig',signed016['signature']),('public',signed016['public']),('archive',signed016['archive'])):
        (tmp_path/name).write_bytes(data)
    code='import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from spot_os import update_contract as c;b=Path(sys.argv[2]);m=c.verify((b/"manifest").read_bytes(),(b/"sig").read_bytes(),(b/"public").read_bytes());c.compatible(m,"0.1.4","0.28.13",{"version":"0.1.5","sequence":105},True);p=c.unpack((b/"archive").read_bytes(),m,b/"old-verified");assert (p/"VERSION").read_text().strip()=="0.1.6";print("exact old verifier accepted")'
    result=subprocess.run([__import__('sys').executable,'-I','-c',code,str(source.parent),str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'exact old verifier accepted' in result.stdout


@pytest.mark.parametrize('phase,fields',[('success',{'version':'0.1.6'}),('rolled-back',{'previous':'0.1.3'})])
def test_terminal_transaction_must_agree_with_installed_version(system,phase,fields):
    atomic(system.path('/etc/spot-os/config.json'),dict(os_version='0.1.4'))
    atomic(system.path(STATE+'/updates/transaction.json'),dict(phase=phase,**fields))
    assert not Installer(system).check()['transaction_complete']


def test_documented_python_verifier_and_readonly_hardware_capture_parse():
    import ast,re
    source=Path(__file__).parents[1]
    script=(source/'hardware_acceptance_016.sh').read_text()
    ast.parse(script.split("<<'PY'\n",1)[1].split('\nPY\n',1)[0])
    doc=(source/'MIGRATION_014_016.md').read_text()
    snippets=re.findall(r"python3 -I -c '([^']+)'",doc)
    assert len(snippets)==1
    ast.parse(snippets[0])
