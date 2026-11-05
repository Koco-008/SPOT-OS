"""Read-only startup-chain diagnostics; explicit integrity versus runtime health."""
import os
from pathlib import Path
import stat
import subprocess
from .common import read


def probe(*args, limit=12000):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return dict(exit_status=result.returncode, stdout=result.stdout[-limit:], stderr=result.stderr[-2000:])
    except (OSError, subprocess.SubprocessError) as exc:
        return dict(error=type(exc).__name__)


def updater_credentials():
    try:
        pid=int(probe('/usr/bin/systemctl','show','spot-os-update.service','-p','MainPID','--value')['stdout'])
        if pid<=1:return {'inactive':True}
        fields={'Uid','Gid','CapInh','CapPrm','CapEff','CapBnd','CapAmb','NoNewPrivs','Seccomp'}
        return {k:v.strip() for k,v in (line.split(':',1) for line in
            Path('/proc',str(pid),'status').read_text().splitlines() if ':' in line) if k in fields}
    except (OSError,ValueError,KeyError):return {'unavailable':True}


def collect(config):
    runtime = Path('/run/user')/str(config.get('uid', -1))
    logs = Path('/var/log/spot-os')
    invocations = sorted((p for p in logs.glob('*') if p.is_dir() and not p.is_symlink()),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    evidence = []
    for folder in invocations:
        items = {}
        for path in folder.glob('*.json'):
            if path.is_file() and not path.is_symlink() and path.stat().st_size < 65536:
                items[path.stem] = read(path)
        errors = folder/'labwc.stderr'
        tail = ''
        if errors.is_file() and not errors.is_symlink():
            with errors.open('rb') as stream:
                stream.seek(max(0, errors.stat().st_size-12000))
                tail = stream.read(12000).decode(errors='replace')
        evidence.append(dict(invocation=folder.name, milestones=items, labwc_stderr=tail))
    paths = [runtime, runtime/'spot-os/control.sock', *runtime.glob('wayland-*')]
    filesystem = []
    for path in paths:
        try:
            info = path.lstat()
            filesystem.append(dict(path=str(path), uid=info.st_uid, mode=oct(stat.S_IMODE(info.st_mode)),
                                   socket=stat.S_ISSOCK(info.st_mode)))
        except OSError:
            filesystem.append(dict(path=str(path), missing=True))
    sessions=probe('/usr/bin/loginctl','list-sessions','--no-legend','--no-pager')
    session_details=[]
    for line in sessions.get('stdout','').splitlines()[:16]:
        name=line.split()[0]
        if len(name)<64 and name.isalnum():
            session_details.append(probe('/usr/bin/loginctl','show-session',name,'-p','Leader',
                '-p','TTY','-p','VTNr','-p','Seat','-p','State','-p','Active','-p','Service','-p','Type'))
    health=read('/var/lib/spot-os/health.json', {})
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    from .lepton import inspect
    from .system import System
    return dict(
        updater_credentials=updater_credentials(),
        preflight_unit=probe('/usr/bin/systemctl','show','spot-os-preflight.service','-p','ActiveState','-p','Result','-p','ExecMainStatus'),
        updater_security=probe('/usr/bin/systemctl','show','spot-os-update.service',
            '-p','MainPID','-p','NoNewPrivileges','-p','AmbientCapabilities','-p','CapabilityBoundingSet',
            '-p','ProtectKernelModules','-p','ReadWritePaths','-p','DropInPaths'),
        lepton=inspect(System(),config['user']),
        session_owner=read('/var/lib/spot-os/session-owner.json',{}),
        unit=probe('/usr/bin/systemctl', 'show', 'spot-os-session.service',
            '-p','ActiveState','-p','SubState','-p','Result','-p','MainPID','-p','ExecMainCode',
            '-p','ExecMainStatus','-p','NRestarts','-p','StatusText','-p','InvocationID'),
        camera_startup=read('/run/spot-os/lepton-start.json',{}),
        camera_ready_unit=probe('/usr/bin/systemctl','show','spot-os-lepton-ready.service',
            '-p','ActiveState','-p','Result','-p','ExecMainStatus','-p','InvocationID'),
        health_unit=probe('/usr/bin/systemctl','show','spot-os-health.service',
            '-p','ActiveState','-p','Result','-p','ExecMainStatus'),
        prepare_unit=probe('/usr/bin/systemctl','show','spot-os-prepare.service','-p','Result','-p','ExecMainStatus'),
        boot_health=health, current_boot_verified=health.get('boot_verified') is True and health.get('boot')==boot,
        vt=probe('/usr/bin/fgconsole'),
        sessions=sessions, session_details=session_details,
        seat=probe('/usr/bin/loginctl','seat-status','seat0','--no-pager'),
        processes=probe('/usr/bin/ps','-eo','pid,ppid,sid,pgid,tty,uid,stat,comm'),
        runtime=filesystem, recent_invocations=evidence,
        journal=probe('/usr/bin/journalctl','-b','--no-pager','-n','200',
            '-u','spot-os-session.service','-u','spot-os-prepare.service',
            '-u','spot-os-health.service','-u','systemd-logind.service','-u','plymouth-quit.service'),
        session_output=probe('/usr/bin/journalctl','-b','--no-pager','-n','150','-t','spot-os'),
        maintenance='OTA failure: stop updater/socket/session, use verified recover-os-update; on restored 0.1.4 use the 0.1.6 bridge. See MIGRATION_014_016.md. No automatic reboot.')


def static_check(config, system):
    """Never opens a VT/DRM device, starts compositor, changes target or runs GUI."""
    import json
    root = Path(__file__).parent
    checks = dict(
        unit=probe('/usr/bin/systemd-analyze','verify','/etc/systemd/system/spot-os-session.service',
            '/etc/systemd/system/spot-os-prepare.service','/etc/systemd/system/spot-os-health.service',
            '/etc/systemd/system/spot-os-lepton-ready.service'),
        labwc=probe('/usr/bin/labwc','--version'),
        dbus=probe('/usr/sbin/runuser','-u',config['user'],'--','/usr/bin/dbus-run-session','--','/usr/bin/dbus-send','--session','--print-reply',
            '--dest=org.freedesktop.DBus','/','org.freedesktop.DBus.ListNames'),
        native_dependencies=probe('/usr/bin/ldd',str(root/'native/spot-splash')),
        pam_stack=Path('/etc/pam.d/spot-os').read_text() == (root/'config/pam-spot-os').read_text(),
        pam_modules=all(list(Path('/usr/lib').glob('*/security/'+name)) for name in ('pam_systemd.so','pam_loginuid.so','pam_unix.so')))
    current = read(Path(config['prefix'])/'current.json')
    from .system import qt_network_probe
    from .lepton import inspect
    checks['qt_network'] = qt_network_probe(system, config['user'], current['python'])
    cci = inspect(system, config['user'])
    checks['lepton_cci'] = cci['compatible']
    checks['qt_wayland'] = probe('/usr/sbin/runuser','-u',config['user'],'--',current['python'],'-c',
        'from PySide6.QtCore import QLibraryInfo; from pathlib import Path; '
        'p=Path(QLibraryInfo.path(QLibraryInfo.PluginsPath))/"platforms/libqwayland-generic.so"; '
        'print(p); raise SystemExit(0 if p.is_file() else 1)')
    import xml.etree.ElementTree as ET
    ET.parse('/etc/spot-os/labwc/rc.xml')
    checks['labwc_xml'] = True
    ok = all(v is True or isinstance(v,dict) and v.get('exit_status') == 0 for v in checks.values())
    return dict(ok=ok, kind='static_only', hardware_session_verified=False,
        lepton=cci, protocols='layer-shell, wp_presentation, foreign-toplevel require live compositor; not verified here', checks=checks)
