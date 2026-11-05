"""systemd socket activation. Fixed, bounded protocol authenticated by SO_PEERCRED."""
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import threading
from .common import encode,atomic,read
from .update_contract import strict_json,trust,trusted_file
from .update_backend import Backend
from .update_engine import Engine,MUTATING

SOCKET=Path('/run/spot-os-update/control.sock')


def peer(stream,uid):
    pid,actual,_=struct.unpack('3i',stream.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    if actual!=uid or pid<=1:raise PermissionError('OS request peer not the configured session account')
    return pid


def serve_request(stream,engine,uid):
    stream.settimeout(3)
    try:
        pid=peer(stream,uid)
        raw=bytearray()
        while b'\n' not in raw:
            block=stream.recv(min(4096,8193-len(raw)))
            if not block:raise ValueError('Incomplete OS request')
            raw.extend(block)
            if len(raw)>8192:raise ValueError('OS request limit')
        line,rest=bytes(raw).split(b'\n',1)
        if rest:raise ValueError('One OS request per connection')
        request=strict_json(line)
        # Status remains readable for bootstrap/misconfiguration errors. Changes
        # require the independently provisioned anchor on every request.
        if request!={'action':'status'}:trust()
        response=engine.dispatch(request,pid)
    except Exception as exc:
        from .update_engine import error_message
        response=dict(error=error_message(exc)[:400])
    stream.sendall(encode(response))


def state_directory():
    trusted_file('/etc/spot-os/config.json')
    state=Path('/var/lib/spot-os/updates')
    state.mkdir(mode=0o700,exist_ok=True)
    for path in (state,*state.rglob('*')):
        info=path.lstat()
        if info.st_uid!=0 or info.st_mode&0o077 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise PermissionError('OS staging/state must be root-private without links')
    return state


def main():
    if os.geteuid()!=0:raise PermissionError('OS update daemon must be root-owned')
    if os.environ.get('LISTEN_PID')!=str(os.getpid()) or os.environ.get('LISTEN_FDS')!='1':
        raise RuntimeError('OS daemon requires its systemd socket')
    # Validate fixed root state paths before importing state or accepting work.
    state=state_directory()
    backend=Backend();engine=Engine(backend)
    # Recovery uses the root journal, not network data or a GUI. Missing signer
    # configuration never prevents restoration of a previously trusted snapshot.
    listener=socket.socket(fileno=3)
    if listener.family!=socket.AF_UNIX or listener.getsockname()!=str(SOCKET):raise ValueError('Wrong OS activation socket')
    listener.settimeout(1)
    address=os.environ.get('NOTIFY_SOCKET')
    if address:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as notify:
            notify.connect('\0'+address[1:] if address.startswith('@') else address)
            notify.sendall(b'READY=1')
    # Successor validation needs PID1's READY processing; identity evidence has
    # a bounded activating->running wait. No transaction is resumed before this.
    recovery=threading.Thread(target=engine.resume,name='spot-os-boot-recovery',daemon=False)
    engine.thread=recovery;recovery.start()
    stopped=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stopped.set())
    signal.signal(signal.SIGINT,lambda *_:stopped.set())
    while not stopped.is_set():
        if backend.handover_requested and not (engine.thread and engine.thread.is_alive()):
            from .update_handover import EXIT_STATUS
            return EXIT_STATUS
        try:stream,_=listener.accept()
        except socket.timeout:continue
        with stream:
            try:serve_request(stream,engine,backend.config['uid'])
            except (OSError,ValueError):pass
    if engine.thread:engine.thread.join(timeout=130)
    return 0


def manual_recover(backend,root=None):
    """Explicit SSH administration only; one new attempt, never an IPC action."""
    from .installer import Installer
    for name in ('spot-os-update.service','spot-os-update.socket'):
        result=backend.system.command('/usr/bin/systemctl','show',name,'-p','ActiveState','--value')
        if result not in ('inactive','failed'):raise RuntimeError('Stop the OS update service AND socket before manual recovery')
    Installer(backend.system).require_stopped()
    from .update_owner import owner
    directory=state_directory() if root is None else root
    with owner(directory):
        engine=Engine(backend,directory)
        if engine.data.get('phase') not in MUTATING|{'recovery-failed'}:
            raise RuntimeError('No failed/interrupted OS transaction to recover')
        # Root explicitly authorizes one bounded retry; trust/floor stay intact.
        engine.save('rollback-stopping','Einmalige administrative Wiederherstellung',rollback_attempts=0)
        engine.recover('Administrative Wiederherstellung')
        persisted=read(engine.root/'transaction.json')
        if persisted != engine.data:raise RuntimeError('Recovery journal changed outside its owner')
        return dict(ok=persisted['phase']=='rolled-back',phase=persisted['phase'],message=persisted['message'])
