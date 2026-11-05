"""Physical double-tap certification with a separate root systemd restore guard.

No DSI disable, bl_power, suspend or camera power changes. Software standby is
the default until BOTH raw touch and the actual SPOT wake transition are seen.
"""
import json
import os
from pathlib import Path
import platform
import select
import signal
import stat
import subprocess
import time

from .common import atomic, read

RECORD = Path('/var/lib/spot-os/backlight-test.json')
BRIGHTNESS = Path('/sys/class/backlight/11-0045/brightness')


def restore_app_record(record, brightness=BRIGHTNESS):
    record, brightness = Path(record), Path(brightness)
    if not record.exists():
        return False
    info = record.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
        raise ValueError('Unsafe app display restoration record')
    data = read(record)
    device = brightness.stat()
    if data.get('name') != '11-0045' or (data.get('device'),data.get('inode')) != (device.st_dev,device.st_ino):
        raise ValueError('Backlight identity mismatch')
    value = data.get('value')
    if type(value) is not int or not 0 < value <= int(brightness.with_name('max_brightness').read_text()):
        raise ValueError('Invalid saved brightness')
    brightness.write_text(str(value))
    if int(brightness.read_text()) != value:
        raise RuntimeError('Backlight restore not confirmed')
    record.unlink()
    return True


class RestoreGuard:
    def __init__(self, path, value, deadline, record):
        self.path, self.value, self.deadline, self.record = Path(path), value, deadline, Path(record)
        self.identity = (self.path.stat().st_dev, self.path.stat().st_ino)

    def restore(self):
        info = self.path.stat()
        if (info.st_dev, info.st_ino) != self.identity:
            raise RuntimeError('Backlight identity changed; manual maintenance required')
        self.path.write_text(str(self.value))
        if int(self.path.read_text()) != self.value:
            raise RuntimeError('Backlight return value not confirmed')

    def run(self, now=time.monotonic, sleep=time.sleep):
        data = read(self.record)
        data['watchdog_ready'] = True
        atomic(self.record, data)
        try:
            while now() < self.deadline:
                if read(self.record).get('wake_verified'):
                    break
                sleep(.1)
        finally:
            self.restore()
            data = read(self.record)
            data['restored'] = True
            atomic(self.record, data)


class DoubleTap:
    """Two short contacts, not drag/hold/multitouch. Coordinates in raw units."""
    def __init__(self):
        self.previous = None
        self.down = None
        self.xy = (0, 0)
        self.multitouch = False

    def contact(self, down, x, y, now, contacts=1):
        if contacts > 1:
            self.previous = self.down = None
            return False
        if down:
            self.down = (now, x, y)
            return False
        if self.down is None:
            return False
        started, sx, sy = self.down
        self.down = None
        if now-started > .35 or abs(x-sx)+abs(y-sy) > 50:
            self.previous = None
            return False
        previous, self.previous = self.previous, (now, x, y)
        return bool(previous and .06 <= now-previous[0] <= .45
                    and abs(x-previous[1])+abs(y-previous[2]) <= 50)


def prepare(config):
    """Reapply only the already certified backlight's group permission on each boot."""
    profile = Path('/etc/spot/device-power.json')
    if not profile.exists():
        return dict(enabled=False)
    info = profile.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        return dict(enabled=False,reason='unsafe_device_profile')
    try:
        data = read(profile)
    except (OSError,ValueError):
        return dict(enabled=False,reason='invalid_device_profile')
    if not (data.get('touch_wake_verified') is True and data.get('kernel_release') == platform.release()
            and data.get('backlight_name') == '11-0045' and data.get('session_type') == 'wayland'):
        return dict(enabled=False)
    import grp
    import pwd
    try:
        group = grp.getgrnam('spot-device')
        user = pwd.getpwnam(config['user'])
    except KeyError:
        return dict(enabled=False, reason='existing_device_group_missing')
    if group.gr_gid not in os.getgrouplist(user.pw_name, user.pw_gid):
        return dict(enabled=False, reason='existing_device_group_membership_missing')
    # Fixed device only; no arbitrary path or command from the profile.
    if not BRIGHTNESS.resolve().is_relative_to(Path('/sys/devices')):
        return dict(enabled=False,reason='unexpected_backlight_path')
    try:
        os.chown(BRIGHTNESS, -1, group.gr_gid)
        os.chmod(BRIGHTNESS, stat.S_IMODE(BRIGHTNESS.stat().st_mode) | 0o020)
    except OSError:
        return dict(enabled=False,reason='backlight_permission_unavailable')
    return dict(enabled=True)


def physical_test(config):
    from .cli import request
    from .installer import Journal
    from .system import System
    from .system import validate
    # Same target hardware checks, but a live software-standby GUI is REQUIRED here.
    system = System()
    inventory = system.inventory(config['user'])
    result = validate(inventory, maintenance=False)
    if not result['ok']:
        raise RuntimeError('Backlight test preflight failed')
    manager = request(config, 'status')
    if (manager['state'] != 'running' or manager.get('power_state') != 'sleeping'
            or manager['update_locked']):
        raise RuntimeError('First enter software standby in SPOT, then run test-backlight over SSH')
    import grp
    import pwd
    group = grp.getgrnam('spot-device')
    user = pwd.getpwnam(config['user'])
    if group.gr_gid not in os.getgrouplist(user.pw_name, user.pw_gid):
        raise RuntimeError('Use the existing device-actions setup for group membership first')
    original = int(BRIGHTNESS.read_text())
    if not 0 < original <= int(BRIGHTNESS.with_name('max_brightness').read_text()):
        raise ValueError('Start the test at a positive original brightness')
    from evdev import InputDevice, ecodes, list_devices
    devices = [InputDevice(path) for path in list_devices()]
    touch = next((device for device in devices if 'ft5x06' in device.name.lower()
                  and ecodes.INPUT_PROP_DIRECT in device.input_props()), None)
    if touch is None:
        raise RuntimeError('Direct FT5x06 touch input missing')
    started = time.monotonic()
    atomic(RECORD, dict(original=original, deadline=started+30, touch=touch.name,
                        watchdog_ready=False, wake_verified=False, restored=False))
    # Guard is a separate root service: survives test process exit, SSH loss or app crash.
    subprocess.run(['/usr/bin/systemd-run', '--unit=spot-os-backlight-restore', '--collect',
        '--property=Type=exec', '--property=TimeoutStopSec=5',
        '--setenv=PYTHONPATH=/usr/local/lib/spot-os',
        '/usr/bin/python3', '-m', 'spot_os.backlight'], check=True, timeout=8)
    deadline = time.monotonic()+5
    while not read(RECORD)['watchdog_ready'] and time.monotonic() < deadline:
        time.sleep(.05)
    if not read(RECORD)['watchdog_ready']:
        raise RuntimeError('Restore watchdog not armed: brightness unchanged')
    detector = DoubleTap()
    x = y = 0
    contact = False
    verified = False
    try:
        if int(BRIGHTNESS.read_text()) != original:
            raise RuntimeError('Brightness changed before test')
        BRIGHTNESS.write_text('0')
        if int(BRIGHTNESS.read_text()) != 0:
            raise RuntimeError('Backlight did not accept zero')
        print('Backlight 0: jetzt am SPOT-Display doppeltippen. Automatische Rückkehr nach 30 s.', flush=True)
        while time.monotonic() < started+25:
            ready, _, _ = select.select([touch.fd], [], [], .1)
            if not ready:
                continue
            for event in touch.read():
                if event.type == ecodes.EV_ABS:
                    if event.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X): x = event.value
                    if event.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y): y = event.value
                    if event.code == ecodes.ABS_MT_SLOT and event.value > 0:
                        detector.previous = detector.down = None
                if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                    contact = bool(event.value)
                    if detector.contact(contact, x, y, time.monotonic()):
                        wake_deadline = time.monotonic()+2
                        while time.monotonic() < wake_deadline:
                            state = request(config, 'status')
                            if state['state'] == 'running' and state.get('power_state') == 'active':
                                verified = True
                                break
                            time.sleep(.1)
            if verified:
                data = read(RECORD)
                data['wake_verified'] = True
                atomic(RECORD, data)
                break
    finally:
        # Parent also restores on normal/error exit; independent guard covers SIGKILL.
        BRIGHTNESS.write_text(str(original))
        for device in devices:
            device.close()
    deadline = time.monotonic()+6 if verified else started+35
    while not read(RECORD)['restored'] and time.monotonic() < deadline:
        time.sleep(.1)
    if not verified or not read(RECORD)['restored'] or int(BRIGHTNESS.read_text()) != original:
        return dict(certified=False, original_restored=int(BRIGHTNESS.read_text()) == original,
                    fallback='software standby')
    journal = Journal(system, 'backlight')
    # Keep prior root profile byte-for-byte, including absence, for OS rollback.
    profile = dict(touch_wake_verified=True, kernel_release=platform.release(), session_type='wayland',
                   touch_name=read(RECORD)['touch'], backlight_name='11-0045')
    journal.write('/etc/spot/device-power.json', (json.dumps(profile)+'\n').encode())
    journal.data['phase'] = 'certified'
    journal.save()
    if not prepare(config)['enabled']:
        journal.restore()
        return dict(certified=False,original_restored=True,fallback='software standby; permission unavailable')
    return dict(certified=True, original_restored=True, next_standby_uses_zero=True)


def execute(command, config):
    if command == 'backlight-prepare':
        return prepare(config)
    if command == 'test-backlight':
        return physical_test(config)
    raise ValueError('Guard is started only by the fixed root systemd test unit')


if __name__ == '__main__':
    data = read(RECORD)
    guard = RestoreGuard(BRIGHTNESS, data['original'], data['deadline'], RECORD)
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    guard.run()
