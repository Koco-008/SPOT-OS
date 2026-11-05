"""Independent unprivileged supervisor; never installs releases or deletes locks."""
import argparse
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import selectors
import signal
import socket
import struct
import stat
import subprocess
import time
from dataclasses import asdict, replace

from .common import MAX_MESSAGE, atomic, encode, private_directory, read
from .model import Evidence, Policy, State
from .diagnostics import event

LOG = logging.getLogger('spot-os')


def boot_limited(path=Path('/run/spot-os/boot-blocked.json'), boot=Path('/proc/sys/kernel/random/boot_id')):
    try:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_size>4096:
            return False
        data=read(path,{})
        return data.get('blocked') is True and data.get('boot')==boot.read_text().strip()
    except FileNotFoundError:
        return False


def locked(path):
    if not Path(path).exists():
        return False
    import fcntl
    with Path(path).open('rb') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def identity(pid, prefix, version, proc=Path('/proc')):
    """SO_PEERCRED PID must be the real GUI child for the CURRENT release."""
    try:
        current = read(prefix / 'current.json', {})
        if current.get('version') != version:
            return False
        args = (proc / str(pid) / 'cmdline').read_bytes().split(b'\0')
        target = (prefix / 'releases' / version / 'SPOT_v27.py').resolve()
        if len(args) < 2 or Path(os.fsdecode(args[1])).resolve() != target:
            return False
        env = dict(part.split(b'=', 1) for part in
                   (proc / str(pid) / 'environ').read_bytes().split(b'\0') if b'=' in part)
        parent = int((proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[1])
        if int(env.get(b'SPOT_LAUNCH_PID', b'0')) != parent:
            return False
        if Path(os.fsdecode(env.get(b'SPOT_LAUNCH_PREFIX', b''))).resolve() != prefix.resolve():
            return False
        parent_args = (proc / str(parent) / 'cmdline').read_bytes().split(b'\0')
        if len(parent_args) < 2 or Path(os.fsdecode(parent_args[1])).name != 'launch.py':
            return False
        candidate = Path(os.fsdecode(parent_args[1])).resolve()
        permitted = candidate == (prefix / 'bin/launch.py').resolve()
        if candidate.parent.parent == (prefix / 'pending').resolve():
            # Copied handoff launcher must match signed release's launcher bytes.
            permitted = hashlib.sha256(candidate.read_bytes()).digest() == hashlib.sha256(
                target.parent.joinpath('tools/launch.py').read_bytes()).digest()
        return permitted and locked(prefix / 'app.lock')
    except (OSError, ValueError, KeyError):
        return False


class Manager:
    def __init__(self, prefix, runtime, root=Path(__file__).parent):
        self.prefix, self.root = Path(prefix).resolve(), Path(root).resolve()
        self.runtime = private_directory(runtime)
        self.policy = Policy()
        self.selector = selectors.DefaultSelector()
        self.token = secrets.token_hex(32)
        self.clients = {}
        self.launcher = None
        self.gui = None
        self.last_evidence = 0
        self.evidence = Evidence()
        self.busy = True
        self.sleeping = False
        self.power_state = 'unknown'
        self.covered = False
        self.cover_requested = True
        self.cover_generation = 1
        self.toplevel_mapped = False
        self.cover_waiters = []
        self.stopping = False
        self.restart_after_cover = False
        self.restart_sent = None
        self.update_origin = None
        self.milestones = {'manager_started': time.monotonic()}
        self.policy.state = State.SESSION

    def start(self):
        # Socket only becomes stale after owning the manager lock in main().
        self.path = self.runtime / 'control.sock'
        self.path.unlink(missing_ok=True)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.server.listen(8)
        event('MANAGER_STARTED', pid=os.getpid())
        event('MANAGER_SOCKET_READY', pid=os.getpid())
        self.server.setblocking(False)
        self.selector.register(self.server, selectors.EVENT_READ, self.accept)
        self.splash = subprocess.Popen([str(self.root / 'native/spot-splash'),
            str(self.root / 'artwork/splash.png')], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, bufsize=0)
        event('SPLASH_PROCESS_STARTED', pid=self.splash.pid)
        os.set_blocking(self.splash.stdout.fileno(), False)
        self.splash_buffer = b''
        self.selector.register(self.splash.stdout, selectors.EVENT_READ, self.splash_event)
        self.started_at = time.monotonic()

    def accept(self, server):
        client, _ = server.accept()
        pid, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid not in (0, os.getuid()) or len(self.clients) >= 8:
            client.close()
            return
        client.setblocking(False)
        self.clients[client] = dict(pid=pid, uid=uid, buffer=b'')
        self.selector.register(client, selectors.EVENT_READ, self.client_event)

    def drop(self, client):
        if client in self.clients:
            self.selector.unregister(client)
            del self.clients[client]
            client.close()
        self.cover_waiters = [c for c in self.cover_waiters if c is not client]
        if self.gui is client:
            self.gui = None
            self.evidence = Evidence()
            self.busy = True
            self.restore_brightness()

    def restore_brightness(self):
        """Use the app's existing exact-value record after crash; no new privilege."""
        from .backlight import restore_app_record
        try:
            restore_app_record(Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'spot/display-restore.json')
        except (OSError, ValueError, RuntimeError):
            self.policy.state = State.RECOVERY
            self.policy.reason = 'backlight_restore_failed'
            LOG.error('Backlight restoration needs SSH maintenance')

    def reply(self, client, value):
        try:
            # Messages are bounded, local and tiny. Never wait for a stalled peer.
            client.sendall(encode(value))
        except (BlockingIOError, OSError):
            self.drop(client)

    def client_event(self, client):
        info = self.clients[client]
        try:
            data = client.recv(4096)
            if not data:
                self.drop(client)
                return
            info['buffer'] += data
            if len(info['buffer']) > MAX_MESSAGE:
                raise ValueError('Oversized protocol input')
            while b'\n' in info['buffer']:
                line, info['buffer'] = info['buffer'].split(b'\n', 1)
                self.message(client, info, json.loads(line))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            LOG.warning('Local protocol rejected: %s',type(exc).__name__)
            self.drop(client)

    def message(self, client, info, message):
        command = message.get('command')
        if command == 'status':
            self.reply(client, self.status())
            return
        if command == 'os-update-cover':
            if (info['uid'] != 0 or self.busy or time.monotonic()-self.last_evidence>1
                    or self.policy.state != State.RUNNING or not self.update_locked()):
                raise ValueError('OS update cover requires root, fresh idle GUI and held update lock')
            self.policy.state=State.UPDATING
            self.cover_waiters.append(client)
            self.cover(force=True)
            return
        if command == 'os-update-cancel':
            if info['uid']!=0 or self.update_locked():raise ValueError('OS update cancellation not permitted')
            self.policy.state=State.WAITING
            self.policy.started=time.monotonic()
            self.reply(client,dict(cancelled=True))
            return
        if command == 'os-update-quiesce':
            if (info['uid']!=0 or not self.update_locked() or not self.covered or self.gui is None
                    or self.busy or time.monotonic()-self.last_evidence>1):
                raise ValueError('OS shutdown requires root, held lock, cover and fresh idle GUI')
            self.reply(self.gui,dict(os_update_shutdown=True))
            self.reply(client,dict(requested=True))
            return
        if command == 'restart':
            if (self.policy.state != State.RUNNING or self.busy
                    or time.monotonic() - self.last_evidence > 1 or self.update_locked()):
                self.reply(client, dict(error='App busy, not responsive or update active'))
                return
            self.restart_after_cover = True
            self.policy.state = State.RESTARTING
            self.cover()
            self.reply(client, dict(restart='cover_requested'))
            return
        token = message.get('token', '')
        if (not isinstance(token, str) or not hmac.compare_digest(token, self.token)
                or message.get('pid') != info['pid']):
            raise ValueError('Unauthorized OS message')
        version = message.get('version') or read(self.prefix / 'current.json', {}).get('version')
        if not identity(info['pid'], self.prefix, version):
            raise ValueError('Wrong app identity')
        if command == 'cover' and message.get('purpose') == 'update':
            self.policy.state = State.UPDATING
            self.update_origin = info['pid']
            self.cover_waiters.append(client)
            self.cover(force=True)
        elif command == 'cancel_cover' and info['pid'] == self.update_origin and not self.update_locked():
            self.update_origin = None
            self.policy.state = State.WAITING
            self.policy.started = time.monotonic()
        elif command == 'evidence':
            if self.gui is not None and self.gui is not client:
                raise ValueError('A second GUI is forbidden')
            self.gui = client
            self.last_evidence = time.monotonic()
            fields = {name: message.get(name) is True for name in
                      ('wayland', 'mapped', 'painted', 'responsive', 'enabled', 'initialized', 'camera')}
            fields['mapped'] = fields['mapped'] and self.toplevel_mapped
            self.evidence = Evidence(authenticated=True, release=True,
                size=tuple(message.get('size', [])), position=tuple(message.get('position', [])),
                screen=tuple(message.get('screen', [])), fullscreen=message.get('fullscreen') is True, **fields)
            # Existing Handoff keeps the main widget disabled until commit.
            # Supply compositor/render proof first; reveal still requires enabled.
            if replace(self.evidence,enabled=True).gui and not info.get('proof_sent'):
                self.reply(client,dict(gui_prepared=True))
                info['proof_sent']=True
            self.busy = message.get('busy') is not False
            self.sleeping = message.get('sleeping') is True
            state = message.get('power_state')
            self.power_state = state if state in ('active','preparing_sleep','fading_out','sleeping','waking',
                'restoring','error','system_pending') else 'unknown'
            if self.evidence.gui:
                if 'gui_ready' not in self.milestones:
                    event('SPOT_GUI_READY', pid=info['pid'])
                self.milestones.setdefault('gui_ready', self.last_evidence)
            if self.evidence.camera:
                self.milestones.setdefault('camera_ready', self.last_evidence)

    def splash_command(self, text):
        if self.splash.poll() is not None:
            raise RuntimeError('Independent splash exited')
        self.splash.stdin.write((text + '\n').encode())
        self.splash.stdin.flush()

    def cover(self, force=False):
        if not self.cover_requested or force:
            self.cover_generation += 1
            self.covered = False
            self.cover_requested = True
            self.splash_command('cover ' + str(self.cover_generation))

    def splash_event(self, stream):
        data = os.read(stream.fileno(), 1024)
        if not data:
            raise RuntimeError('Splash presentation channel closed')
        self.splash_buffer += data
        if len(self.splash_buffer) > 4096:
            raise ValueError('Invalid splash output')
        while b'\n' in self.splash_buffer:
            line, self.splash_buffer = self.splash_buffer.split(b'\n', 1)
            if line.startswith(b'toplevel '):
                self.toplevel_mapped = line == b'toplevel 1'
            if line == ('covered ' + str(self.cover_generation)).encode():
                self.covered = True
                self.milestones['last_cover_presented'] = time.monotonic()
                if 'splash_presented' not in self.milestones:
                    event('SPLASH_PRESENTED', pid=self.splash.pid)
                self.milestones.setdefault('splash_presented', time.monotonic())
                for client in self.cover_waiters[:]:
                    self.reply(client, dict(covered=True))
                self.cover_waiters.clear()

    def update_locked(self):
        return locked(self.prefix / 'update.lock')

    def launch(self, now):
        if not self.policy.start(now):
            return
        self.recovery_shown = False
        env = dict(os.environ, SPOT_OS_SOCKET=str(self.path), SPOT_OS_TOKEN=self.token,
                   QT_QPA_PLATFORM='wayland', QT_WAYLAND_DISABLE_WINDOWDECORATION='1',
                   PYTHONPATH=str(self.root / 'bootstrap') + os.pathsep + str(self.root.parent))
        # Invoke the established installed entrypoint, which owns app.lock and recovers pending transactions.
        self.launcher = subprocess.Popen([str(self.prefix / 'bin/spot')], env=env,
                                          stdin=subprocess.DEVNULL, start_new_session=True)
        self.milestones['app_process_started'] = now
        event('SPOT_LAUNCHER_STARTED', pid=self.launcher.pid)

    def status(self):
        from . import VERSION
        return dict(os_version=VERSION, state=self.policy.state.value, attempts=self.policy.attempts,
            busy=self.busy, evidence_age=max(0,time.monotonic()-self.last_evidence),
            session_invocation=os.environ.get('INVOCATION_ID'),
            splash_pid=getattr(self, 'splash', None).pid if getattr(self, 'splash', None) else None,
            launcher_pid=self.launcher.pid if self.launcher else None,
            app_pid=self.clients.get(self.gui, {}).get('pid'),
            reason=self.policy.reason, covered=self.covered, gui=self.evidence.gui,
            camera=self.evidence.camera, app=read(self.prefix / 'current.json', {}).get('version'),
            sleeping=self.sleeping,
            power_state=self.power_state,
            evidence=asdict(self.evidence), peer_connected=self.gui is not None,
            update_locked=self.update_locked(), transaction=read(self.prefix / 'transaction.json', {}).get('state'),
            milestones=self.milestones, pid=os.getpid())

    def step(self, now):
        update = self.update_locked()
        app_lock = locked(self.prefix / 'app.lock')
        alive = self.launcher is not None and self.launcher.poll() is None
        if self.restart_after_cover and self.covered and update:
            self.restart_after_cover=False
            self.policy.state=State.UPDATING
        if (self.restart_after_cover and self.covered
                and self.last_evidence>self.milestones.get('last_cover_presented',0)):
            self.restart_after_cover = False
            if self.busy:
                # A touch/file job may have started between initial request and
                # presented cover. Fresh post-cover telemetry must still be idle.
                self.policy.state=State.RUNNING
                self.policy.reason='restart_cancelled_busy'
                self.splash_command('reveal '+str(self.cover_generation))
                self.covered=False
                self.cover_requested=False
                return
            if self.gui:
                info = self.clients[self.gui]
                # Stable launcher is the validated GUI's real parent, including Handoff-spawned launches.
                parent = int(Path('/proc', str(info['pid']), 'stat').read_text().rsplit(')', 1)[1].split()[1])
                descriptor = os.pidfd_open(parent)
                try:
                    if not identity(info['pid'], self.prefix, read(self.prefix/'current.json')['version']):
                        raise RuntimeError('App identity changed before restart')
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                finally:
                    os.close(descriptor)
                self.restart_sent = now
        if self.restart_sent is not None and app_lock:
            if now - self.restart_sent > 30:
                self.policy.state = State.RECOVERY
                self.policy.reason = 'graceful_stop_timeout'
            return
        if not app_lock and not update and not alive:
            self.restart_sent = None
            if self.policy.state in (State.RUNNING, State.WAITING, State.UPDATING, State.ROLLBACK, State.RECOVERY):
                self.cover()
                self.policy.exited(now, False)
            if self.policy.state == State.SESSION or (self.policy.state == State.RESTARTING and now >= self.policy.next_attempt):
                if self.covered:
                    self.launch(now)
        elif update and not self.gui:
            self.policy.state = State.UPDATING
            self.cover()
        if self.gui and now - self.last_evidence < 2:
            if (self.policy.state == State.UPDATING and not update
                    and self.clients[self.gui]['pid'] != self.update_origin):
                self.policy.started = now
                self.policy.state = State.WAITING
                transaction = read(self.prefix / 'transaction.json', {})
                if transaction.get('state') == 'rolled-back':
                    self.policy.state = State.ROLLBACK
                self.update_origin = None
            if self.policy.state in (State.WAITING, State.ROLLBACK):
                if self.policy.observe(now, self.evidence, update) == 'reveal':
                    self.splash_command('reveal ' + str(self.cover_generation))
                    self.covered = False
                    self.cover_requested = False
                    self.milestones['app_revealed'] = now
        elif self.policy.state == State.RUNNING:
            self.cover()
            self.policy.state = State.RECOVERY
            self.policy.reason = 'gui_heartbeat_lost'
        if self.policy.state == State.WAITING and now - self.policy.started >= self.policy.startup_timeout:
            self.policy.state = State.RECOVERY
            self.policy.reason = 'startup_timeout'
        if self.policy.state in (State.RECOVERY, State.FAILED):
            if not getattr(self, 'recovery_shown', False):
                self.cover_generation+=1
                self.covered=False
                self.cover_requested=True
                self.splash_command('recovery '+str(self.cover_generation))
                self.recovery_shown = True
        if not self.milestones.get('splash_presented') and now - self.started_at > 15:
            raise RuntimeError('No compositor presentation acknowledgement')

    def run(self):
        try:
            self.start()
            if boot_limited():
                self.policy.state=State.FAILED
                self.policy.reason='consecutive_device_boot_limit'
            previous = None
            while not self.stopping:
                for key, _ in self.selector.select(.2):
                    key.data(key.fileobj)
                self.step(time.monotonic())
                status = self.status()
                if status != previous:
                    atomic(self.runtime / 'status.json', status)
                    if previous is None or status['state'] != previous['state']:
                        LOG.info('state=%s reason=%s', status['state'], status['reason'])
                        event('SPOT_RUNNING' if status['state']=='running' else 'APP_STATE',
                              state=status['state'], reason=status['reason'])
                    previous = status
        finally:
            # No SIGKILL. The system service cgroup also covers Handoff descendants.
            if self.launcher and self.launcher.poll() is None:
                self.launcher.terminate()
                try:
                    self.launcher.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    LOG.error('Launcher still draining; no competing app will be started')
            if getattr(self, 'splash', None) and self.splash.poll() is None:
                self.splash_command('quit')
            if getattr(self,'path',None):self.path.unlink(missing_ok=True)


def main(root=Path(__file__).parent):
    from .supervisor import parent_death_signal
    parent_death_signal()
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, default=Path(os.environ.get('XDG_RUNTIME_DIR', '/run/invalid')) / 'spot-os')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(name)s %(message)s')
    import fcntl
    directory = private_directory(args.runtime)
    with (directory / 'manager.lock').open('a+b') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manager = Manager(args.prefix, directory, root=root)
        signal.signal(signal.SIGTERM, lambda *_: setattr(manager, 'stopping', True))
        signal.signal(signal.SIGINT, lambda *_: setattr(manager, 'stopping', True))
        manager.run()


if __name__ == '__main__':
    main()
