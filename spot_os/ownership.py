"""Root-authorized cleanup of a closing instrument scope, never a user-wide kill.

Read identity from systemd/logind, not user-writable milestone files. A retained
root ledger binds the boot, PAM session, leader, scope and service invocation.
Legacy 0.1.1 adoption requires matching retained systemd leader AND timestamps.
Only verified private dbus-daemon PIDs receive TERM, through pidfds. Stop the
scope only after the kernel reports it empty; unknown members block cleanup.
"""
import os
from pathlib import Path
import re
import signal
import stat
import time
from .common import atomic, read

UNIT = 'spot-os-session.service'
LEDGER = '/var/lib/spot-os/session-owner.json'


def properties(system, tool, name, fields):
    args = ['/usr/bin/'+tool, 'show-session' if tool == 'loginctl' else 'show', name]
    for field in fields:
        args += ['-p', field]
    value = system.command(*args, check=False)
    return dict(line.split('=', 1) for line in value.splitlines() if '=' in line)


def sessions(system):
    listing = system.command('/usr/bin/loginctl', 'list-sessions', '--no-legend', '--no-pager')
    result = []
    for line in listing.splitlines():
        name = line.split()[0]
        if not re.fullmatch('[a-zA-Z0-9]+', name):
            raise RuntimeError('Unrecognized logind session identity; cleanup blocked')
        info = properties(system, 'loginctl', name,
            ('User','Leader','Seat','VTNr','TTY','Type','State','Service','Scope','TimestampMonotonic'))
        result.append((name, info))
    return result


def own(info, name, config):
    return (info.get('User') == str(config['uid']) and info.get('Service') == 'spot-os'
        and info.get('Leader','').isdigit() and int(info['Leader']) > 1
        and info.get('Seat') == 'seat0' and info.get('VTNr') == '1' and info.get('TTY') == 'tty1'
        and info.get('Type') == 'wayland' and info.get('Scope') == 'session-'+name+'.scope')


def remember(system, config):
    unit = properties(system, 'systemctl', UNIT, ('MainPID','InvocationID','ExecMainStartTimestampMonotonic'))
    matches = [(name, info) for name, info in sessions(system)
        if own(info, name, config) and info.get('Leader') == unit.get('MainPID')]
    if len(matches) != 1 or int(unit.get('MainPID','0')) <= 0 or not unit.get('InvocationID'):
        raise RuntimeError('Cannot record unique active SPOT PAM ownership')
    name, info = matches[0]
    atomic(system.path(LEDGER), dict(boot=system.path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        session=name, identity=info, invocation=unit['InvocationID']))
    return dict(recorded=name)


def cleanup(system, config):
    candidates = [(name, info) for name, info in sessions(system) if info.get('VTNr') == '1']
    if not candidates:
        return dict(cleaned=[], reason='no_tty1_session')
    unit = properties(system, 'systemctl', UNIT,
        ('MainPID','ExecMainPID','InvocationID','ExecMainStartTimestampMonotonic','ActiveState'))
    if int(unit.get('MainPID','0')) or unit.get('ActiveState') in ('active','deactivating','reloading'):
        raise RuntimeError('SPOT session is still live; cleanup blocked')
    ledger_path = system.path(LEDGER)
    if ledger_path.exists():
        meta = ledger_path.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o077:
            raise PermissionError('Invalid private root ownership ledger')
    ledger = read(ledger_path, {})
    boot = system.path('/proc/sys/kernel/random/boot_id').read_text().strip()
    verified = []
    # Validate ALL sessions before signalling anything.
    for name, info in candidates:
        if not own(info, name, config) or info.get('State') != 'closing':
            raise RuntimeError('tty1 has a foreign/live/unverified session '+name+'; no takeover')
        recorded = (ledger.get('boot') == boot and ledger.get('session') == name
            and bool(ledger.get('invocation'))
            and all(ledger.get('identity',{}).get(key) == info.get(key)
                for key in ('Leader','User','Service','Scope','Seat','VTNr','TTY','Type','TimestampMonotonic')))
        # Old installations have no ledger. Both timestamps are supplied by
        # root services in this boot, not process names or a user-provided PID.
        started = int(unit.get('ExecMainStartTimestampMonotonic','0'))
        opened = int(info.get('TimestampMonotonic','0'))
        legacy = (info.get('Leader') == unit.get('ExecMainPID') and started > 0
            and 0 <= opened-started < 10_000_000)
        if not (recorded or legacy):
            raise RuntimeError('Cannot prove previous SPOT session ownership; SSH diagnosis required')
        scope = info['Scope']
        group = '/user.slice/user-'+str(config['uid'])+'.slice/'+scope
        actual = properties(system, 'systemctl', scope, ('ControlGroup',)).get('ControlGroup')
        if actual != group:
            raise RuntimeError('Unexpected session cgroup; cleanup blocked')
        verified.append((scope, group))
    cleaned = []
    with system.maintenance_guard(config['prefix']):
        descriptors = []
        try:
            for scope, group in verified:
                members = system.path('/sys/fs/cgroup'+group+'/cgroup.procs')
                # A delegated child cgroup could hide other writers.
                if any(p.is_dir() for p in members.parent.iterdir()):
                    raise RuntimeError('Nested session cgroup; cleanup blocked')
                for value in members.read_text().split():
                    pid = int(value)
                    fd = os.pidfd_open(pid)
                    descriptors.append(fd)
                    proc = system.path('/proc/'+value)
                    argv = (proc/'cmdline').read_bytes().split(b'\0')
                    if (proc.stat().st_uid != config['uid'] or
                        (proc/'exe').resolve() != system.path('/usr/bin/dbus-daemon').resolve() or
                        not all(flag in argv for flag in (b'--session', b'--nofork', b'--print-address')) or
                        (proc/'cgroup').read_text().strip() != '0::'+group):
                        raise RuntimeError('Unknown remaining process '+value+'; no scope termination')
            for fd in descriptors:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            deadline = time.monotonic()+5
            for scope, group in verified:
                members = system.path('/sys/fs/cgroup'+group+'/cgroup.procs')
                while members.exists() and members.read_text().strip() and time.monotonic() < deadline:
                    time.sleep(.1)
                if members.exists() and members.read_text().strip():
                    raise RuntimeError('Session did not drain after private broker TERM; no SIGKILL')
                # No unverified app, recorder, or same-UID SSH process is killed.
                system.command('/usr/bin/systemctl', 'stop', scope, check=False)
                state = properties(system,'systemctl',scope,('LoadState','ActiveState'))
                if state.get('LoadState') != 'not-found' and state.get('ActiveState') not in ('inactive','failed'):
                    raise RuntimeError('Empty session scope did not stop; inspect logind before retry')
                cleaned.append(scope)
        finally:
            for fd in descriptors:
                os.close(fd)
    return dict(cleaned=cleaned)
