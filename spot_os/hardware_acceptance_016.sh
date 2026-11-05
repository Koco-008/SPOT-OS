#!/bin/sh
# Read-only evidence capture. Run only by the operator, from a verified package.
# No start/stop, recovery, camera open, CCI write, module operation or reboot.
set -eu
test "$(id -u)" = 0
date -u '+%Y-%m-%dT%H:%M:%SZ'
uname -a
cat /proc/sys/kernel/random/boot_id
systemctl show spot-os-update.service -p MainPID -p NoNewPrivileges -p AmbientCapabilities -p CapabilityBoundingSet -p ProtectKernelModules -p ReadWritePaths -p DropInPaths
systemctl show spot-os-session.service lepton-streamer.service -p ActiveState -p MainPID -p InvocationID -p NRestarts
stat -c '%n dev=%d inode=%i mode=%a uid=%u gid=%g' /run/lock/spot-lepton/cci.lock
sha256sum /usr/local/bin/lepton_streamer /etc/spot-os/lepton-compat.json /etc/spot-os/os-release.pub /etc/spot-os/os-trust.json
/usr/bin/python3 -I - <<'PY'
import json,subprocess
from pathlib import Path
pid=int(subprocess.check_output(['systemctl','show','spot-os-update.service','-p','MainPID','--value']))
fields={'Uid','Gid','CapInh','CapPrm','CapEff','CapBnd','CapAmb','NoNewPrivs','Seccomp','PPid'}
out={}
for path in Path('/proc').glob('[0-9]*') if pid>1 else []:
    try:
        value={k:v.strip() for k,v in (line.split(':',1) for line in (path/'status').read_text().splitlines() if ':' in line) if k in fields}
        if path.name==str(pid) or value.get('PPid')==str(pid):out[path.name]=value
    except OSError:pass
print(json.dumps({'updater_process_credentials':out},indent=2))
PY
if test -f /usr/local/lib/spot-os/ota-bridge-0.1.6/spot_os/bridge_entry.py && /usr/bin/python3 -I -c 'import json,sys;sys.exit(0 if json.load(open("/etc/spot-os/config.json"))["os_version"]=="0.1.4" else 1)'; then
    /usr/bin/python3 -I /usr/local/lib/spot-os/ota-bridge-0.1.6/spot_os/bridge_entry.py --doctor
else
    /usr/local/bin/spot-os doctor
fi
