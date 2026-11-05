"""Isolated systemd CI adapter: real PAM/DBus/Labwc/manager, headless output.

Only path/seat/output substitutions; never installed by the OS installer.
Production CLI has no test bypass. No physical VT or DRM is accessed here.
"""
import json
import os
from pathlib import Path
import signal
import sys
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from spot_os.supervisor import compositor, supervise, private_directory_existing, session_info
from spot_os.diagnostics import event
from spot_os.session import main as autostart


def main():
    base=Path(os.environ['SPOT_UNIT_FIXTURE'])
    mode=sys.argv[1] if len(sys.argv)>1 else 'main'
    fault=os.environ.get('SPOT_UNIT_FAULT','')
    if mode=='cleanup-orphan':
        from spot_os import ownership
        from spot_os.system import System
        ownership.UNIT=os.environ['SPOT_UNIT_NAME']
        ownership.LEDGER=str(base/'root-session-owner.json')
        original_sessions=ownership.sessions
        profile=os.environ['SPOT_UNIT_PROFILE']
        config=json.loads((base/'config.json').read_text())
        def test_identity(info,name,cfg):
            # Only substitute physical seat/TTY/PAM profile with this CI PTY.
            return (info.get('User')==str(cfg['uid']) and info.get('Service')==profile
                and info.get('Type')=='wayland' and info.get('TTY','').startswith('pts/')
                and info.get('Scope')=='session-'+name+'.scope')
        def candidates(system):
            result=[]
            for name,info in original_sessions(system):
                if info.get('Service')==profile and info.get('State')=='closing':
                    assert test_identity(info,name,config),info
                    result.append((name,dict(info,VTNr='1')))
            assert len(result)==1,result
            return result
        ownership.own=test_identity
        ownership.sessions=candidates
        print(json.dumps(ownership.cleanup(System(),config)),flush=True)
        return 0
    if mode=='bus-client':
        from spot_os.supervisor import parent_death_signal
        parent_death_signal()
        event('PRIVATE_BROKER_READY',pid=os.getpid())
        time.sleep(300)
        return 0
    if mode=='manager-splash':
        from spot_os.manager import main as manager
        config=json.loads((base/'config.json').read_text())
        sys.argv=[sys.argv[0],'--prefix',config['prefix']]
        return manager(root=base/'missing-native')
    if mode=='health':
        from spot_os.health import check
        from spot_os.supervisor import manager_status
        runtime=Path(os.environ['XDG_RUNTIME_DIR'])
        result=check({},lambda *_:manager_status(runtime/'spot-os/control.sock'),base/'health.json',
            timeout=3,stable=1)
        return 0 if result['boot_verified'] else 1
    if mode=='compositor':
        if fault=='no-socket':time.sleep(15);return 1
        if fault=='labwc-error' or fault=='retry-limit':os.environ['WLR_BACKENDS']='invalid-test-backend'
        if fault=='dbus-hup':os.kill(os.getpid(),signal.SIGHUP)
        return compositor(base/'labwc',executable='/missing/labwc' if fault=='labwc-start' else '/usr/bin/labwc')
    if mode=='autostart':
        if fault=='manager':event('AUTOSTART_EXECUTED',pid=os.getpid());return 9
        if fault=='splash':
            return autostart(base/'config.json',manager_command=[sys.executable,str(Path(__file__)),'manager-splash'])
        return autostart(base/'config.json')
    runtime=Path(os.environ['XDG_RUNTIME_DIR'])
    private_directory_existing(runtime)
    info=session_info(os.environ['XDG_SESSION_ID'])
    assert info.get('Leader')==str(os.getpid()),info
    assert info.get('Type')=='wayland',info
    # PTY session has no physical seat/VT. This is intentionally NOT a DSI proof.
    logs=base/'logs'
    logs.mkdir(exist_ok=True,mode=0o700)
    os.environ['SPOT_OS_SESSION_LOG']=str(logs/os.environ['INVOCATION_ID'])
    event('PAM_SESSION_OPENED',leader=os.getpid(),session=os.environ['XDG_SESSION_ID'])
    if fault.startswith('broker-orphan-'):
        from spot_os.supervisor import parent_death_signal,notify
        command=['/usr/bin/dbus-run-session']
        if fault.endswith('bound'):command+=['--dbus-daemon='+str(ROOT/'spot_os/bus_daemon.py')]
        child=subprocess.Popen(command+['--',sys.executable,str(Path(__file__)),'bus-client'],preexec_fn=parent_death_signal)
        marker=Path(os.environ['SPOT_OS_SESSION_LOG'])/'PRIVATE_BROKER_READY.json'
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.05)
        assert marker.exists()
        notify('READY=1')
        return child.wait()
    if fault=='runtime':runtime=base/'missing-runtime' 
    child=['/usr/bin/dbus-run-session', '--dbus-daemon='+str(ROOT/'spot_os/bus_daemon.py')]
    if fault=='dbus':child=['/usr/bin/dbus-run-session','--dbus-daemon=/missing/dbus-daemon']
    child+=['--',sys.executable,str(Path(__file__)),'compositor']
    return supervise(json.loads((base/'config.json').read_text()),runtime,None,
        check_session=False,timeout=6 if fault else 35,health_timeout=2,
        child_command=child,
        log_root=logs,unit_name=os.environ['SPOT_UNIT_NAME'])


if __name__=='__main__':sys.exit(main())
