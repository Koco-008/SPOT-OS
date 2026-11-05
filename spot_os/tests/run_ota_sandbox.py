"""Real systemd namespaces + signed journal transactions on a disposable CI VM.

No physical camera is simulated as hardware proof. Inventory/session responses
in the journal matrix are doubles; filesystem access, CAPs, runuser, Qt imports,
producer executable/inode inspection, PID1 activation and owner contention are
real. All system units are unique test names, never production SPOT units.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/spot-os-0.1.7'
FIXTURE=ROOT/'spot_os/tests/ota_sandbox_fixture.py'


def run(*args,check=True,timeout=90):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if check and result.returncode:raise RuntimeError(repr(args)+': '+result.stderr[-5000:]+result.stdout[-2000:])
    return result


def main():
    assert sys.platform=='linux' and Path('/proc/1/comm').read_text().strip()=='systemd'
    assert not Path('/proc/device-tree/model').exists(),'Never run on BMA-Pi5'
    assert os.environ.get('GITHUB_ACTIONS')=='true','Requires a disposable GitHub Actions VM'
    OUT.mkdir(parents=True,exist_ok=True)
    token='spot-ota-ci-'+uuid.uuid4().hex[:12]
    base=Path('/opt')/token
    module=Path('/usr/lib/modules')/token
    units=[];producer=None
    proof=dict(real_systemd=True,real_mount_namespaces=True,physical_hardware=False,cases=[])
    body=(ROOT/'spot_os/config/spot-os-update.service').read_text().split('[Service]',1)[1].split('[Install]',1)[0]
    targets=next(l.split('=',1)[1].split() for l in body.splitlines() if l.startswith('ReadWritePaths='))
    def sudo(*args,**kw):return run('sudo',*args,**kw)
    def ctl(*args,**kw):return sudo('systemctl',*args,**kw)
    def fixture(mode,path,*args,check=True):return sudo(sys.executable,str(FIXTURE),mode,str(path),*args,check=check)
    def unit(name,text):
        full=token+'-'+name+'.service'
        with tempfile.NamedTemporaryFile(mode='w',delete=False) as stream:
            stream.write(text);source=Path(stream.name)
        try:sudo('install','-m','644',str(source),'/etc/systemd/system/'+full)
        finally:source.unlink()
        units.append(full);ctl('daemon-reload');return full
    def exec_args(*args):return ' '.join(json.dumps(str(a)) for a in (sys.executable,FIXTURE,*args))
    def execute(name,text,expected=0):
        name=unit(name,text)
        result=ctl('start',name,check=False,timeout=120)
        status=ctl('show',name,'-p','ExecMainStatus','--value').stdout.strip()
        if status!=str(expected) or (expected==0 and result.returncode):
            raise AssertionError(ctl('status',name,'--no-pager',check=False).stdout+sudo('journalctl','-u',name,'--no-pager').stdout)
        return name
    def sandbox(command,writes,negative=False,extra=''):
        lines=[]
        for line in body.splitlines():
            if line.startswith(('RestartForceExitStatus=','SuccessExitStatus=')):continue
            if line.startswith(('NotifyAccess=','TimeoutStartSec=','ExecStart=','Environment=','ReadWritePaths=')):continue
            if line.startswith('Type='):line='Type=oneshot'
            elif line.startswith('Restart='):line='Restart=no'
            elif line.startswith(('StateDirectory=','RuntimeDirectory=')):line=line.split('=',1)[0]+'='+token
            elif negative and line.startswith(('AmbientCapabilities=','CapabilityBoundingSet=')):
                line=line.replace(' CAP_SETUID','').replace('=CAP_SETUID ','=')
            lines.append(line)
        # ExecStart uses C-style JSON escaping, never shell interpretation.
        cmd=' '.join(json.dumps(str(a)) for a in command)
        return '[Service]\n'+'\n'.join(lines)+'\nTimeoutStartSec=100\nEnvironment=SPOT_OS_ISOLATED_PREFLIGHT=1\nReadWritePaths='+' '.join(map(str,writes))+'\nExecStart='+cmd+'\n'+extra+'\n'
    try:
        sudo('install','-d','-m','755',str(base),str(module))
        probe=base/'probe';sudo('install','-d','-m','755',str(probe))
        user='nobody';account=pwd.getpwnam(user)
        lock=probe/'cci.lock';sudo('touch',str(lock));sudo('chown','0:'+str(account.pw_gid),str(lock));sudo('chmod','660',str(lock))
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/'producer.c'
            sys.path.insert(0,str(ROOT))
            from spot_os.lepton import MARKERS,REVIEWED_COMMIT,PROTOCOL
            source.write_text('#include <fcntl.h>\n#include <unistd.h>\n#include <stdio.h>\nint main(){puts('+json.dumps(b'\n'.join(MARKERS).decode())+');int fd=open('+json.dumps(str(lock))+',O_RDWR);if(fd<0)return 2;while(1)sleep(1);}\n')
            sudo('cc',str(source),'-o',str(probe/'producer'))
        digest=hashlib.sha256((probe/'producer').read_bytes()).hexdigest()
        record=json.dumps(dict(commit=REVIEWED_COMMIT,protocol=PROTOCOL,binary_sha256=digest))
        sudo('/usr/bin/python3','-c','from pathlib import Path;import sys;Path(sys.argv[1]).write_text(sys.argv[2])',str(probe/'cci-record.json'),record)
        sudo('chmod','644',str(probe/'cci-record.json'));sudo('chmod','755',str(probe/'producer'))
        producer=subprocess.Popen(['sudo',str(probe/'producer')],stdout=subprocess.DEVNULL)
        # sudo can remain a monitor. Locate the real fixture executable PID.
        import time
        time.sleep(.4)
        pids=sudo('pgrep','-f','^'+str(probe/'producer')+'$').stdout.split()
        assert len(pids)==1,pids
        producer_pid=pids[0]
        host=fixture('facts',probe);sudo('cp',str(probe/'facts.json'),str(probe/'host-facts.json'))
        # The production helper intentionally hides /home, including this CI
        # checkout. Put its actual inventory modules in a root-owned /opt tree.
        tool=base/'host-probe/spot_os';sudo('install','-d','-m','755',str(tool))
        for name in ('__init__.py','common.py','system.py','boot_facts.py','runtime.py'):
            sudo('install','-m','644',str(ROOT/'spot_os'/name),str(tool/name))
        program='import sys;sys.path.insert(0,'+repr(str(tool.parent))+');from spot_os import boot_facts;from spot_os.system import System;boot_facts.FACTS='+repr(str(probe/'facts.json'))+';boot_facts.publish(System())'
        helper_body=(ROOT/'spot_os/config/spot-os-preflight.service').read_text().split('[Service]',1)[1]
        helper_body='\n'.join(l for l in helper_body.splitlines() if not l.startswith(('ExecStart=','ReadWritePaths=')))
        helper=unit('facts','[Service]\n'+helper_body+'\nReadWritePaths='+str(probe)+'\nExecStart='+' '.join(json.dumps(str(a)) for a in (sys.executable,'-I','-c',program))+'\n')
        env='Environment=SPOT_CI_MODULE_SENTINEL='+token
        for negative in (True,False):
            command=[sys.executable,FIXTURE,'probe',probe,user,sys.executable,producer_pid,helper,'negative' if negative else 'positive']
            name=execute('caps-'+str(negative),sandbox(command,[lock],negative,env))
            journal=sudo('journalctl','-u',name,'--no-pager','-o','cat').stdout
            proof['cases'].append(dict(case='missing_SETUID_negative' if negative else 'actual_caps_Qt_CCI_and_host_preflight',journal=journal[-12000:]))
        # Inspect PID1's effective merged legacy unit, not merely string asserts.
        old=run('git','-c','safe.directory='+ROOT.as_posix(),'show','c8c10f2c016a6a8a46ac313b75a51d8d79979889:spot_os/config/spot-os-update.service').stdout
        old=old.replace('spot-os-update.socket',token+'-unused.socket').replace('spot-os-session.service',token+'-unused-session.service')
        policy=unit('bridge-policy',old)
        from spot_os.legacy_bridge import overlays,UPDATE
        overlay=overlays(ROOT/'spot_os')[UPDATE].decode()
        # Only the entrypoint is substituted: no production service is started.
        overlay='\n'.join('ExecStart=/usr/bin/true' if l.startswith('ExecStart=') and l!='ExecStart=' else l for l in overlay.splitlines())+'\n'
        with tempfile.NamedTemporaryFile(mode='w',delete=False) as stream:
            stream.write(overlay);overlay_source=Path(stream.name)
        sudo('install','-d','-m','755','/etc/systemd/system/'+policy+'.d')
        try:sudo('install','-m','644',str(overlay_source),'/etc/systemd/system/'+policy+'.d/50-bridge.conf')
        finally:overlay_source.unlink()
        ctl('daemon-reload')
        merged=ctl('show',policy,'-p','ReadWritePaths','-p','AmbientCapabilities','-p','CapabilityBoundingSet',
            '-p','ProtectKernelModules','-p','NoNewPrivileges').stdout
        properties=dict(l.split('=',1) for l in merged.splitlines() if '=' in l)
        assert properties['ProtectKernelModules']=='yes' and properties['NoNewPrivileges']=='yes'
        assert set(properties['ReadWritePaths'].split())==set(targets),properties
        assert set(properties['AmbientCapabilities'].split())=={'cap_setuid','cap_setgid'},properties
        assert 'cap_sys_module' not in properties['CapabilityBoundingSet']
        proof['cases'].append(dict(case='actual_014_unit_plus_signed_bridge_policy',effective=properties))
        # Parent write allowlist is mapped ONE DIRECTORY AT A TIME into an
        # otherwise read-only /opt root. No writable whole fake-root exemption.
        cases=('success','pam-ro','atomic-ro','gui-failure','camera-failure','interrupt-installing','interrupt-rollback')
        for case in cases:
            root=base/case;sudo('install','-d','-m','755',str(root))
            fixture('seed',root)
            for target in targets:
                path=root/target.lstrip('/')
                if target.endswith('/cci.lock'):continue # seeded with preserved inode
                sudo('install','-d','-m','755',str(path))
            writes=[root/t.lstrip('/') for t in targets if not (case=='pam-ro' and t=='/etc/pam.d') and not (case=='atomic-ro' and t=='/etc/spot-os')]
            contender=unit('contender-'+case,'[Service]\nType=oneshot\nExecStart='+' '.join(json.dumps(str(a)) for a in (sys.executable,FIXTURE,'contend',root,case))+'\n')
            command=[sys.executable,FIXTURE,'run',root,case]
            expected=77 if case=='interrupt-installing' else 78 if case=='interrupt-rollback' else 0
            extra='Environment=SPOT_CI_CONTENDER='+contender+'\nEnvironment=SPOT_CI_REAL_BUILD=1'
            execute('transaction-'+case,sandbox(command,writes,extra=extra),expected)
            if expected:
                execute('resume-'+case,sandbox([sys.executable,FIXTURE,'resume',root,'resume'],writes,extra=extra))
            result=json.loads(sudo('cat',str(root/'var/lib/spot-os/result.json')).stdout)
            assert result['floor']['sequence']==106
            proof['cases'].append(dict(case=case,**result))
        for variant in ('legacy','bridge','fixed'):
            legacy=variant=='legacy'
            graph=base/(variant+'-graph');sudo('install','-d','-m','755',str(graph));fixture('seed',graph)
            role='legacy-daemon' if legacy else 'new-wanted-daemon' if variant=='bridge' else 'new-daemon'
            daemon=unit(role,'[Service]\nType=oneshot\nExecStart='+exec_args('graph',graph,role)+'\n')
            if variant in ('legacy','bridge'):
                session=unit(variant+'-session','[Unit]\nWants='+daemon+'\nAfter='+daemon+'\n[Service]\nType=oneshot\nExecStart=/usr/bin/true\n')
                result=fixture('graph',graph,'legacy-manual' if legacy else 'new-manual',session)
            else:
                sock=token+'-graph.socket';endpoint=str(graph/'update.sock')
                with tempfile.NamedTemporaryFile(mode='w',delete=False) as stream:
                    stream.write('[Socket]\nListenStream='+endpoint+'\nService='+daemon+'\n');socket_source=Path(stream.name)
                try:sudo('install','-m','644',str(socket_source),'/etc/systemd/system/'+sock)
                finally:socket_source.unlink()
                units.append(sock);ctl('daemon-reload')
                session=unit('fixed-session','[Unit]\nWants='+sock+'\nAfter='+sock+'\n[Service]\nType=oneshot\nExecStart=/usr/bin/true\n')
                result=fixture('graph',graph,'new-manual',session,endpoint)
            proof['cases'].append(dict(case=variant+'_recovery_activation_graph',**json.loads(result.stdout)))
        proof['passed']=True
    finally:
        journals='\n'.join(sudo('journalctl','-u',u,'--no-pager',check=False).stdout for u in units)
        (OUT/'ota-sandbox-journal.log').write_text(journals)
        (OUT/'ota-sandbox-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
        for name in reversed(units):
            assert name.startswith(token+'-')
            ctl('stop',name,check=False);sudo('rm','-f','/etc/systemd/system/'+name,check=False)
            sudo('rm','-rf','--','/etc/systemd/system/'+name+'.d',check=False)
        if producer:
            sudo('pkill','-f','^'+str(base/'probe/producer')+'$',check=False)
            producer.wait(timeout=10)
        ctl('daemon-reload',check=False)
        assert base.parent==Path('/opt') and base.name.startswith('spot-ota-ci-')
        sudo('rm','-rf','--',str(base),str(module),'/var/lib/'+token,'/run/'+token,check=False)
    print(json.dumps(proof,indent=2))


if __name__=='__main__':main()
