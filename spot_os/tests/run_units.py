"""Real systemd/PAM/PTY and graphical-process integration on a disposable CI VM.

Requires passwordless sudo and systemd PID 1. Creates ONLY uniquely named test
units/PAM profile and a test prefix; never installs or starts production units,
never opens a physical VT, never uses DRM. Real DSI/seat0 remains hardware QA.
"""
import json
import os
from pathlib import Path
import pty
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/spot-os-0.1.7'
sys.path.insert(0,str(ROOT))
from spot_os.supervisor import manager_status
from wayland_fixture import GUI,CONTROLLER


def run(*args, check=True):
    return subprocess.run(args,text=True,capture_output=True,check=check,timeout=60)


def ctl(*args,check=True):return run('sudo','systemctl',*args,check=check)


def wait(predicate,seconds=40):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.2)
    raise AssertionError('systemd integration timeout')


def main():
    (ROOT/'spot_os/bus_daemon.py').chmod(0o755)
    assert Path('/proc/1/comm').read_text().strip()=='systemd','CI must run on a real systemd VM'
    OUT.mkdir(parents=True,exist_ok=True)
    evidence=dict(kind='real-systemd-PAM-PTY-headless',physical_DSI_verified=False,tests=[])
    unit_prefix='spot-os-ci-'+uuid.uuid4().hex[:10]
    units=[]
    pam=Path('/etc/pam.d')/unit_prefix
    with tempfile.TemporaryDirectory(prefix='spot-unit-') as temporary:
        base=Path(temporary)
        os.chmod(base,0o700)
        master,slave=pty.openpty()
        tty=os.ttyname(slave)
        user=run('id','-un').stdout.strip()
        uid=os.getuid()
        runtime=Path('/run/user')/str(uid)
        ssh_server=ssh_client=None
        def add_unit(name, text):
            unit=unit_prefix+'-'+name+'.service'
            source=base/unit
            source.write_text(text)
            run('sudo','install','-m','644',str(source),'/etc/systemd/system/'+unit)
            units.append(unit)
            ctl('daemon-reload')
            return unit
        def props(unit):
            lines=ctl('show',unit,'-p','ActiveState','-p','SubState','-p','Result',
                '-p','ExecMainCode','-p','ExecMainStatus','-p','MainPID','-p','NRestarts').stdout
            return dict(line.split('=',1) for line in lines.splitlines() if '=' in line)
        try:
            probe=run('sudo',sys.executable,str(ROOT/'spot_os/tests/root_checks.py'),str(base),user,sys.executable)
            evidence['tests'].append(dict(case='real_root_runtime_and_QtNetwork',result=json.loads(probe.stdout)))
            import socket
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1',0));ssh_port=port_socket.getsockname()[1]
            for name in ('host','client'):
                run('ssh-keygen','-q','-t','ed25519','-N','','-f',str(base/name))
            ssh_config=base/'sshd.conf'
            ssh_config.write_text(f"Port {ssh_port}\nListenAddress 127.0.0.1\nHostKey {base}/host\nPidFile {base}/sshd.pid\nAuthorizedKeysFile {base}/client.pub\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM yes\nStrictModes no\nAllowUsers {user}\nPrintMotd no\nLogLevel ERROR\n")
            run('sudo','install','-d','-m','755','/run/sshd')
            ssh_log=(base/'sshd.log').open('w')
            ssh_server=subprocess.Popen(['sudo','/usr/sbin/sshd','-D','-e','-f',str(ssh_config)],stdout=ssh_log,stderr=ssh_log)
            ssh_args=['ssh','-i',str(base/'client'),'-p',str(ssh_port),'-o','BatchMode=yes',
                '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null',user+'@127.0.0.1']
            wait(lambda:run(*ssh_args,'true',check=False).returncode==0,seconds=10)
            ssh_client=subprocess.Popen(ssh_args+['echo SPOT_SSH_READY; sleep 600'],stdout=subprocess.PIPE,stderr=ssh_log,text=True)
            import select
            wait(lambda:select.select([ssh_client.stdout],[],[],0)[0],seconds=10)
            assert ssh_client.stdout.readline().strip()=='SPOT_SSH_READY'
            evidence['tests'].append(dict(case='real_loopback_SSH_same_UID',uid=uid,connected=True))
            # Reproduce the old failure independently of PAM/Labwc: same TTY execution
            # settings + post command, real PID1 executor, actual allocated terminal.
            for hangup in ('yes','no'):
                text=f'''[Unit]
Description=SPOT old TTY regression {hangup}
[Service]
Type=exec
RemainAfterExit=yes
User={user}
StandardInput=tty
TTYPath={tty}
TTYVHangup={hangup}
TTYReset=yes
TTYVTDisallocate=no
ExecStart=/usr/bin/sleep 300
ExecStartPost=/usr/bin/sleep 2
Restart=no
TimeoutStartSec=10
'''
                unit=add_unit('tty-'+hangup,text)
                ctl('start',unit,check=False)
                time.sleep(.5)
                state=props(unit)
                evidence['tests'].append(dict(case='post_with_vhangup_'+hangup,result=state))
                if hangup=='yes':
                    assert state['ExecMainCode']=='2' and state['ExecMainStatus']==str(signal.SIGHUP),state
                    assert state['Result']=='success' and state['SubState']=='exited',state
                else:
                    assert state['ActiveState']=='active',state
                    occupied=add_unit('occupied-tty',f'''[Service]
Type=exec
User={user}
StandardInput=tty-fail
TTYPath={tty}
TTYVHangup=no
ExecStart=/usr/bin/sleep 3
''')
                    assert ctl('start',occupied,check=False).returncode!=0
                    failure=props(occupied)
                    assert failure['Result']=='exit-code' and failure['ExecMainStatus']=='208',failure
                    evidence['tests'].append(dict(case='occupied_PTY_refused_without_hangup',result=failure))
                ctl('stop',unit,check=False)
            # New dedicated PAM stack, never the production name.
            run('sudo','install','-m','644',str(ROOT/'spot_os/config/pam-spot-os'),str(pam))
            prefix=base/'prefix'
            def drained():
                # Closing a PAM scope can terminate a socket owner before its
                # finally block runs. An orphaned inode is not a live manager.
                # Production startup removes it only after taking manager.lock.
                from spot_os.manager import locked
                try:
                    manager_status(runtime/'spot-os/control.sock')
                    return False
                except (OSError,ValueError):
                    return not locked(prefix/'app.lock') and not locked(runtime/'spot-os/manager.lock')
            release=prefix/'releases/0.28.10'
            (release/'tools').mkdir(parents=True)
            (prefix/'bin').mkdir()
            (prefix/'update.lock').touch()  # present in a real installed stable prefix
            (prefix/'current.json').write_text(json.dumps(dict(version='0.28.10',python=sys.executable)))
            shutil.copy(ROOT/'tools/launch.py',prefix/'bin/launch.py')
            shutil.copy(ROOT/'tools/launch.py',release/'tools/launch.py')
            (release/'VERSION').write_text('0.28.10')
            (release/'SPOT_v27.py').write_text(GUI)
            (release/'spot_update_ui.py').write_text(CONTROLLER)
            launcher=prefix/'bin/spot'
            launcher.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+
                shlex.quote(str(prefix/'bin/launch.py'))+' --prefix '+shlex.quote(str(prefix))+'\n')
            launcher.chmod(0o755)
            config=base/'config.json'
            config.write_text(json.dumps(dict(uid=uid,user=user,prefix=str(prefix))))
            labwc=base/'labwc'
            labwc.mkdir()
            for name in ('rc.xml','menu.xml'):shutil.copy(ROOT/'spot_os/config/labwc'/name,labwc/name)
            autostart=labwc/'autostart'
            autostart.write_text('#!/bin/sh\nwlr-randr --output HEADLESS-1 --custom-mode 800x480@60Hz\nexec '+
                shlex.quote(sys.executable)+' '+shlex.quote(str(ROOT/'spot_os/tests/unit_entry.py'))+' autostart\n')
            autostart.chmod(0o755)
            # Keep the production unit and override only hardware/path prerequisites.
            # No ExecStartPost is added: MAINPID notifier is exercised through PID1.
            template=(ROOT/'spot_os/config/spot-os-session.service').read_text().replace('@USER@',user).replace('@HOME@',str(base))
            # Dependency directives cannot be reset by empty later assignments.
            # Remove only physical/production dependencies in this isolated copy.
            template='\n'.join(line for line in template.splitlines() if not line.startswith(
                ('After=','Requires=','Wants=','Conflicts=','ConditionPathExists=','OnFailure=')))+'\n'
            for fault in ('','dbus','labwc-start','labwc-error','dbus-hup','runtime','no-socket',
                          'autostart','manager','splash','gui','broker-orphan-old','broker-orphan-bound','manager-lost','kill-main','pam-close','socket-lost','retry-limit'):
                (release/'SPOT_v27.py').write_text(GUI.replace('self.picam3=S();','') if fault=='gui' else GUI)
                autostart.write_text('#!/bin/sh\n' if fault=='autostart' else
                    '#!/bin/sh\nwlr-randr --output HEADLESS-1 --custom-mode 800x480@60Hz\nexec '+
                    shlex.quote(sys.executable)+' '+shlex.quote(str(ROOT/'spot_os/tests/unit_entry.py'))+' autostart\n')
                autostart.chmod(0o755)
                unit_name=unit_prefix+'-session-'+(fault or 'success')+'.service'
                text=template+f'''
[Unit]
ConditionPathExists=
After=
Requires=
Wants=
Conflicts=
OnFailure=
[Service]
PAMName={unit_prefix}
TTYPath={tty}
ExecStart=
ExecStart={sys.executable} {ROOT}/spot_os/tests/unit_entry.py
Restart=no
Environment=PYTHONPATH={ROOT}
Environment=SPOT_UNIT_FIXTURE={base} SPOT_UNIT_FAULT={fault}
Environment=SPOT_UNIT_NAME={unit_name}
Environment=WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 LIBGL_ALWAYS_SOFTWARE=1
Environment=SPOT_TEST_TRIGGER={base}/trigger.json SPOT_TEST_EVENTS={base}/events.jsonl
Environment=LD_LIBRARY_PATH={os.environ.get('LD_LIBRARY_PATH','')}
Environment=XCURSOR_THEME=spot-transparent XCURSOR_PATH={ROOT}/spot_os/cursors XCURSOR_SIZE=24
'''
                if not fault:text+='\n[Unit]\nStartLimitIntervalSec=0\n'
                if fault=='retry-limit':text+='\n[Service]\nRestart=on-failure\nRestartSec=.5\n'
                unit=add_unit('session-'+(fault or 'success'),text)
                outcome=ctl('start',unit,check=False)
                state=props(unit)
                if fault.startswith('broker-orphan-'):
                    assert outcome.returncode==0 and state['ActiveState']=='active',(outcome.stderr,state)
                    invocation=ctl('show',unit,'-p','InvocationID','--value').stdout.strip()
                    marker=json.loads((base/'logs'/invocation/'PAM_SESSION_OPENED.json').read_text())
                    sid=marker['session']
                    scope=run('loginctl','show-session',sid,'-p','Scope','--value').stdout.strip()
                    group=ctl('show',scope,'-p','ControlGroup','--value').stdout.strip()
                    members=Path('/sys/fs/cgroup'+group+'/cgroup.procs')
                    os.kill(int(state['MainPID']),signal.SIGKILL)
                    wait(lambda:props(unit)['ActiveState']=='failed')
                    if fault.endswith('old'):
                        def orphaned():
                            pids=members.read_text().split() if members.exists() else []
                            return pids if len(pids)==1 and Path('/proc',pids[0],'comm').read_text().strip()=='dbus-daemon' else None
                        leftovers=wait(orphaned,seconds=10)
                        # Exercise the production pidfd/scope cleanup on the real
                        # PAM scope, substituting only the CI PTY/profile identity.
                        result=run('sudo','env','PYTHONPATH='+str(ROOT),
                            'SPOT_UNIT_FIXTURE='+str(base),'SPOT_UNIT_NAME='+unit,'SPOT_UNIT_PROFILE='+unit_prefix,
                            sys.executable,str(ROOT/'spot_os/tests/unit_entry.py'),'cleanup-orphan',check=False)
                        assert result.returncode==0,(result.stdout,result.stderr)
                        assert json.loads(result.stdout)['cleaned']==[scope],result.stdout
                    wait(lambda:not members.exists() or not members.read_text().strip(),seconds=10)
                    assert ssh_client.poll() is None
                    evidence['tests'].append(dict(case=fault,scope=scope,scope_empty=True,SSH_connection_alive=True))
                    ctl('stop',unit,check=False)
                    continue
                if fault in ('','gui' ,'manager-lost','kill-main','pam-close','socket-lost'):
                    assert outcome.returncode==0 and state['ActiveState']=='active',(outcome.stderr,state)
                    def running():
                        try:
                            value=manager_status(runtime/'spot-os/control.sock')
                            return value if value.get('state')=='running' else None
                        except (OSError,ValueError):return None
                    if fault=='gui':
                        health_unit=add_unit('health-'+fault,f'''[Service]
Type=oneshot
User={user}
Environment=SPOT_UNIT_FIXTURE={base} XDG_RUNTIME_DIR={runtime} PYTHONPATH={ROOT}
Environment=LD_LIBRARY_PATH={os.environ.get('LD_LIBRARY_PATH','')}
ExecStart={sys.executable} {ROOT}/spot_os/tests/unit_entry.py health
''')
                        assert ctl('start',health_unit,check=False).returncode!=0
                        assert props(health_unit)['Result']=='exit-code'
                        assert props(unit)['ActiveState']=='active','GUI health must not kill graphical recovery'
                        assert not json.loads((base/'health.json').read_text())['boot_verified']
                        evidence['tests'].append(dict(case='GUI_health_timeout_preserves_graphics',result=props(health_unit)))
                        ctl('stop',unit,check=False)
                        wait(drained)
                        continue
                    status=wait(running)
                    if not fault:
                        health_unit=add_unit('health-success',f'''[Service]
Type=oneshot
User={user}
Environment=SPOT_UNIT_FIXTURE={base} XDG_RUNTIME_DIR={runtime} PYTHONPATH={ROOT}
Environment=LD_LIBRARY_PATH={os.environ.get('LD_LIBRARY_PATH','')}
ExecStart={sys.executable} {ROOT}/spot_os/tests/unit_entry.py health
''')
                        assert ctl('start',health_unit).returncode==0
                        assert json.loads((base/'health.json').read_text())['boot_verified']
                        evidence['tests'].append(dict(case='stable_GUI_health_success',result=props(health_unit)))
                    assert status['gui'] and status['milestones']['splash_presented']<status['milestones']['app_process_started']
                    invocation=ctl('show',unit,'-p','InvocationID','--value').stdout.strip()
                    milestones=base/'logs'/invocation
                    for name in ('PAM_SESSION_OPENED','DBUS_SESSION_STARTED','LABWC_PROCESS_STARTED',
                        'WAYLAND_SOCKET_READY','AUTOSTART_EXECUTED','MANAGER_STARTED','SPLASH_PRESENTED',
                        'SPOT_GUI_READY','SPOT_RUNNING','GRAPHICAL_SESSION_READY'):
                        assert (milestones/(name+'.json')).is_file(),name
                    if not fault:
                        # Exercise the same real PAM unit repeatedly. Unlike the
                        # old drained() check, prove the actual logind cgroup is
                        # empty/gone, including the private dbus-daemon.
                        def identity():
                            current=ctl('show',unit,'-p','InvocationID','--value').stdout.strip()
                            marker=json.loads((base/'logs'/current/'PAM_SESSION_OPENED.json').read_text())
                            sid=marker['session']
                            scope=run('loginctl','show-session',sid,'-p','Scope','--value').stdout.strip()
                            group=ctl('show',scope,'-p','ControlGroup','--value').stdout.strip()
                            assert scope=='session-'+sid+'.scope' and group.endswith('/'+scope)
                            return dict(session=sid,scope=scope,group=group)
                        def empty(previous):
                            path=Path('/sys/fs/cgroup'+previous['group']+'/cgroup.procs')
                            return not path.exists() or not path.read_text().strip()
                        for action in ('stop-start','restart','stop-start','restart'):
                            previous=identity()
                            if action=='stop-start':
                                ctl('stop',unit)
                                wait(lambda:empty(previous),seconds=10)
                                ctl('start',unit)
                            else:ctl('restart',unit)
                            wait(running)
                            wait(lambda:empty(previous),seconds=10)
                            assert ssh_client.poll() is None,'Same-user SSH connection was terminated'
                            evidence['tests'].append(dict(case='real_PAM_'+action,previous=previous,
                                old_scope_empty=True,SSH_connection_alive=True))
                        # Continue failure injection against the latest invocation.
                        state=props(unit)
                        invocation=ctl('show',unit,'-p','InvocationID','--value').stdout.strip()
                        milestones=base/'logs'/invocation
                    # A HUP on Labwc is its documented reload, not a crash.
                    labwc_pid=json.loads((milestones/'LABWC_PROCESS_STARTED.json').read_text())['pid']
                    os.kill(labwc_pid,signal.SIGHUP)
                    time.sleep(.5)
                    assert props(unit)['ActiveState']=='active'
                    # Independent failure injections after real full-chain readiness.
                    if fault=='manager-lost':os.kill(status['pid'],signal.SIGTERM)
                    elif fault=='socket-lost':(runtime/'spot-os/control.sock').unlink()
                    elif fault=='pam-close':
                        session=json.loads((milestones/'PAM_SESSION_OPENED.json').read_text())['session']
                        run('sudo','loginctl','terminate-session',session)
                    else:os.kill(int(state['MainPID']),signal.SIGKILL if fault=='kill-main' else signal.SIGHUP)
                    wait(lambda:props(unit)['ActiveState']=='failed')
                    if not fault:
                        failure=json.loads((milestones/'START_FAILURE.json').read_text())
                        assert failure['terminating_signal']=='SIGHUP'
                else:
                    assert outcome.returncode!=0,(fault,state)
                    if fault=='retry-limit':
                        # systemd versions may retain the preceding startup
                        # exit-code result when the subsequent restart is denied.
                        wait(lambda:int(props(unit)['NRestarts'])>=3 and props(unit)['ActiveState']=='failed')
                        state=props(unit)
                        count=state['NRestarts']
                        time.sleep(2)
                        assert props(unit)['NRestarts']==count and int(count)<=3,state
                        journal=run('sudo','journalctl','-u',unit,'--no-pager').stdout
                        assert 'Start request repeated too quickly' in journal
                        assert sum(('Starting '+unit) in line for line in journal.splitlines())==3,journal
                    assert state['Result']!='success' and state['ExecMainStatus']!='0',(fault,state)
                evidence['tests'].append(dict(case=fault or 'complete_chain_then_HUP',result=props(unit)))
                ctl('stop',unit,check=False)
                # Wait for the PAM session, manager and its drain to finish before reusing PTY.
                wait(drained,seconds=40)
                time.sleep(.5)
            assert ssh_client.poll() is None
            assert run(*ssh_args,'true').returncode==0
            evidence['tests'].append(dict(case='SSH_new_connection_after_all_faults',connected=True))
            evidence['passed']=True
        finally:
            if ssh_client is not None:
                ssh_client.terminate();ssh_client.wait(timeout=10)
            if ssh_server is not None:
                # Stop only this test daemon, never the host SSH service.
                pidfile=base/'sshd.pid'
                if pidfile.exists():run('sudo','kill','-TERM',pidfile.read_text().strip(),check=False)
                ssh_server.wait(timeout=10)
            journals=[]
            details={}
            for path in (base/'logs').glob('*/*'):
                if path.is_file():
                    data=path.read_text(errors='replace')[-12000:]
                    details[str(path.relative_to(base/'logs'))]=data
                    if not evidence.get('passed'):print(str(path.relative_to(base))+'\n'+data,flush=True)
            (OUT/'systemd-details.json').write_text(json.dumps(details,indent=2)+'\n')
            for unit in units:
                ctl('stop',unit,check=False)
                journal=run('sudo','journalctl','-u',unit,'--no-pager','-n','120',check=False).stdout
                journals.append(unit+'\n'+journal)
                if not evidence.get('passed'):print(unit+'\n'+journal,flush=True)
                run('sudo','rm','-f','/etc/systemd/system/'+unit,check=False)
            run('sudo','rm','-f',str(pam),check=False)
            ctl('daemon-reload',check=False)
            os.close(slave);os.close(master)
            (OUT/'systemd-proof.json').write_text(json.dumps(evidence,indent=2)+'\n')
            (OUT/'systemd-journal.log').write_text('\n'.join(journals))
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
