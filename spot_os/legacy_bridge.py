"""Explicit, signed 0.1.4 updater bootstrap; never called by ordinary OTA.

The old updater cannot repair its own preflight/sandbox using a future ZIP.
An administrator first verifies/extracts that ZIP with the INSTALLED verifier.
This step verifies it again, installs only a private updater runtime + drop-ins,
and preserves the old OS/app version and original units. UpgradeJournal retires
the drop-ins during the later OTA, restoring them on rollback to 0.1.4.
"""
import hashlib
import os
from pathlib import Path
import tempfile
from .common import read, atomic
from .installer import Installer, Journal, STATE
from .update_contract import verify, compatible, unpack, trust
from .update_owner import owner

BASE='/usr/local/lib/spot-os/ota-bridge-0.1.6'
RECORD=STATE+'/ota-bridge.json'
UPDATE='/etc/systemd/system/spot-os-update.service.d/50-spot-os-014-bridge.conf'


def overlays(source):
    body=(source/'config/spot-os-update.service').read_text().split('[Service]',1)[1].split('[Install]',1)[0]
    # Reset list-valued settings: legacy defaults must not widen the new policy.
    body='[Service]\nExecStart=\nReadWritePaths=\nCapabilityBoundingSet=\nAmbientCapabilities=\n'+body
    body=body.replace('/usr/local/lib/spot-os/admin_entry.py',BASE+'/spot_os/bridge_entry.py')
    # Dependencies cannot be removed using empty drop-in assignments. Keep
    # the legacy session intact; the new engine owner also protects THAT graph.
    # Its Wants/After edge is removed only by the later full unit replacement.
    return {UPDATE:body.encode()}


def prepare(system,source,archive,raw,signature,*,key_provider=trust):
    source=Path(source)
    data=verify(raw,signature,key_provider())
    if data['version']!='0.1.6' or data['sequence']!=106 or data['channel']!='prerelease':
        raise ValueError('Bridge requires the signed OS 0.1.6/106 pre-release')
    config=read(system.path('/etc/spot-os/config.json'),{})
    current=read(system.path(config['prefix']+'/current.json'),{})
    if config.get('os_version')!='0.1.4':raise ValueError('Bridge only supports installed OS 0.1.4')
    for unit,allowed in (('spot-os-update.service',set()),('spot-os-session.service',{'user.conf'})):
        folder=system.path('/etc/systemd/system/'+unit+'.d')
        for path in folder.glob('*.conf'):
            bridge_names={'50-spot-os-014-bridge.conf'} if unit=='spot-os-update.service' else set()
            if path.name not in allowed|bridge_names:
                raise ValueError('Review unknown service override before bridge setup: '+str(path))
    compatible(data,'0.1.4',current.get('version',''),read(system.path(STATE+'/updates/floor.json'),{}),True)
    with tempfile.TemporaryDirectory() as directory:
        verified=unpack(archive,data,Path(directory)/'verified')
        expected={p.relative_to(verified).as_posix() for p in verified.rglob('*') if p.is_file()}
        actual={p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        if actual!=expected:raise ValueError('Bridge source has unlisted/missing files')
        for path in verified.rglob('*'):
            if path.is_file() and (source/path.relative_to(verified)).read_bytes()!=path.read_bytes():
                raise ValueError('Bridge source differs from signed package')
    from .update_engine import TERMINAL
    phase=read(system.path(STATE+'/updates/transaction.json'),{}).get('phase','idle')
    if phase not in TERMINAL-{'recovery-failed'}:raise RuntimeError('Finish/recover the old OTA first')
    system.command('/usr/bin/systemctl','stop','spot-os-update.socket','spot-os-update.service',timeout=150)
    Installer(system).require_stopped()
    with owner(system.path(STATE+'/updates')):
        # Old 0.1.4 did not own this inode: recheck after PID1 drained it.
        phase=read(system.path(STATE+'/updates/transaction.json'),{}).get('phase','idle')
        if phase not in TERMINAL-{'recovery-failed'}:raise RuntimeError('Old OTA interrupted; recover before bridge setup')
        if not Installer(system).check()['ok']:raise RuntimeError('Repair old OS integrity before bridge setup')
        from .update_backend import Backend
        with Backend(system).transaction_guard(),system.maintenance_guard(config['prefix']):
            # maintenance_guard requires a stopped app; bridge setup is explicitly
            # an SSH maintenance action. No GUI/driver is stopped by this code.
            journal=Journal(system,'ota-bridge-installation')
            digest=hashlib.sha256(archive).hexdigest()
            existing=read(system.path(RECORD),{})
            if existing:
                if existing.get('sha256')!=digest:raise ValueError('Different bridge already installed')
                if journal.data['phase']!='prepared':raise RuntimeError('Interrupted bridge setup; inspect its journal')
                for name,contents in overlays(source).items():
                    if system.path(name).read_bytes()!=contents:raise ValueError('Modified bridge overlay')
                system.command('/usr/bin/systemctl','start','spot-os-update.socket','spot-os-update.service',timeout=45)
                return dict(ok=True,unchanged=True,os_version='0.1.4')
            if journal.data['phase'] not in ('new','restored'):
                journal.restore()
            journal.data=dict(schema=1,phase='preparing',entries=[]);journal.save()
            try:
                for path in sorted(source.rglob('*')):
                    if path.is_symlink():raise ValueError('Bridge source link')
                    if path.is_file() and '__pycache__' not in path.parts:
                        journal.write(BASE+'/spot_os/'+path.relative_to(source).as_posix(),path.read_bytes())
                for name,contents in overlays(source).items():
                    if system.path(name).exists() or system.path(name).is_symlink():
                        raise ValueError('Refusing an existing bridge drop-in')
                    journal.write(name,contents)
                helper=(source/'config/spot-os-preflight.service').read_bytes().replace(
                    b'/usr/local/lib/spot-os/admin_entry.py',(BASE+'/spot_os/bridge_entry.py').encode())
                helper_name='/etc/systemd/system/spot-os-preflight.service'
                if system.path(helper_name).exists():raise ValueError('Unknown host preflight unit')
                journal.write(helper_name,helper)
                record=dict(schema=1,version='0.1.6',source_commit=data['source_commit'],sha256=digest,
                    overlays={n:hashlib.sha256(b).hexdigest() for n,b in overlays(source).items()},
                    status='prepared',app_version=current['version'],previous='0.1.4')
                journal.write(RECORD,(__import__('json').dumps(record,sort_keys=True)+'\n').encode(),0o600)
                journal.data['phase']='prepared';journal.save()
                system.command('/usr/bin/systemctl','daemon-reload')
            except BaseException:
                journal.restore();system.command('/usr/bin/systemctl','daemon-reload',check=False)
                raise
    system.command('/usr/bin/systemctl','start','spot-os-update.socket','spot-os-update.service',timeout=45)
    return dict(ok=True,os_version='0.1.4',updater_version='0.1.6',source_commit=data['source_commit'],reboot=False)


def retire(system,journal):
    record=read(system.path(RECORD),{})
    if not record:return
    expected=overlays(Path(__file__).parent)
    if record.get('overlays')!={n:hashlib.sha256(b).hexdigest() for n,b in expected.items()}:
        raise ValueError('Unknown legacy bridge policy')
    for name,data in expected.items():
        if system.path(name).read_bytes()!=data:raise ValueError('Modified legacy bridge drop-in')
        journal.remove(name)
    journal.remove(RECORD)
    bridge_journal=Journal(system,'ota-bridge-installation')
    if bridge_journal.data['phase']!='prepared':raise ValueError('Legacy bridge journal is not complete')
    retired=dict(bridge_journal.data,phase='retired')
    journal.write(STATE+'/ota-bridge-installation.json',(__import__('json').dumps(retired,sort_keys=True)+'\n').encode(),0o600)


def undo(system):
    from .update_engine import TERMINAL
    from .update_backend import Backend
    config=read(system.path('/etc/spot-os/config.json'),{})
    if config.get('os_version')!='0.1.4':raise ValueError('Bridge undo requires restored OS 0.1.4')
    for name in ('spot-os-update.service','spot-os-update.socket'):
        value=system.command('/usr/bin/systemctl','show',name,'-p','ActiveState','--value')
        if value not in ('inactive','failed'):raise RuntimeError('Stop updater and socket before bridge undo')
    Installer(system).require_stopped()
    with owner(system.path(STATE+'/updates')),Backend(system).transaction_guard(),system.maintenance_guard(config['prefix']):
        phase=read(system.path(STATE+'/updates/transaction.json'),{}).get('phase','idle')
        if phase not in TERMINAL-{'recovery-failed'}:raise RuntimeError('Recover the OTA before bridge undo')
        if read(system.path(STATE+'/updates/floor.json'),{}).get('sequence',0)>=106:
            raise RuntimeError('Keep the repaired bridge after an attempted 106 OTA; anti-replay prevents reinstalling that bridge')
        journal=Journal(system,'ota-bridge-installation')
        if journal.data['phase']=='new':raise ValueError('No bridge journal to undo')
        journal.restore()
        system.command('/usr/bin/systemctl','daemon-reload')
    return dict(ok=True,bridge_removed=True,os_version='0.1.4',old_updater_limitations_restored=True)


def main(argv):
    import argparse,json
    from .system import System
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path);p.add_argument('manifest',type=Path);p.add_argument('signature',type=Path)
    a=p.parse_args(argv)
    if os.geteuid()!=0:raise PermissionError('Explicit root preparation required')
    print(json.dumps(prepare(System(),Path(__file__).parent,a.archive.read_bytes(),a.manifest.read_bytes(),a.signature.read_bytes()),indent=2))
    return 0
