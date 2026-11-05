"""Operator-only read-only before/after evidence; never changes device state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def command(*args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=90)
    if result.returncode:raise RuntimeError('Abnahmeprüfung fehlgeschlagen: '+args[0]+': '+result.stderr[-300:])
    return result.stdout


def doctor_command(version,bridge_present):
    if version=='0.1.4' and bridge_present:
        return ['/usr/bin/python3','-I','/usr/local/lib/spot-os/ota-bridge-0.1.6/spot_os/bridge_entry.py','--doctor']
    return ['/usr/local/bin/spot-os','doctor']


def compare(before,after):
    changed=[name for name in ('boot','kernel','app','hashes','cci_lock','streamer') if before.get(name)!=after.get(name)]
    if changed:raise ValueError('Bestandsschutz nicht bestätigt: '+', '.join(changed))
    if after.get('os')!='0.1.7' or not after.get('doctor',{}).get('ok'):
        raise ValueError('OS 0.1.7 / gesunde Sitzung nicht bestätigt')
    if after.get('transaction',{}).get('phase')!='success' or after['transaction'].get('handover_confirmed') is not True:
        raise ValueError('OTA-/Updater-Abschluss nicht bestätigt')
    if after.get('floor',{}).get('sequence',0)<107:raise ValueError('Sequenzschutz 107 fehlt')
    return dict(ok=True,unchanged=['boot','kernel','app','hashes','cci_lock','streamer'],same_boot=True)


def collect():
    config=json.loads(Path('/etc/spot-os/config.json').read_text())
    app=json.loads((Path(config['prefix'])/'current.json').read_text())
    names=['/usr/local/bin/lepton_streamer','/etc/spot-os/lepton-compat.json',
           '/etc/spot-os/os-release.pub','/etc/spot-os/os-trust.json',
           '/etc/systemd/system/lepton-streamer.service',
           str(Path(config['prefix'])/'releases'/app['version']/'assets/trust/spot-release.pub')]
    hashes={n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in names}
    lock=Path('/run/lock/spot-lepton/cci.lock').stat()
    bridge=Path('/usr/local/lib/spot-os/ota-bridge-0.1.6/spot_os/bridge_entry.py').is_file()
    result=dict(schema=1,os=config['os_version'],app=app,kernel=command('/usr/bin/uname','-r').strip(),
        boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),hashes=hashes,
        cci_lock=dict(dev=lock.st_dev,inode=lock.st_ino,mode=lock.st_mode,uid=lock.st_uid,gid=lock.st_gid),
        streamer=command('/usr/bin/systemctl','show','lepton-streamer.service','-p','ActiveState','-p','MainPID','-p','InvocationID','-p','NRestarts'),
        updater=command('/usr/bin/systemctl','show','spot-os-update.service','spot-os-update.socket',
            '-p','MainPID','-p','InvocationID','-p','ActiveState','-p','SubState','-p','Result','-p','NRestarts',
            '-p','NoNewPrivileges','-p','AmbientCapabilities','-p','CapabilityBoundingSet','-p','ProtectKernelModules','-p','ReadWritePaths'),
        doctor=json.loads(command(*doctor_command(config['os_version'],bridge))))
    for name in ('transaction','floor'):
        result[name]=json.loads(Path('/var/lib/spot-os/updates/'+name+'.json').read_text())
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--compare',type=Path)
    args=parser.parse_args()
    if os.geteuid()!=0:raise PermissionError('Bitte als Administrator zur lesenden Abnahme ausführen')
    result=collect()
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    if args.compare:result['comparison']=compare(json.loads(args.compare.read_text()),result)
    print(json.dumps(dict(os=result['os'],healthy=result['doctor'].get('ok'),output=str(args.output),comparison=result.get('comparison'))))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError) as exc:
        print('Hardwareabnahme: '+str(exc),file=sys.stderr);raise SystemExit(1)
