"""Actual production socket daemon + hardened PID1 exit/restart; disposable CI only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid
ROOT=Path(__file__).resolve().parents[2]


def run(*args,check=True):
    r=subprocess.run(args,text=True,capture_output=True,timeout=45)
    if check and r.returncode:raise RuntimeError(repr(args)+': '+r.stderr[-3000:]+r.stdout[-3000:])
    return r.stdout.strip()


def main():
    assert os.environ.get('GITHUB_ACTIONS')=='true' and not Path('/proc/device-tree/model').exists()
    assert Path('/proc/1/comm').read_text().strip()=='systemd'
    token='spot-handover-ci-'+uuid.uuid4().hex[:12];base=Path('/opt')/token
    units=[];proof=dict(real_pid1=True,real_production_service_loop=True,physical_hardware=False,cases=[])
    body=(ROOT/'spot_os/config/spot-os-update.service').read_text().split('[Service]',1)[1].split('[Install]',1)[0]
    def ctl(*args,**kwargs):return run('sudo','systemctl',*args,**kwargs)
    def unit(name,text):
        with tempfile.NamedTemporaryFile('w',delete=False) as f:f.write(text);source=Path(f.name)
        try:run('sudo','install','-m','644',str(source),'/etc/systemd/system/'+name)
        finally:source.unlink()
        units.append(name)
    try:
        run('sudo','install','-d','-m','755',str(base))
        for mode in ('success','legacy-error','failure'):
            folder=base/mode;run('sudo','install','-d','-m','755',str(folder))
            service=token+'-'+mode+'.service';sock=token+'-'+mode+'.socket'
            unit(sock,'[Socket]\nListenStream='+str(folder/'control.sock')+'\nSocketMode=0600\nService='+service+'\n')
            lines=[]
            for line in body.splitlines():
                if line.startswith(('ExecStart=','ReadWritePaths=','StateDirectory=','RuntimeDirectory=')):continue
                if line.startswith('RestartSec='):line='RestartSec=0'
                lines.append(line)
            command=' '.join(json.dumps(str(a)) for a in (sys.executable,ROOT/'spot_os/tests/handover_service_fixture.py',folder,service,mode))
            unit(service,'[Unit]\nRequires='+sock+'\nAfter='+sock+'\nStartLimitIntervalSec=120\nStartLimitBurst=3\n[Service]\n'+
                '\n'.join(lines)+'\nReadWritePaths='+str(folder)+'\nExecStart='+command+'\n')
            ctl('daemon-reload');ctl('start',sock,service)
            target='handover-failed' if mode=='failure' else 'success'
            deadline=time.monotonic()+35
            while True:
                state=folder/'state/transaction.json'
                # Keep production journals root-private; the external CI
                # observer reads through sudo, never widens their modes.
                raw=run('sudo','cat',str(state),check=False)
                data=json.loads(raw) if raw else {}
                if data.get('phase')==target and (target!='success' or data.get('handover_confirmed')):break
                if time.monotonic()>=deadline:
                    raise RuntimeError(run('sudo','journalctl','-u',service,'--no-pager'))
                threading.Event().wait(.1)
            status=ctl('show',service,'-p','MainPID','-p','InvocationID','-p','ActiveState','-p','SubState','-p','Result','-p','NRestarts')
            values=dict(l.split('=',1) for l in status.splitlines() if '=' in l)
            assert values['ActiveState']=='active' and values['SubState']=='running' and values['Result']=='success'
            assert int(values['NRestarts'])==1,values
            if target=='success':
                old,new=data['handover_origin'],data['handover_evidence']
                assert old['pid']!=new['pid'] and old['invocation']!=new['invocation']
                assert str(new['pid'])==values['MainPID'] and new['invocation']==values['InvocationID']
                if mode=='legacy-error':assert 'systemctl' in data['handover_previous_message']
            audit=run('sudo','cat',str(folder/'state/audit.jsonl'))
            ctl('restart',service) # subsequent ordinary daemon start keeps historical audit/status
            invocation=ctl('show',service,'-p','InvocationID','--value')
            deadline=time.monotonic()+10
            while not (folder/('resumed-'+invocation)).exists():
                if time.monotonic()>=deadline:raise RuntimeError('Successor resume not completed')
                threading.Event().wait(.1)
            if target=='success':assert run('sudo','cat',str(folder/'state/audit.jsonl'))==audit
            proof['cases'].append(dict(mode=mode,phase=data['phase'],pid1=values,verified_successor=target=='success'))
        out=ROOT/'outputs/spot-os-0.1.7';out.mkdir(parents=True,exist_ok=True)
        (out/'handover-017-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
        print(json.dumps(proof))
    finally:
        for name in reversed(units):ctl('stop',name,check=False)
        for name in units:run('sudo','rm','-f','/etc/systemd/system/'+name)
        ctl('daemon-reload')
        # /opt evidence retained for diagnosis until disposable runner teardown.


if __name__=='__main__':main()
