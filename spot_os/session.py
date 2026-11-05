"""Labwc starts exactly this process, no desktop autostart environment."""
import os
from pathlib import Path
import subprocess
import sys
from .common import read
from .diagnostics import event


def main(config_path='/etc/spot-os/config.json', manager_command=None):
    config = read(config_path)
    if not config or os.getuid() != config['uid'] or os.getuid() == 0:
        raise RuntimeError('Wrong session user')
    runtime = Path(os.environ['XDG_RUNTIME_DIR']) / 'spot-os'
    # Connecting proves the compositor's socket is serving, rather than a stale inode.
    import socket
    import struct
    import select
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        probe.connect(str(Path(os.environ['XDG_RUNTIME_DIR'])/os.environ['WAYLAND_DISPLAY']))
        pid, uid, _ = struct.unpack('3i',probe.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if pid!=int(os.environ['LABWC_PID']) or uid!=os.getuid():
            raise RuntimeError('Wayland socket is not owned by this Labwc session')
        compositor = os.pidfd_open(pid)
    event('WAYLAND_SOCKET_READY', wayland=os.environ['WAYLAND_DISPLAY'])
    event('AUTOSTART_EXECUTED', pid=os.getpid())
    child = subprocess.Popen(manager_command or [sys.executable, '-m', 'spot_os.manager', '--prefix', config['prefix'],
                                                 '--runtime', str(runtime)])
    import signal
    signal.signal(signal.SIGTERM, lambda *_: child.terminate() if child.poll() is None else None)
    # Labwc deliberately detaches autostart (PPID may be 1). Bind its lifetime
    # to the verified compositor PIDFD, not to that transient shell parent.
    while child.poll() is None:
        if select.select([compositor],[],[],.25)[0]:
            event('COMPOSITOR_CONNECTION_LOST',recovery_action='graceful_manager_drain')
            child.terminate()
            break
    code = child.wait()
    os.close(compositor)
    event('MANAGER_EXIT', exit_status=code if code >= 0 else None,
          terminating_signal=signal.Signals(-code).name if code < 0 else None)
    # If manager fails, end the compositor; systemd applies the bounded session retry.
    subprocess.run(['/usr/bin/labwc', '--exit'], check=False, timeout=5)
    return code or 1


if __name__ == '__main__':
    sys.exit(main())
