"""Fixed OS actions. No request can supply a command, user or destination."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import time
from .common import read
from .installer import Installer,Journal,STATE
from .system import System


@contextmanager
def launcher_lock(path,uid):
    import fcntl
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid:raise ValueError('Launcher lock identity')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield fd
        if os.stat(path,follow_symlinks=False).st_ino!=info.st_ino:raise ValueError('Launcher lock replaced')
    finally:os.close(fd)


class Backend:
    def __init__(self,system=None):
        self.system=system or System()
        self.config=read(self.system.path('/etc/spot-os/config.json'),{})
        if self.config.get('user')=='root' or type(self.config.get('uid')) is not int or self.config['uid']<=0:
            raise ValueError('OS session account not provisioned')
        self.locks=[]
        self.handover_requested=False

    def installed(self):
        config=read(self.system.path('/etc/spot-os/config.json'))
        app=read(Path(config['prefix'])/'current.json')
        from .update_contract import version
        version(config['os_version']);version(app['version'])
        return config['os_version'],config['os_revision'],app['version']

    def preflight(self,folder,data):
        result=Installer(self.system,folder,target=(data['version'],data['revision']),online=True).preflight(self.config['user'],maintenance=False)
        if not result['ok']:
            raise RuntimeError('OS-Voraussetzungen fehlen: '+', '.join(k for k,v in result['checks'].items() if not v))

    def status(self):
        from .cli import request
        return request(self.config,'status')

    def consent(self,pid):
        from .manager import identity
        status=self.status()
        if (status.get('app_pid')!=pid or status.get('state')!='running' or status.get('busy') is not False
                or status.get('evidence_age',999)>1 or status.get('sleeping') or status.get('power_state')!='active'
                or status.get('transaction') not in (None,'committed','recovered','rolled-back')):
            raise RuntimeError('SPOT ist beschäftigt oder die Sitzung ist nicht aktuell bestätigt')
        if not identity(pid,Path(self.config['prefix']),status.get('app')):raise PermissionError('OS installation requires actual launched GUI')
        return status

    def cover(self):
        from .cli import request
        if not request(self.config,'os-update-cover').get('covered'):raise RuntimeError('SPOT-Splash nicht bestätigt')

    def wait_exit(self,pid,timeout=45):
        # Observe exact PID identity; never signal or forcibly finish an activity.
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if not Path('/proc',str(pid)).exists():return
            time.sleep(.2)
        raise RuntimeError('SPOT wurde nicht sauber beendet; Installation abgebrochen')

    @contextmanager
    def update_lock(self):
        with launcher_lock(Path(self.config['prefix'])/'update.lock',self.config['uid']):yield

    @contextmanager
    def transaction_guard(self):
        import fcntl
        path=self.system.path(STATE+'/installer.lock')
        fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        finally:os.close(fd)

    def cancel_cover(self):
        try:
            from .cli import request
            request(self.config,'os-update-cancel')
        except (OSError,RuntimeError):pass

    def stop(self):
        # Recovery must not terminate user work that began after a daemon loss.
        # The normal install arrives here only AFTER the exact GUI exited.
        processes=[]
        for proc in self.system.path('/proc').glob('[0-9]*'):
            try:
                if any(Path(os.fsdecode(arg)).name.lower().startswith('spot_v') and arg.endswith(b'.py')
                       for arg in (proc/'cmdline').read_bytes().split(b'\0')):processes.append(proc.name)
            except OSError:continue
        if processes:
            current=self.status()
            if (current.get('busy') is not False or current.get('evidence_age',999)>1 or
                    current.get('power_state')!='active'):
                raise RuntimeError('SPOT ist beschäftigt oder ungeprüft; keine erzwungene Session-Beendigung')
            from .cli import request
            if current.get('state')=='running':self.cover()
            if not request(self.config,'os-update-quiesce').get('requested'):
                raise RuntimeError('GUI-Quieszenz nicht bestätigt')
            self.wait_exit(current['app_pid'])
        self.system.command('/usr/bin/systemctl','stop','spot-os-session.service',timeout=110)
        Installer(self.system).require_stopped()
        from .ownership import cleanup
        cleanup(self.system,self.config)

    @contextmanager
    def maintenance(self):
        with launcher_lock(Path(self.config['prefix'])/'app.lock',self.config['uid']):
            # Both launcher locks are held by this exact transaction, not bypassed.
            backend=self
            class OwnedSystem(System):
                def inventory(self,user):
                    data=backend.system.inventory(user)
                    if data.get('app_processes'):raise RuntimeError('Unexpected application process during OS upgrade')
                    if data.get('transaction_state') not in (None,'committed','recovered','rolled-back'):
                        raise RuntimeError('Pending app update')
                    data.update(app_locked=False,update_locked=False)
                    return data
                def maintenance_guard(self,prefix):
                    if prefix!=backend.config['prefix']:raise ValueError('Maintenance prefix changed')
                    from contextlib import nullcontext
                    return nullcontext()
                def command(self,*args,**kwargs):return backend.system.command(*args,**kwargs)
            yield OwnedSystem(self.system.root)

    def upgrade(self,folder,data):
        with self.maintenance() as system:
            installer=Installer(system,folder,target=(data['version'],data['revision']),online=True)
            result=installer.upgrade(self.config['user'])
            if not result.get('upgraded') or not installer.check(allow_transaction=True)['ok']:raise RuntimeError('OS integrity check failed')
            import json
            result=json.loads(system.command('/usr/local/bin/spot-os','static-check',timeout=90))
            if not result.get('ok'):raise RuntimeError('New OS runtime/static validation failed')

    def rollback(self,expected,previous):
        with self.maintenance() as system:
            journal=Journal(system,'upgrade')
            if journal.data.get('to_version')!=expected or journal.data.get('from_version')!=previous:
                if (read(system.path('/etc/spot-os/config.json')).get('os_version')==previous and
                        journal.data['phase'] in ('new','committed','restored') and
                        Installer(system).check(allow_transaction=True)['ok']):return
                raise RuntimeError('OS rollback journal belongs to another transaction')
            if journal.data['phase'] not in ('new','restored'):
                journal.restore()
                system.command('/usr/bin/systemctl','daemon-reload')
            if not Installer(system).check(allow_transaction=True)['ok']:raise RuntimeError('Previous OS integrity could not be restored')

    def start(self):
        # No reset-failed. A consumed maintenance window gets one bounded retry
        # after expiry; actual process/notify/watchdog failures are not hidden.
        try:self.system.command('/usr/bin/systemctl','start','spot-os-session.service',timeout=60)
        except RuntimeError:
            result=self.system.command('/usr/bin/systemctl','show','spot-os-session.service','-p','Result','--value')
            if result!='start-limit-hit':raise
            unit=self.system.path('/etc/systemd/system/spot-os-session.service').read_text()
            if 'StartLimitIntervalSec=120\n' not in unit:raise
            self.progress('starting','systemd-Startfenster belegt; automatischer Wiederanlauf nach höchstens 122 Sekunden')
            time.sleep(122)
            self.system.command('/usr/bin/systemctl','start','spot-os-session.service',timeout=60)

    def activate_service(self):
        # The service main loop exits AFTER the worker releases its owner lock.
        # PID1 performs the bounded restart; no systemctl child is killed by its
        # own unit's KillMode=control-group while replying to a restart request.
        self.handover_requested=True

    def process_identity(self):
        from .update_handover import identity
        return identity(self.system)

    def confirm_handover(self,expected,origin,camera_required):
        from . import VERSION
        from .update_handover import evidence
        if expected!=VERSION or self.installed()[0]!=expected:
            raise RuntimeError('Updater-Code und installiertes OS stimmen nicht überein')
        checked=Installer(self.system).check(allow_transaction=True)
        if not checked['files_ok'] or not checked['installation_complete'] or not checked['active']:
            raise RuntimeError('OS-Installation/Aktivierung für Updater-Handover nicht bestätigt')
        current=evidence(self.system,origin)
        status=self.status()
        health=read(self.system.path(STATE+'/health.json'),{})
        if (status.get('state')!='running' or status.get('gui') is not True or status.get('evidence_age',999)>=1
                or (camera_required and status.get('camera') is not True)
                or health.get('boot_verified') is not True or health.get('boot')!=current['boot']
                or health.get('session_invocation')!=status.get('session_invocation')):
            raise RuntimeError('GUI/Kamera beim Updater-Handover nicht bestätigt')
        return current

    def healthy(self,old_invocation,camera_required,timeout=125):
        deadline=time.monotonic()+timeout
        stable=None
        while time.monotonic()<deadline:
            try:
                config=read(self.system.path('/etc/spot-os/config.json'))
                status=self.status()
                health=read(self.system.path(STATE+'/health.json'),{})
                boot=self.system.path('/proc/sys/kernel/random/boot_id').read_text().strip()
                valid=(status.get('state')=='running' and status.get('gui') is True and
                    status.get('evidence_age',999)<1 and not status.get('busy') and
                    status.get('session_invocation')!=old_invocation and
                    health.get('boot_verified') is True and health.get('boot')==boot and
                    health.get('session_invocation')==status.get('session_invocation') and
                    (not camera_required or status.get('camera') is True))
                stable=time.monotonic() if valid and stable is None else stable if valid else None
                if stable is not None and time.monotonic()-stable>=3:return status
            except (OSError,ValueError,RuntimeError):stable=None
            time.sleep(.5)
        raise RuntimeError('Neue OS-Sitzung/Kamera nicht bestätigt')
