"""Disposable-VM fixture. NEVER invoke on a SPOT/Pi installation."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'spot_os/tests')]
from spot_os.common import atomic,read
from spot_os.installer import Installer,STATE
from spot_os import update_contract as contract
from spot_os.update_engine import Engine
from test_installer import FakeSystem
from test_os_updates_014 import Backend
from test_boot_012 import snapshot


class FixtureSystem(FakeSystem):
    def command(self,*args,**kwargs):
        if args[0]=='/usr/bin/make' and os.environ.get('SPOT_CI_REAL_BUILD')=='1':
            result=subprocess.run(args,capture_output=True,text=True,timeout=120)
            if result.returncode:raise RuntimeError(result.stderr[-4000:])
            return result.stdout
        if 'show' in args and '--value' in args:return 'inactive'
        return super().command(*args,**kwargs)


def baseline(base):
    return {n:(list(v) if isinstance(v,tuple) else v) for n,v in snapshot(FixtureSystem(base)).items()}


def fingerprint(base):
    # JSON-safe exact bytes, mode and links; state/history append-only excluded.
    items=[(n,v) for n,v in snapshot(FixtureSystem(base)).items() if n.startswith(('/usr/','/etc/','/boot/','/home/','/run/'))]
    return hashlib.sha256(repr(sorted(items)).encode()).hexdigest()


def seed(base):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    from spot_os.package import build
    from spot_os.legacy_bridge import prepare
    system=FixtureSystem(base)
    for name,data in (('/boot/firmware/cmdline.txt',b'console=tty1 root=test rw'),('/boot/firmware/config.txt',b'[all]\n'),
        ('/boot/initrd.img-test',b'original-image'),('/etc/plymouth/plymouthd.conf',b'Theme=original\n')):
        path=system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    raw=subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),'archive','--format=zip',
        'c8c10f2c016a6a8a46ac313b75a51d8d79979889','spot_os'],cwd=ROOT)
    source=base/'old-source'
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:archive.extractall(source)
    spec=importlib.util.spec_from_file_location('spot_os._exact014',source/'spot_os/installer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.VERSION='0.1.4';module.REVISION='signed-os-updates-1'
    old=module.Installer(system,source/'spot_os');old.install('admin');old.activate('admin')
    atomic(system.path('/home/admin/.local/share/spot/current.json'),dict(version='0.28.13'))
    for name in ('/usr/local/bin/lepton_streamer','/run/lock/spot-lepton/cci.lock','/home/admin/DCIM/video.raw','/etc/ssh/sshd_config'):
        path=system.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'KEEP')
    import shutil
    source016=base/'fixture016/spot_os'
    shutil.copytree(ROOT/'spot_os',source016,ignore=shutil.ignore_patterns('tests','__pycache__'))
    (source016/'VERSION').write_text('0.1.6\n')
    (source016/'__init__.py').write_text("VERSION='0.1.6'\nREVISION='signed-os-updates-1'\n")
    release=build(base/'release',source016,target=('0.1.6','signed-os-updates-1'));archive=Path(release['artifact']).read_bytes()
    data=dict(schema=1,kind='spot-os',repository=contract.REPOSITORY,platform=contract.PLATFORM,
        tag='os-v0.1.6',version='0.1.6',revision='signed-os-updates-1',sequence=106,
        asset='spot-os-0.1.6.zip',size=len(archive),sha256=release['sha256'],source_commit='6'*40,
        channel='prerelease',notes='CI ephemeral signer only',requires_os_min='0.1.4',requires_os_max='0.1.4',
        requires_app_min='0.28.13',reader_protocol=1,engine=1,reboot=False)
    raw=json.dumps(data).encode();key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo)
    (base/'release/os-update.json').write_bytes(raw);(base/'release/os-update.json.sig').write_bytes(key.sign(raw))
    (base/'release/test-public.pem').write_bytes(public)
    payload=contract.unpack(archive,data,base/'verified')
    prepare(system,payload,archive,raw,key.sign(raw),key_provider=lambda:public)
    (base/'before.sha256').write_text(fingerprint(base))
    return dict(seeded=True,original_os='0.1.4',bridge_signed_with_ephemeral_key=True)


def engine_for(base,case):
    system=FixtureSystem(base)
    release=base/'release'
    raw=(release/'os-update.json').read_bytes();signature=(release/'os-update.json.sig').read_bytes()
    public=(release/'test-public.pem').read_bytes();data=contract.verify(raw,signature,public)
    candidate=dict(raw=raw,signature=signature,public=public,manifest=data,release={'tag_name':'os-v0.1.6'},
        archive=(release/data['asset']).read_bytes())
    class RealJournalBackend(Backend):
        def __init__(self):super().__init__();self.system=system;self.confirmations=0
        def installed(self):
            c=read(system.path('/etc/spot-os/config.json'));return c['os_version'],c['os_revision'],'0.28.13'
        def upgrade(self,folder,data):
            Installer(system,folder,target=(data['version'],data['revision']),online=True).upgrade('admin')
            if case=='interrupt-installing':os._exit(77)
        def rollback(self,*args):
            if case=='interrupt-rollback' and not (base/'var/lib/spot-os/interrupted').exists():
                original=os.replace
                def interrupt(src,dst):
                    original(src,dst)
                    if str(dst).endswith('/etc/spot-os/config.json'):
                        atomic(base/'var/lib/spot-os/interrupted',dict(interrupted=True));os._exit(78)
                os.replace=interrupt
            Installer(system).rollback_upgrade()
        def start(self):
            contender=os.environ.get('SPOT_CI_CONTENDER')
            if contender:
                subprocess.run(['/usr/bin/systemctl','start',contender],check=True,timeout=20)
        def healthy(self,*args):
            self.confirmations+=1
            if self.confirmations==1 and case in ('gui-failure','interrupt-rollback'):raise RuntimeError('GUI failed')
            return dict(gui=True,camera=not (case=='camera-failure' and self.confirmations==1))
    class Source:
        def __init__(self,*args):pass
        def check(self,*args):return candidate
        def download(self,*args):return candidate['archive']
    return Engine(RealJournalBackend(),system.path(STATE+'/updates'),lambda:public,Source)


def probe(base,user,python,producer,helper,negative):
    from spot_os import lepton,boot_facts
    from spot_os.system import System,qt_network_probe
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    caps=int(status['CapEff'].strip(),16)
    assert status['NoNewPrivs'].strip()=='1'
    assert bool(caps & (1<<7)) is not negative and caps & (1<<6)
    assert not caps & (1<<16) # no CAP_SYS_MODULE
    child=subprocess.run(['/usr/sbin/runuser','-u',user,'--','/usr/bin/python3','-c',
        "import os,json;print(json.dumps(dict(uid=os.geteuid(),status=open('/proc/self/status').read())))"],capture_output=True,text=True)
    nested_code="import json,subprocess;from pathlib import Path;s=Path('/proc/self/status').read_text();c=subprocess.run(['/usr/sbin/runuser','-u',"+repr(user)+",'--','/usr/bin/id','-u'],capture_output=True,text=True);print(json.dumps(dict(status=s,returncode=c.returncode,uid=c.stdout.strip())))"
    nested=json.loads(subprocess.check_output([python,'-I','-c',nested_code],text=True))
    nested_caps=int(next(l.split()[1] for l in nested['status'].splitlines() if l.startswith('CapEff:')),16)
    assert bool(nested_caps & (1<<7)) is not negative
    assert 'NoNewPrivs:\t1' in nested['status']
    if negative:
        assert child.returncode!=0 and nested['returncode']!=0
        return dict(nnp=1,setuid=False,setgid=True,real_runuser_denied=True,nested_python_denied=True)
    assert child.returncode==0,child.stderr
    assert nested['returncode']==0 and int(nested['uid'])!=0
    result=json.loads(child.stdout);assert result['uid']!=0 and 'NoNewPrivs:\t1' in result['status']
    lepton.BINARY=str(base/'producer');lepton.RECORD=str(base/'cci-record.json');lepton.LOCK=str(base/'cci.lock')
    boot_facts.FACTS=str(base/'facts.json')
    class ActualSystem(System):
        def command(self,*args,**kwargs):
            if 'show' in args and 'lepton-streamer.service' in args:return str(producer)
            if 'start' in args and 'spot-os-preflight.service' in args:
                return super().command('/usr/bin/systemctl','start',helper,timeout=25)
            return super().command(*args,**kwargs)
    system=ActualSystem()
    assert qt_network_probe(system,user,python)
    wayland=system.command('/usr/sbin/runuser','-u',user,'--',python,'-c',
        "from PySide6.QtCore import QLibraryInfo;from pathlib import Path;assert list((Path(QLibraryInfo.path(QLibraryInfo.PluginsPath))/'platforms').glob('*wayland*'));print('OK')")
    assert wayland=='OK'
    before=(base/'cci.lock').stat();cci=lepton.inspect(system,user)
    assert cci['compatible'],cci
    try:(base/'cci.lock').unlink();raise AssertionError('CCI parent unexpectedly writable')
    except OSError as exc:assert exc.errno==30
    after=(base/'cci.lock').stat();assert (before.st_dev,before.st_ino)==(after.st_dev,after.st_ino)
    sentinel=os.environ['SPOT_CI_MODULE_SENTINEL']
    try:visible=Path('/usr/lib/modules',sentinel).exists()
    except PermissionError:visible=False
    assert not visible,'ProtectKernelModules did not hide actual host module view'
    facts=boot_facts.inspect(system)
    assert sentinel in facts['kernels']
    host=read(base/'host-facts.json')
    for key in ('initramfs','kernel_logo','kernel','boot'):assert facts[key]==host[key]
    return dict(nnp=1,setuid=True,setgid=True,real_runuser_uid=result['uid'],nested_python_uid=int(nested['uid']),qt_wayland=True,qt_network=True,
        cci=cci,cci_inode_unchanged=True,cci_parent_read_only=True,modules_hidden=True,host_facts_match=True)


def recovery_graph(base,role,session=None,endpoint=None):
    """Actual legacy engine and PID1 Wants edge reproduce a second recovery.

    The corrected case also forces a socket activation DURING manual recovery;
    only the owner may touch transaction.json. Return/disk state must agree.
    """
    import socket
    root=base/'var/lib/spot-os/updates'
    events=base/'var/lib/spot-os/graph-events'
    class GraphBackend(Backend):
        def __init__(self):super().__init__();self.system=FixtureSystem(base)
        def rollback(self,*args):
            with events.open('a') as stream:stream.write(role+'\n');stream.flush();os.fsync(stream.fileno())
            self.version='0.1.4'
        def start(self):
            if session:
                subprocess.run(['/usr/bin/systemctl','start',session],check=True,timeout=20)
                if endpoint:
                    with socket.socket(socket.AF_UNIX) as client:
                        client.settimeout(20);client.connect(endpoint);client.sendall(b'status\n')
                        assert client.recv(1024)==b'owner-busy\n'
    backend=GraphBackend()
    if role.startswith('legacy'):
        spec=importlib.util.spec_from_file_location('spot_os._legacy_engine',base/'old-source/spot_os/update_engine.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        e=module.Engine(backend,root)
    else:e=Engine(backend,root)
    if role in ('legacy-daemon','new-daemon','new-wanted-daemon'):
        before=(root/'transaction.json').read_bytes();e.resume()
        if role in ('new-daemon','new-wanted-daemon'):
            assert (root/'transaction.json').read_bytes()==before
        if role=='new-daemon':
            with socket.socket(fileno=3) as listener:
                listener.settimeout(15)
                stream,_=listener.accept()
                with stream:stream.recv(1024);stream.sendall(b'owner-busy\n')
        return dict(daemon=role)
    e.save('rolling-back','Administrative reproduction',previous='0.1.4',version='0.1.6',camera_required=True)
    if role=='legacy-manual':
        e.recover('manual')
        assert len(events.read_text().splitlines())==2,'Legacy race was not reproduced'
        return dict(legacy_double_recovery_proven=True,phase=e.data['phase'])
    from spot_os.update_service import manual_recover
    result=manual_recover(backend,root)
    assert result['ok'] and result['phase']==read(root/'transaction.json')['phase']=='rolled-back'
    assert len(events.read_text().splitlines())==1
    return dict(single_recovery_owner=True,socket_activation_during_recovery=bool(endpoint),return_matches_disk=True,**result)


def main():
    assert os.geteuid()==0 and not Path('/proc/device-tree/model').exists(),'Disposable x86 Linux CI only'
    mode=sys.argv[1];base=Path(sys.argv[2]);case=sys.argv[3] if len(sys.argv)>3 else ''
    if mode=='seed':result=seed(base)
    elif mode=='graph':result=recovery_graph(base,*sys.argv[3:])
    elif mode=='facts':
        from spot_os.boot_facts import collect
        from spot_os.system import System
        result=collect(System());atomic(base/'facts.json',result)
    elif mode=='probe':result=probe(base,*sys.argv[3:5],int(sys.argv[5]),sys.argv[6],sys.argv[7]=='negative')
    elif mode=='contend':
        e=engine_for(base,case);before=(e.root/'transaction.json').read_bytes();e.resume()
        assert (e.root/'transaction.json').read_bytes()==before
        result=dict(competing_resume_did_not_modify_journal=True)
        atomic(base/'var/lib/spot-os/contender.json',result)
    else:
        e=engine_for(base,case)
        if mode=='resume':e.resume()
        else:
            e.dispatch(dict(action='check',prerelease=True),123,False)
            for action in ('prepare','install'):e.dispatch(dict(action=action,candidate=e.data['candidate']),123,False)
        if e.data['phase']=='handover-pending':e.resume()
        expected='success' if case=='success' else 'rolled-back'
        assert e.data['phase']==expected,e.data
        if expected=='rolled-back':assert fingerprint(base)==(base/'before.sha256').read_text()
        assert e.backend.installed()[2]=='0.28.13'
        assert (base/'usr/local/bin/lepton_streamer').read_bytes()==b'KEEP'
        assert (base/'home/admin/DCIM/video.raw').read_bytes()==b'KEEP'
        result=dict(phase=e.data['phase'],installed=e.backend.installed()[0],app='0.28.13',
            protected_files_unchanged=True,exact_rollback=expected=='rolled-back',floor=read(e.root/'floor.json'),
            native_build_in_actual_namespace=os.environ.get('SPOT_CI_REAL_BUILD')=='1')
        atomic(base/'var/lib/spot-os/result.json',result)
    print(json.dumps(result))


if __name__=='__main__':main()
