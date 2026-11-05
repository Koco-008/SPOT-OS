"""Real disposable systemd units; no production SPOT/SSH/camera unit touched."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/spot-os-0.1.7'


def run(*args,check=True):return subprocess.run(args,capture_output=True,text=True,check=check,timeout=20)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    template=(ROOT/'spot_os/config/spot-os-session.service').read_text()
    assert 'StartLimitIntervalSec=120' in template and 'StartLimitBurst=3' in template and 'RestartSec=15' in template
    name='spot-test-startlimit-'+uuid.uuid4().hex[:12]+'.service'
    target='/etc/systemd/system/'+name
    anchor=name.removesuffix('.service')+'.target'
    anchor_path='/etc/systemd/system/'+anchor
    evidence=dict(production_interval=120,production_burst=3,production_restart_delay=15,
                  test_interval=6,test_restart_delay=.15,real_systemd=True,cases=[])
    def ctl(*args,check=True):return run('sudo','systemctl',*args,check=check)
    def props():
        return dict(line.split('=',1) for line in ctl('show',name,'-p','ActiveState','-p','Result','-p','NRestarts').stdout.splitlines() if '=' in line)
    with tempfile.TemporaryDirectory(prefix='spot-test-limits-') as directory:
        directory=Path(directory);os.chmod(directory,0o755)
        failure=directory/'fail'
        fixture=directory/'fixture.py'
        fixture.write_text('from pathlib import Path\nimport sys\nsys.exit(1 if Path(sys.argv[1]).exists() else 0)\n')
        unit='[Unit]\nStartLimitIntervalSec=6\nStartLimitBurst=3\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 '+str(fixture)+' '+str(failure)+'\nRestart=on-failure\nRestartSec=.15\n'
        source=directory/'unit';source.write_text(unit)
        anchor_source=directory/'anchor';anchor_source.write_text('[Unit]\nDescription=Keep the disposable test unit referenced, as graphical.target does in production\nWants='+name+'\nAfter='+name+'\n')
        try:
            run('sudo','install','-m','644',str(source),target)
            run('sudo','install','-m','644',str(anchor_source),anchor_path);ctl('daemon-reload')
            # An unreferenced inactive oneshot is garbage-collected by systemd,
            # including its counter. Keep a real active target reference.
            ctl('start',anchor)
            # Even successful starts consume the burst, but only inside a finite window.
            for _ in range(2):ctl('start',name)
            assert ctl('start',name,check=False).returncode!=0
            assert props()['Result']=='start-limit-hit'
            evidence['cases'].append('successful_starts_count_only_inside_window')
            time.sleep(6.2);ctl('start',name)
            assert props()['Result']=='success'
            evidence['cases'].append('expired_success_window_restarts_without_reset_failed')
            # Repeated successful maintenance across windows never permanently blocks.
            for _ in range(4):time.sleep(2.2);ctl('start',name)
            evidence['cases'].append('repeated_successful_maintenance_starts')
            time.sleep(6.2);failure.write_text('fail')
            ctl('start',name,check=False)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                current=props()
                if int(current['NRestarts'])==3 and current['ActiveState']=='failed':break
                time.sleep(.1)
            before=props()
            assert before['ActiveState']=='failed' and int(before['NRestarts'])==3
            # After automatic restarts systemd may retain the ORIGINAL exit-code
            # Result instead of start-limit-hit. Prove the actual rate denial
            # and stopped retry counter, not a version-specific Result spelling.
            journal=run('sudo','journalctl','-u',name,'--no-pager').stdout
            assert 'Start request repeated too quickly.' in journal
            time.sleep(.5);assert props()['NRestarts']==before['NRestarts']
            evidence['crash_result']=before
            evidence['cases'].append('rapid_crash_series_bounded_no_restart_loop')
            failure.unlink();time.sleep(6.2);ctl('start',name)
            assert props()['Result']=='success'
            evidence['cases'].append('fixed_fault_recovers_after_window_without_reset_failed')
            evidence['passed']=True
        finally:
            (OUT/'startlimit-proof.json').write_text(json.dumps(evidence,indent=2)+'\n')
            log=run('sudo','journalctl','-u',name,'--no-pager',check=False).stdout
            (OUT/'startlimit-journal.log').write_text(log)
            ctl('stop',anchor,name,check=False)
            run('sudo','rm','-f',target,anchor_path,check=False);ctl('daemon-reload')
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
