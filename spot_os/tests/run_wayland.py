"""Actual headless Labwc + GTK presentation protocol + unchanged stable launcher.

An instrument double supplies camera faults; no hardware or signed release is
modified. The real OS sitecustomize/Qt bridge runs in a separate GUI process.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT/'outputs/spot-os-0.1.7'
sys.path.insert(0,str(ROOT))


def wait(predicate, seconds=30):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('Timed out awaiting real Wayland state')


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='spot-os-wayland-') as temporary:
        base = Path(temporary)
        runtime = base/'runtime'
        runtime.mkdir(mode=0o700)
        config = base/'labwc'
        config.mkdir()
        shutil.copy(ROOT/'spot_os/config/labwc/rc.xml', config/'rc.xml')
        shutil.copy(ROOT/'spot_os/config/labwc/menu.xml', config/'menu.xml')
        # Configure the headless output before the OS manager connects.
        (config/'autostart').write_text('#!/bin/sh\nwlr-randr --output HEADLESS-1 --custom-mode 800x480@60Hz\n')
        (config/'autostart').chmod(0o755)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), WLR_BACKENDS='headless', WLR_RENDERER='pixman',
                   WLR_HEADLESS_OUTPUTS='1', LIBGL_ALWAYS_SOFTWARE='1', QT_QPA_PLATFORM='wayland',
                   GDK_BACKEND='wayland', PYTHONPATH=str(ROOT), XDG_SESSION_TYPE='wayland',
                   SPOT_TEST_TRIGGER=str(base/'trigger.json'),SPOT_TEST_EVENTS=str(base/'events.jsonl'))
        env.update(XCURSOR_THEME='spot-transparent',XCURSOR_PATH=str(ROOT/'spot_os/cursors'),XCURSOR_SIZE='24')
        env.pop('WAYLAND_DISPLAY', None)
        env.pop('DISPLAY', None)
        log = (OUTPUT/'wayland.log').open('w')
        compositor = subprocess.Popen(['labwc','-C',str(config)], env=env, stdout=log, stderr=log)
        manager = None
        try:
            display = wait(lambda: next((p for p in runtime.glob('wayland-*') if p.is_socket()), None))
            env['WAYLAND_DISPLAY'] = display.name
            wait(lambda: subprocess.run(['wlr-randr'], env=env, capture_output=True).returncode == 0)
            time.sleep(1)
            prefix = base/'spot'
            release = prefix/'releases/0.28.10'
            (release/'tools').mkdir(parents=True)
            (prefix/'bin').mkdir()
            (release/'VERSION').write_text('0.28.10')
            shutil.copy(ROOT/'tools/launch.py', prefix/'bin/launch.py')
            shutil.copy(ROOT/'tools/launch.py', release/'tools/launch.py')
            launcher = prefix/'bin/spot'
            import shlex
            launcher.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(prefix/'bin/launch.py'))+
                                ' --prefix '+shlex.quote(str(prefix))+'\n')
            launcher.chmod(0o755)
            (prefix/'current.json').write_text(json.dumps(dict(version='0.28.10', python=sys.executable)))
            from wayland_fixture import CONTROLLER,GUI
            (release/'spot_update_ui.py').write_text(CONTROLLER)
            (release/'SPOT_v27.py').write_text(GUI)
            manager = subprocess.Popen([sys.executable,'-m','spot_os.manager','--prefix',str(prefix)],
                                        env=env, stdout=log, stderr=log)
            status_path = runtime/'spot-os/status.json'
            def state():
                try:return json.loads(status_path.read_text())
                except (FileNotFoundError, json.JSONDecodeError):return {}
            wait(lambda: state().get('covered') and state().get('gui'))
            subprocess.run(['grim',str(OUTPUT/'01-covered-real-wayland.png')],env=env,check=True)
            from PIL import Image
            expected=Image.open(ROOT/'spot_os/artwork/splash.png').convert('RGB')
            captured=Image.open(OUTPUT/'01-covered-real-wayland.png').convert('RGB')
            assert captured.size==(800,480) and captured.tobytes()==expected.tobytes(), 'Actual compositor splash differs from original asset'
            running = wait(lambda: state() if state().get('state') == 'running' else None)
            assert running['camera'] is False
            assert running['reason'] == 'camera_unavailable'
            subprocess.run(['grim',str(OUTPUT/'02-revealed-real-wayland.png')],env=env,check=True)
            camera_proof=[]
            for available,fresh in ((True,True),(True,False),(False,False)):
                previous_attempts=state()['attempts']
                (base/'camera.json').write_text(json.dumps(dict(available=available,fresh=fresh)))
                wait(lambda:state().get('camera') is (available and fresh),seconds=5)
                value=state()
                assert value['gui'] and value['state']=='running' and not value['covered']
                assert value['attempts']==previous_attempts
                camera_proof.append(dict(available=available,fresh=fresh,camera=value['camera'],gui=value['gui']))
            # Exercise the composed OS cover -> actual signed Handoff -> ready/rollback path.
            sys.path.insert(0,str(ROOT/'tests'))
            from test_updates import keypair,make_archive
            from spot_update import atomic_json,digest
            private,public,_=keypair(base)
            handoffs=[]
            for version,behavior in [('0.28.11','healthy'),('0.28.12','crash'),('0.28.13','not_ready')]:
                fail=behavior!='healthy'
                directory=prefix/'pending'/('test-'+version)
                directory.mkdir(parents=True)
                gui_code=GUI.replace('self.picam3=S();','') if behavior=='not_ready' else GUI
                extra={'SPOT_v27.py':(b'raise SystemExit(7)\n' if behavior=='crash' else gui_code.encode()),
                    'spot_update_ui.py':CONTROLLER.encode(),
                    'tools/launch.py':(ROOT/'tools/launch.py').read_bytes(),
                    'spot_runtime.py':(ROOT/'spot_runtime.py').read_bytes(),
                    'spot_update.py':(ROOT/'spot_update.py').read_bytes()}
                archive=make_archive(directory/('spot-'+version+'.zip'),version,extra)
                manifest=dict(schema=1,kind='app',channel='stable',version=version,
                    repository='Koco-008/SPOT-Releases',asset=archive.name,size=archive.stat().st_size,
                    sha256=digest(archive),python_min=[3,11],platform='raspios-debian13-aarch64',
                    requires_driver='Lepton-Pi5/v1.0.0')
                raw=json.dumps(manifest).encode()
                (directory/'update.json').write_bytes(raw)
                (directory/'update.json.sig').write_bytes(private.sign(raw))
                atomic_json(directory/'request.json',dict(prefix=str(prefix),public_key=str(public),
                    tag='v'+version,camera_required=False))
                shutil.copy(ROOT/'tools/launch.py',directory/'launch.py')
                atomic_json(base/'trigger.json',dict(directory=str(directory)))
                phase='rolled-back' if fail else 'healthy'
                def handoff_status():
                    try:return json.loads((directory/'status.json').read_text())
                    except (FileNotFoundError,json.JSONDecodeError):return {}
                wait(lambda:handoff_status().get('phase')==phase,seconds=35)
                wait(lambda:state().get('state')=='running' and state().get('app')=='0.28.11',seconds=35)
                events=[json.loads(line) for line in (base/'events.jsonl').read_text().splitlines()]
                assert events[-1]['at'] >= state()['milestones']['last_cover_presented']
                if behavior=='not_ready':
                    assert json.loads((directory/'ready.json').read_text())['gui_ready'] is False
                handoffs.append(dict(candidate=version,behavior=behavior,phase=phase,app=state()['app'],old_exit=events[-1]))
            release=prefix/'releases/0.28.11'
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.connect(str(runtime/'spot-os/control.sock'))
                client.sendall(b'{"command":"restart"}\n')
                assert b'cover_requested' in client.recv(4096)
            wait(lambda: state().get('attempts',0) >= 2)
            wait(lambda: state().get('state') == 'running')
            # Crash the real GUI, not manager or compositor. Retried once, then final bounded recovery.
            for attempt in (3,4):
                pids = []
                for proc in Path('/proc').glob('[0-9]*'):
                    try:
                        args = (proc/'cmdline').read_bytes().split(b'\0')
                        if len(args)>1 and args[1] == os.fsencode(release/'SPOT_v27.py'):pids.append(int(proc.name))
                    except (OSError,ProcessLookupError):pass
                assert len(pids) == 1
                if attempt==3:
                    # Prove the logo survives even before the supervisor can react.
                    os.kill(manager.pid,signal.SIGSTOP)
                    try:
                        os.kill(pids[0],signal.SIGKILL)
                        time.sleep(.25)
                        subprocess.run(['grim',str(OUTPUT/'04-crash-base-real-wayland.png')],env=env,check=True)
                        base_image=Image.open(OUTPUT/'04-crash-base-real-wayland.png').convert('RGB')
                        assert base_image.tobytes()==expected.tobytes()
                    finally:
                        os.kill(manager.pid,signal.SIGCONT)
                else:
                    os.kill(pids[0],signal.SIGKILL)
                if attempt == 3:
                    wait(lambda: state().get('attempts') == 3)
                    wait(lambda: state().get('state') == 'running')
                else:
                    wait(lambda: state().get('state') == 'failed' and state().get('covered'))
            subprocess.run(['grim',str(OUTPUT/'03-recovery-real-wayland.png')],env=env,check=True)
            recovery=Image.open(OUTPUT/'03-recovery-real-wayland.png').convert('RGB')
            assert recovery.crop((0,0,800,400)).tobytes()==expected.crop((0,0,800,400)).tobytes()
            (OUTPUT/'wayland-proof.json').write_text(json.dumps(dict(
                real_compositor=True, real_presentation_ack=True, real_launcher=True,
                real_qt_bridge=True, camera_unavailable_opened=True, simulated_camera_loss=camera_proof, graceful_restart=True,
                signed_handoffs=handoffs,crash_retry_bounded=True,
                independent_crash_background=True, final=state()),indent=2))
        finally:
            if 'state' in locals():
                (OUTPUT/'wayland-last-status.json').write_text(json.dumps(state(),indent=2))
            if manager and manager.poll() is None:
                manager.terminate()
                manager.wait(timeout=40)
            if 'prefix' in locals():
                # Only fixture processes beneath this unique temporary release root.
                for proc in Path('/proc').glob('[0-9]*'):
                    try:
                        args=(proc/'cmdline').read_bytes().split(b'\0')
                        if len(args)>1 and os.fsdecode(args[1]).startswith(str(prefix)):
                            os.kill(int(proc.name),signal.SIGTERM)
                    except (OSError,ProcessLookupError):pass
            compositor.terminate()
            compositor.wait(timeout=10)
            log.close()


if __name__ == '__main__':
    main()
