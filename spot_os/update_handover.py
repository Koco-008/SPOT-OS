"""Read-only PID1/process evidence for a committed OTA's successor daemon."""
import os
import time

SERVICE='spot-os-update.service'
EXIT_STATUS=75


def identity(system):
    pid=os.getpid()
    # starttime disambiguates PID reuse. /proc stat's command can contain spaces.
    start=system.path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
    boot=system.path('/proc/sys/kernel/random/boot_id').read_text().strip()
    invocation=os.environ.get('INVOCATION_ID','')
    if not boot or not invocation or not start.isdecimal():
        raise RuntimeError('Updater-Prozessidentität nicht bestätigt')
    return dict(pid=pid,start=start,boot=boot,invocation=invocation)


def evidence(system,origin,*,unit=SERVICE,timeout=10):
    current=identity(system)
    if origin and (current==origin or (current['boot']==origin.get('boot')
            and current['invocation']==origin.get('invocation'))):
        raise RuntimeError('Neuer Updater-Prozess noch nicht gestartet')
    deadline=time.monotonic()+timeout
    while True:
        raw=system.command('/usr/bin/systemctl','show',unit,
            '-p','MainPID','-p','InvocationID','-p','ActiveState','-p','SubState','-p','Result',timeout=5)
        values=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
        if (values.get('MainPID')==str(current['pid']) and values.get('InvocationID')==current['invocation']
                and values.get('ActiveState')=='active' and values.get('SubState')=='running'
                and values.get('Result')=='success'):
            return dict(current,systemd=values)
        # READY=1 can still be in PID1's notification queue. This bounded
        # readiness wait is not a delay used as a proxy for successful startup.
        if values.get('ActiveState')!='activating' or time.monotonic()>=deadline:
            raise RuntimeError('systemd bestätigt den neuen Updater nicht')
        import threading
        threading.Event().wait(min(.1,max(0,deadline-time.monotonic())))
