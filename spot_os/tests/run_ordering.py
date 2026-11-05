"""Real PID1 ordering and root probe; V4L2 format and GUI are explicit doubles.

Uses /dev/null to test actual writable producer FD identity without installing a
kernel module or touching a camera. Existing headless/PAM CI tests the real GUI.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/spot-os-0.1.7'


def run(*args,check=True):
    result=subprocess.run(args,capture_output=True,text=True,timeout=45)
    if check and result.returncode:raise RuntimeError(result.stderr or result.stdout)
    return result


def ctl(*args,check=True):return run('sudo','systemctl',*args,check=check)


def prop(unit,key):return ctl('show',unit,'-p',key,'--value').stdout.strip()


def main():
    assert Path('/proc/1/comm').read_text().strip()=='systemd'
    OUT.mkdir(parents=True,exist_ok=True)
    evidence=dict(real_systemd=True,real_root_probe=True,simulated_V4L2=True,simulated_GUI=True,cases=[])
    names=[]
    journals=[]
    prefix='spot-order-'+uuid.uuid4().hex[:8]
    user=run('id','-un').stdout.strip()
    try:
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
            parent=Path(temporary)
            for name,delay,budget in [('cold-boot',.3,2),('delayed-format',1,2),
                                      ('streamer-failed',0,2),('format-timeout',5,.8),('hung-probe',.1,10)]:
                base=parent/name;base.mkdir()
                (base/'case.json').write_text(json.dumps(dict(name=name,delay=delay,budget=budget)))
                producer=prefix+'-'+name+'-producer.service'
                probe=prefix+'-'+name+'-probe.service'
                consumer=prefix+'-'+name+'-consumer.service'
                environment=(f'Environment=SPOT_ORDERING_FIXTURE={base} SPOT_ORDERING_PRODUCER={producer}\n'
                    f'Environment=PYTHONPATH={ROOT}\n')
                command=shlex.quote(sys.executable)+' '+shlex.quote(str(ROOT/'spot_os/tests/ordering_fixture.py'))
                source=(ROOT/'spot_os/config/spot-os-lepton-ready.service').read_text()
                source=source.replace('After=spot-os-prepare.service lepton-streamer.service','After='+producer)
                source=source.replace('lepton-streamer.service',producer).replace('spot-os-session.service',consumer)
                source=source.replace('ExecStart=/usr/local/bin/spot-os camera-ready','ExecStart='+command+' probe')
                source=source.replace('UMask=0077','UMask=0077\n'+environment)
                if name=='hung-probe':source=source.replace('TimeoutStartSec=14','TimeoutStartSec=2')
                # Take the production weak dependencies/ordering literally, with
                # only the unrelated physical/PAM preparation prerequisites removed.
                lines=(ROOT/'spot_os/config/spot-os-session.service').read_text().splitlines()
                after=next(x for x in lines if x.startswith('After='))
                wants=next(x for x in lines if x.startswith('Wants='))
                after='After='+' '.join(x for x in after[6:].split() if x in ('lepton-streamer.service','spot-os-lepton-ready.service'))
                wants='Wants='+' '.join(x for x in wants[6:].split() if x in ('lepton-streamer.service','spot-os-lepton-ready.service'))
                dependencies=(after+'\n'+wants).replace('lepton-streamer.service',producer).replace('spot-os-lepton-ready.service',probe)
                units={producer:f'[Service]\nType=simple\nUser={user}\nExecStart={command} producer\nRestart=no\n'+environment,
                    probe:source,
                    consumer:f'[Unit]\n{dependencies}\n[Service]\nType=notify\nNotifyAccess=main\nUser={user}\nExecStart={command} consumer\nRestart=no\n'+environment}
                for unit,text in units.items():
                    path=base/unit;path.write_text(text)
                    run('sudo','install','-m','644',str(path),'/etc/systemd/system/'+unit)
                    names.append(unit)
                ctl('daemon-reload')
                started=time.monotonic()
                ctl('start',consumer)
                elapsed=time.monotonic()-started
                actual=json.loads((base/'consumer.json').read_text())
                assert actual['gui'] and prop(consumer,'ActiveState')=='active'
                assert prop(producer,'NRestarts')=='0'
                if name in ('cold-boot','delayed-format'):
                    result=json.loads((base/'probe.json').read_text())
                    formatted=json.loads((base/'format.json').read_text())
                    assert result['format_ready'] and formatted['at']<result['at']<actual['at']
                    # Probe scheduling can lag the producer. Compare against
                    # the whole start transaction, not the probe's later epoch.
                    assert elapsed>=delay-.1,(name,result)
                    if name=='cold-boot':
                        old_pid=prop(producer,'MainPID')
                        old_probe=result['at']
                        ctl('restart',consumer)
                        newer=json.loads((base/'probe.json').read_text())
                        assert newer['at']>old_probe and newer['format_ready']
                        assert prop(producer,'MainPID')==old_pid and prop(producer,'NRestarts')=='0'
                        evidence['cases'].append(dict(case='session-restart',gate_reran=True,same_producer_PID=True))
                else:
                    assert actual['camera_error'] and not actual['format_ready']
                    assert elapsed<6,(name,elapsed)
                    if name=='hung-probe':assert prop(probe,'Result')=='timeout'
                    else:assert not json.loads((base/'probe.json').read_text())['format_ready']
                evidence['cases'].append(dict(case=name,elapsed=elapsed,consumer=actual,producer_restarts=0,
                    probe_result=prop(probe,'Result')))
                for unit in (consumer,probe,producer):
                    ctl('stop',unit,check=False)
                    journals.append(unit+'\n'+run('sudo','journalctl','-u',unit,'--no-pager',check=False).stdout)
            evidence['passed']=True
    finally:
        for unit in reversed(names):
            ctl('stop',unit,check=False)
            run('sudo','rm','-f','/etc/systemd/system/'+unit,check=False)
        ctl('daemon-reload',check=False)
        (OUT/'ordering-proof.json').write_text(json.dumps(evidence,indent=2)+'\n')
        (OUT/'ordering-journal.log').write_text('\n'.join(journals))
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
