"""Bounded, read-only startup barrier, NOT camera health or camera recovery.

Wants/After lets a failed probe or failed streamer release the GUI startup job.
Only this short-lived root probe opens the node before the app: no S_FMT,
REQBUFS, STREAMON, read(), CCI, service restart or module operation is used.
The separate systemd oneshot also bounds a hung ioctl, without killing the GUI.
"""
import ctypes
import os
from pathlib import Path
import stat
import struct
import subprocess
import time

from .common import atomic

UNIT = 'lepton-streamer.service'
DEVICE = '/dev/video10'
STATUS = Path('/run/spot-os/lepton-start.json')
EXPECTED = dict(width=160, height=120, fourcc='Y16 ')


class FormatUnion(ctypes.Union):
    # v4l2_format contains a 200-byte union including pointer-aligned members.
    _fields_ = [('raw', ctypes.c_ubyte*200), ('alignment', ctypes.c_void_p)]


class Format(ctypes.Structure):
    _fields_ = [('type', ctypes.c_uint32), ('fmt', FormatUnion)]


def read_format(device):
    import fcntl
    value = Format()
    value.type = 1  # V4L2_BUF_TYPE_VIDEO_CAPTURE: the view consumed by SPOT.
    request = (3 << 30) | (ctypes.sizeof(Format) << 16) | (ord('V') << 8) | 4
    buffer = bytearray(bytes(value))
    # Merely opening/G_FMT does not negotiate or start capture. NONBLOCK also
    # avoids waiting for frames; no frame/temperature readiness is claimed.
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise ValueError('V4L2 node is not a character device')
        fcntl.ioctl(fd, request, buffer, True)
    finally:
        os.close(fd)
    width, height, fourcc, _field, stride, size = struct.unpack_from('=6I',buffer,Format.fmt.offset)
    return dict(width=width,height=height,fourcc=fourcc.to_bytes(4,'little').decode('ascii',errors='replace'),
                bytesperline=stride,sizeimage=size)


def service(system, unit):
    text = system.command('/usr/bin/systemctl','show',unit,'-p','ActiveState','-p','SubState',
        '-p','MainPID','-p','InvocationID','-p','ExecMainStartTimestampMonotonic',check=False,timeout=1)
    return dict(line.split('=',1) for line in text.splitlines() if '=' in line)


def owns_device(pid, device, proc=Path('/proc')):
    info = Path(device).stat()
    if not stat.S_ISCHR(info.st_mode):
        return False
    for fd in (proc/str(pid)/'fd').iterdir():
        try:
            opened = fd.stat()
            if stat.S_ISCHR(opened.st_mode) and opened.st_rdev == info.st_rdev:
                fields = (proc/str(pid)/'fdinfo'/fd.name).read_text().splitlines()
                flags = next((line.split(':',1)[1].strip() for line in fields if line.startswith('flags:')),'0')
                if int(flags,8) & os.O_ACCMODE in (os.O_WRONLY,os.O_RDWR):
                    return True
        except FileNotFoundError:
            continue
    return False


def wait_for_format(system, *, unit=UNIT, device=DEVICE, timeout=12, interval=.2,
                    format_reader=read_format, owner=owns_device, clock=time.monotonic, pause=time.sleep):
    started = clock()
    deadline = started+timeout
    previous = None
    reason = 'streamer_not_running'
    observed = None
    info = {}
    checks = 0
    while clock() < deadline:
        try:
            checks += 1
            info = service(system,unit)
            if info.get('ActiveState') == 'failed':
                reason = 'streamer_failed'
                break
            pid = int(info.get('MainPID','0'))
            identity = (pid,info.get('InvocationID'),info.get('ExecMainStartTimestampMonotonic'))
            running = (info.get('ActiveState') == 'active' and info.get('SubState') == 'running'
                and pid > 1 and bool(identity[1]) and int(identity[2] or '0') > 0)
            valid = False
            if running and owner(pid,device):
                observed = format_reader(device)
                valid = (all(observed.get(k)==v for k,v in EXPECTED.items())
                    and observed.get('bytesperline',0)>=320 and observed.get('sizeimage',0)>=38400)
                # A retained node format without a live producer is NOT ready.
                # Recheck unit generation around G_FMT and require two samples.
                after = service(system,unit)
                valid &= after == info and owner(pid,device)
                reason = 'format_not_ready' if not valid else 'format_confirming'
            else:
                reason = 'producer_not_open' if running else 'streamer_not_running'
            if valid and previous == identity and clock() < deadline:
                return dict(format_ready=True,reason='producer_format_ready',device=device,
                    expected=EXPECTED,observed=observed,service=info,checks=checks,elapsed=clock()-started)
            previous = identity if valid else None
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
            reason = 'probe_'+type(exc).__name__
            previous = None
        remaining = deadline-clock()
        if remaining > 0:
            pause(min(interval,remaining))
    return dict(format_ready=False,reason=reason,device=device,expected=EXPECTED,observed=observed,
        service=info,checks=checks,elapsed=clock()-started,gui_fallback=True)


def execute(system):
    from .runtime import root_readable_directory
    root_readable_directory(STATUS.parent,0o711)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    started=time.monotonic()
    invocation=os.environ.get('INVOCATION_ID')
    atomic(STATUS,dict(phase='checking',boot=boot,probe_invocation=invocation,
        started_monotonic=started,format_ready=False),0o644)
    result = wait_for_format(system)
    result.update(phase='complete',boot=boot,probe_invocation=invocation,
        started_monotonic=started,completed_monotonic=time.monotonic())
    atomic(STATUS,result,0o644)
    # Readiness is diagnostic. Camera failure must never fail the GUI dependency.
    return result
