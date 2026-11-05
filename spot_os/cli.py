"""Explicit administrative actions; no command reboots or stops a live session."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import socket
import sys
import time

from .common import atomic, encode, read
from .installer import Installer, STATE


def request(config, command):
    path = Path('/run/user') / str(config['uid']) / 'spot-os/control.sock'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(path))
        client.sendall(encode(dict(command=command)))
        data = b''
        while b'\n' not in data and len(data) <= 16384:
            block = client.recv(4096)
            if not block:
                raise RuntimeError('Manager closed channel')
            data += block
        return json.loads(data.split(b'\n', 1)[0])


def boot_guard(path=Path(STATE)/'boot-attempts.json', boot_id=Path('/proc/sys/kernel/random/boot_id')):
    data = read(path, dict(attempts=0))
    current = boot_id.read_text().strip()
    if data.get('boot') != current:
        data = dict(attempts=data['attempts']+1, boot=current)
        atomic(path, data)
    data['blocked'] = data['attempts'] > 3
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preflight', 'install', 'activate', 'status', 'doctor', 'check',
        'repair', 'test-restart', 'deactivate', 'rollback', 'uninstall', 'test-backlight', 'provision-os-updates', 'recover-os-update',
        'driver-status', 'provision-lepton', 'rollback-lepton', 'setup',
        'backlight-prepare', 'boot-guard', 'boot-check', 'prepare-session', 'migrate', 'static-check', 'snapshot-failure', 'upgrade', 'rollback-upgrade', 'session-cleanup', 'camera-ready'))
    parser.add_argument('--user', default='admin')
    parser.add_argument('--key-file',type=Path)
    parser.add_argument('--key-sha256')
    parser.add_argument('--provision-lepton', action='store_true', help='Explicit first-device driver setup, never an OTA operation')
    parser.add_argument('--resume-lepton', action='store_true', help='Continue this administrator-owned setup after manual reboot')
    args = parser.parse_args(argv)
    if os.name != 'posix':
        raise RuntimeError('Administrative CLI is only supported on the target Linux system')
    installer = Installer()
    command = args.command
    read_only = command in ('status', 'doctor', 'check', 'static-check', 'driver-status')
    if not read_only and os.geteuid() != 0:
        raise PermissionError('Use sudo for this administrative operation')
    # These commands own the same installer lock themselves. Handle BEFORE the
    # ordinary OS preflight, so a missing driver does not create a setup cycle.
    if command in ('driver-status', 'provision-lepton', 'rollback-lepton', 'setup'):
        from .lepton_provision import probe, Provisioner
        if command == 'driver-status':
            result = probe(installer.system, args.user)
        elif command == 'rollback-lepton':
            result = Provisioner(installer.system).rollback()
        else:
            if command == 'setup' and not args.provision_lepton:
                raise ValueError('Fresh setup requires explicit --provision-lepton consent')
            result = Provisioner(installer.system).run(args.user, resume=args.resume_lepton, include_os=command == 'setup')
            if command == 'setup' and result.get('ok') and not result.get('pending'):
                # Re-enter normal installer CLI to acquire its existing lock and
                # run all ordinary app, SSH, display and camera health barriers.
                return main(['install', '--user', args.user])
        print(json.dumps(result, indent=2))
        return 0 if result.get('ok', result.get('state') == 'compatible') else 1
    Path(STATE).mkdir(parents=True, exist_ok=True, mode=0o700) if not read_only else None
    import fcntl
    # Diagnostics do not need to create files. Serialized mutations have a separate OS lock.
    serialized = command in ('install','activate','repair','deactivate','rollback','uninstall','test-backlight','migrate','upgrade','rollback-upgrade','session-cleanup','provision-os-updates')
    lock = (Path(STATE)/'installer.lock').open('a+b') if serialized else contextlib.nullcontext()
    from .update_owner import owner
    with (owner() if serialized else contextlib.nullcontext()), lock as guard:
        if guard is not None:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = read('/etc/spot-os/config.json', {})
        if command == 'recover-os-update':
            from .update_service import manual_recover
            from .update_backend import Backend
            result=manual_recover(Backend(installer.system))
        elif command == 'provision-os-updates':
            if not args.key_file or not args.key_sha256:raise ValueError('Public key file and independent fingerprint required')
            from .provision import provision
            result=provision(args.key_file,args.key_sha256)
        elif command == 'preflight':
            result = installer.preflight(args.user)
        elif command == 'install':
            result = installer.install(args.user)
        elif command == 'upgrade':
            result = installer.upgrade(args.user)
        elif command == 'rollback-upgrade':
            result = installer.rollback_upgrade()
        elif command == 'camera-ready':
            from .camera_ready import execute
            result = execute(installer.system)
        elif command == 'session-cleanup':
            from .ownership import cleanup
            result = cleanup(installer.system, config)
        elif command == 'migrate':
            result = installer.migrate(args.user)
        elif command == 'static-check':
            from .doctor import static_check
            result = static_check(config, installer.system)
        elif command == 'activate':
            result = installer.activate(args.user)
        elif command in ('deactivate', 'rollback'):
            result = installer.deactivate()
        elif command == 'uninstall':
            result = installer.uninstall()
        elif command == 'repair':
            result = installer.repair(args.user)
            atomic(Path(STATE)/'boot-attempts.json', dict(attempts=0))
            Path('/run/spot-os/boot-blocked.json').unlink(missing_ok=True)
        elif command in ('check', 'status', 'doctor'):
            result = installer.check()
            try:
                result['manager'] = request(config, 'status')
            except (OSError, RuntimeError, KeyError, ValueError):
                result['manager'] = 'not reachable'
            if command == 'doctor':
                from .doctor import collect
                result['diagnostics'] = collect(config)
                result['integrity_ok'] = result['files_ok']
                result['runtime_ok'] = (isinstance(result['manager'], dict)
                    and result['manager'].get('state')=='running' and result['manager'].get('gui') is True
                    and result['manager'].get('camera') is True
                    and result['manager'].get('evidence_age',999)<1
                    and result['diagnostics'].get('current_boot_verified') is True
                    and result['manager'].get('session_invocation')==
                        result['diagnostics'].get('boot_health',{}).get('session_invocation'))
                result['activation_ok'] = result['active']
                result['ok'] = result['ok'] and result['activation_ok'] and result['runtime_ok']
        elif command == 'snapshot-failure':
            from .doctor import collect
            result = collect(config)
            atomic(Path(STATE)/'failures'/(str(time.time_ns())+'.json'),result)
        elif command == 'test-restart':
            result = request(config, 'restart')
        elif command in ('boot-guard', 'prepare-session'):
            if command == 'prepare-session':
                from .health import prepare
                from .ownership import cleanup
                cleanup(installer.system, config)
                prepare(installer.system)
                from .backlight import execute
                execute('backlight-prepare', config)
            result = boot_guard()
            from .runtime import publish_boot_status
            publish_boot_status(result)
        elif command == 'boot-check':
            from .ownership import remember
            remember(installer.system, config)
            from .health import check
            atomic(Path(STATE)/'health.json',dict(boot_verified=False,state='checking',utc=time.time()))
            result = check(config, request, Path(STATE)/'health.json')
            result['boot'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            atomic(Path(STATE)/'health.json',result)
            if result['boot_verified']:
                atomic(Path(STATE)/'boot-attempts.json', dict(attempts=0,
                    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
            print(json.dumps(result))
            return 0 if result['boot_verified'] else 1
        elif command.startswith('backlight') or command == 'test-backlight':
            from .backlight import execute
            result = execute(command, config)
        print(json.dumps(result, indent=2))
        return 1 if isinstance(result, dict) and (result.get('ok') is False or 'error' in result) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print('SPOT OS: '+str(exc), file=sys.stderr)
        sys.exit(1)
