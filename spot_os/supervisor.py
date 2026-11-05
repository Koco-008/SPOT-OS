"""Single PAM/TTY owner and MAINPID notifier; children never reopen the TTY.

READY means a usable graphical recovery session (real manager + native frame),
not application readiness. spot-os-health.service checks the latter separately.
No HUP is ignored. A terminating HUP is converted into an explicit failure.
"""
import argparse
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid

from .common import private_directory, read
from .diagnostics import event


class Failure(RuntimeError):
    def __init__(self, component, reason, code=None):
        super().__init__(reason)
        self.component, self.reason, self.code = component, reason, code


def parent_death_signal():
    """PAM may move descendants into a logind scope outside the service cgroup.

    Keep orderly TERM propagation even when the systemd main process is killed.
    App launcher shutdown remains the manager's existing graceful drain path.
    """
    import ctypes
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'PR_SET_PDEATHSIG failed')
    if parent == 1 or os.getppid() != parent:
        raise Failure('session', 'parent_already_exited')


def notify(message):
    address = os.environ.get('NOTIFY_SOCKET')
    if not address:
        return False
    if address.startswith('@'):
        address = '\0'+address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.connect(address)
        client.sendall(message.encode())
    return True


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=3)
    if result.returncode:
        raise Failure('logind', 'query_failed', result.returncode)
    return result.stdout.strip()


def session_info(session):
    text = command('/usr/bin/loginctl', 'show-session', session,
        '-p', 'Leader', '-p', 'Active', '-p', 'Seat', '-p', 'VTNr', '-p', 'Type', '-p', 'State')
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def validate_session(config):
    if os.getuid() == 0 or os.getuid() != config['uid']:
        raise Failure('PAM', 'wrong_user')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/invalid'))
    if runtime != Path('/run/user')/str(os.getuid()):
        raise Failure('PAM', 'runtime_missing_or_unexpected')
    private_directory_existing(runtime)
    session = os.environ.get('XDG_SESSION_ID')
    if not session:
        raise Failure('PAM', 'session_id_missing')
    info = session_info(session)
    if (info.get('Leader') != str(os.getpid()) or info.get('Seat') != 'seat0'
            or info.get('VTNr') != '1' or info.get('Type') != 'wayland' or info.get('Active') != 'yes'):
        raise Failure('logind', 'session_not_active_tty1_leader')
    event('PAM_SESSION_OPENED', leader=os.getpid(), session=session, seat='seat0', vt=1, active=True)
    return runtime, session


def private_directory_existing(path):
    info = path.lstat()  # Do NOT create /run/user/$UID when PAM/logind failed.
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Failure('PAM', 'runtime_permissions')


def manager_status(path):
    from .common import encode
    import json
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(.5)
        client.connect(str(path))
        client.sendall(encode(dict(command='status')))
        data = b''
        while b'\n' not in data and len(data) < 16384:
            block = client.recv(4096)
            if not block:
                raise OSError('manager EOF')
            data += block
        return json.loads(data.split(b'\n', 1)[0])


def wayland_socket(runtime, pid):
    for path in Path(runtime).glob('wayland-*'):
        try:
            if not stat.S_ISSOCK(path.lstat().st_mode):
                continue
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(.2)
                client.connect(str(path))
                peer, uid, _ = struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                if peer==pid and uid==os.getuid():
                    return path.name
        except OSError:
            continue
    return None


def supervise(config, runtime, session, *, timeout=35, health_timeout=8, check_session=True,
              child_command=None, log_root=Path('/var/log/spot-os'), unit_name='spot-os-session.service'):
    # Keyword overrides are used by isolated integration tests only; CLI exposes none.
    invocation = os.environ.get('INVOCATION_ID') or uuid.uuid4().hex
    folder = private_directory(os.environ.get('SPOT_OS_SESSION_LOG', Path(log_root)/invocation))
    os.environ['SPOT_OS_SESSION_LOG'] = str(folder)
    event('SESSION_START_REQUESTED', pid=os.getpid())
    stopping = [None]
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, lambda n, _frame: stopping.__setitem__(0, n))
    child = None
    ready = False
    since = time.monotonic()
    last_seen = since
    next_logind = since
    try:
        private_directory_existing(runtime)
        child_env = dict(os.environ)
        child_env.pop('NOTIFY_SOCKET', None)  # MAINPID alone may report service readiness.
        child = subprocess.Popen(child_command or ['/usr/bin/dbus-run-session',
            '--dbus-daemon='+str(Path(__file__).with_name('bus_daemon.py')), '--', sys.executable,
            '-m', 'spot_os.supervisor', 'compositor'], stdin=subprocess.DEVNULL, env=child_env,
            preexec_fn=parent_death_signal)
        while not stopping[0]:
            now = time.monotonic()
            code = child.poll()
            if code is not None:
                raise Failure('DBus/Labwc', 'session_child_exited', code)
            if not (folder/'WAYLAND_SOCKET_READY.json').exists():
                compositor_pid=read(folder/'LABWC_PROCESS_STARTED.json',{}).get('pid')
                if compositor_pid:
                    display=wayland_socket(runtime,compositor_pid)
                    if display:event('WAYLAND_SOCKET_READY',wayland=display)
            if check_session and now >= next_logind:
                info = session_info(session)
                if info.get('Leader') != str(os.getpid()) or info.get('State') == 'closing':
                    raise Failure('PAM', 'session_closed')
                # Inactive VT is allowed after start: logind suspends DRM/input safely.
                next_logind = now+2
            try:
                status = manager_status(Path(runtime)/'spot-os/control.sock')
                if status.get('session_invocation') != invocation:
                    raise OSError('stale manager from another invocation')
                last_seen = now
                if status.get('milestones', {}).get('splash_presented') and not ready:
                    if not (folder/'AUTOSTART_EXECUTED.json').is_file():
                        raise Failure('autostart', 'manager_without_session_marker')
                    event('GRAPHICAL_SESSION_READY', pid=os.getpid())
                    notify('READY=1\nSTATUS=Graphical recovery ready; app boot health is checked separately')
                    ready = True
                if ready:
                    notify('WATCHDOG=1')
            except (OSError, ValueError):
                pass
            if not ready and now-since > timeout:
                component = ('DBus' if not (folder/'DBUS_SESSION_STARTED.json').exists() else
                    'Labwc' if not (folder/'WAYLAND_SOCKET_READY.json').exists() else
                    'autostart' if not (folder/'AUTOSTART_EXECUTED.json').exists() else 'manager/splash')
                raise Failure(component, 'graphical_readiness_timeout')
            if ready and now-last_seen > health_timeout:
                raise Failure('manager', 'heartbeat_timeout')
            time.sleep(.25)
        expected_stop = (stopping[0] == signal.SIGTERM and command('/usr/bin/systemctl',
            'show',unit_name,'-p','ActiveState','--value')=='deactivating')
        if not expected_stop:
            raise Failure('session', 'unexpected_signal', -stopping[0])
        event('SESSION_STOP_REQUESTED', terminating_signal='SIGTERM')
        notify('STOPPING=1')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        code = getattr(exc, 'code', None)
        event('START_FAILURE', component=getattr(exc, 'component', 'session'),
            reason=getattr(exc, 'reason', type(exc).__name__), exit_status=code if code is not None and code >= 0 else None,
            terminating_signal=signal.Signals(-code).name if code is not None and code < 0 else None,
            recovery_action='bounded_systemd_retry_then_SSH_rollback')
        notify('STATUS=Session failed; inspect spot-os doctor; SSH rollback available')
        return 1
    finally:
        # Ask manager to drain the existing launcher before ending its compositor.
        marker = read(folder/'MANAGER_STARTED.json', {})
        pid = marker.get('pid')
        if isinstance(pid, int):
            try:
                # Validate actual module and same UID before signalling (diagnostic files are not authority).
                args = Path('/proc', str(pid), 'cmdline').read_bytes().split(b'\0')
                if b'spot_os.manager' in args and Path('/proc', str(pid)).stat().st_uid == os.getuid():
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic()+35
                    while Path('/proc', str(pid)).exists() and time.monotonic() < deadline:
                        time.sleep(.2)
            except (OSError, ValueError):
                pass
        if child is not None and child.poll() is None:
            # Let Labwc/session finish first, so dbus-run-session can reap its
            # program and terminate its broker on the normal waitpid path.
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()  # broker adapter also receives parent-death TERM
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                event('DRAIN_TIMEOUT', component='DBus/Labwc', recovery_action='SSH_inspect_no_SIGKILL')
        try:
            from .backlight import restore_app_record
            restore_app_record(Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'spot/display-restore.json')
        except (OSError, ValueError, RuntimeError):
            event('BACKLIGHT_RESTORE_FAILURE', recovery_action='SSH_restore_record')


def compositor(config_dir=Path('/etc/spot-os/labwc'), executable='/usr/bin/labwc'):
    parent_death_signal()
    # This child runs inside the real fresh dbus-run-session, before Labwc.
    result = subprocess.run(['/usr/bin/dbus-send', '--session', '--print-reply',
        '--dest=org.freedesktop.DBus', '/', 'org.freedesktop.DBus.ListNames'],
        stdout=subprocess.DEVNULL, timeout=3)
    if result.returncode:
        raise Failure('DBus', 'bus_query_failed', result.returncode)
    event('DBUS_SESSION_STARTED', pid=os.getpid())
    folder = Path(os.environ['SPOT_OS_SESSION_LOG'])
    with (folder/'labwc.stderr').open('ab', buffering=0) as errors:
        child = subprocess.Popen([executable, '-C', str(config_dir)],
            stdin=subprocess.DEVNULL, stderr=errors, preexec_fn=parent_death_signal)
        event('LABWC_PROCESS_STARTED', pid=child.pid)
        signal.signal(signal.SIGTERM, lambda *_: child.terminate() if child.poll() is None else None)
        code = child.wait()
        event('LABWC_EXIT', exit_status=code if code >= 0 else None,
              terminating_signal=signal.Signals(-code).name if code < 0 else None)
        return code if code > 0 else 1  # unexpected clean exit is a failure too


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', nargs='?', choices=('session', 'compositor'), default='session')
    args = parser.parse_args()
    if args.mode == 'compositor':
        return compositor()
    # A log is allocated even if PAM/runtime checks fail before starting children.
    folder = private_directory(Path('/var/log/spot-os')/(os.environ.get('INVOCATION_ID') or uuid.uuid4().hex))
    os.environ['SPOT_OS_SESSION_LOG'] = str(folder)
    try:
        config = read('/etc/spot-os/config.json')
        runtime, session = validate_session(config)
        return supervise(config, runtime, session)
    except (OSError, ValueError, RuntimeError) as exc:
        event('START_FAILURE', component=getattr(exc, 'component', 'PAM'),
              reason=getattr(exc, 'reason', type(exc).__name__), recovery_action='SSH_doctor_rollback')
        return 1


if __name__ == '__main__':
    sys.exit(main())
