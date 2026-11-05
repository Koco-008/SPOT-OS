"""Persistent root transaction. Survives the GUI and reuses UpgradeJournal."""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from .common import atomic,read
from .update_contract import verify,compatible,unpack,trust,REPOSITORY
from .update_source import Source
from .update_owner import owner, OwnerBusy

TERMINAL={'idle','current','available','prepared','success','rolled-back','error','recovery-failed'}
HANDOVER={'handover-pending','handover-failed'}
MUTATING={'awaiting-exit','stopping','installing','starting','verifying','rollback-stopping','rolling-back','rollback-starting','rollback-verifying'}


def error_message(exc):
    from cryptography.exceptions import InvalidSignature
    if isinstance(exc,InvalidSignature):return 'OS-Signatur ungültig; Update verweigert'
    if isinstance(exc,ValueError):return 'OS-Sicherheits-/Kompatibilitätsprüfung fehlgeschlagen: '+str(exc)
    if isinstance(exc,TimeoutError):return 'Zeitlimit beim OS-Update überschritten'
    if isinstance(exc,OSError):return 'OS-Netzwerk-/Speicher-/Dateifehler: '+str(exc)
    return str(exc) or type(exc).__name__


class Engine:
    def __init__(self,backend,root=Path('/var/lib/spot-os/updates'),key_provider=trust,source_factory=Source):
        self.backend,self.root=backend,Path(root)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.key_provider,self.source_factory=key_provider,source_factory
        self.guard=threading.Lock()
        self.thread=None
        self.data=read(self.root/'transaction.json',dict(phase='idle',message='Bereit'))
        self.operation=None
        self.backend.progress=self.save

    def save(self,phase,message,**extra):
        with self.guard:
            self.data.update(phase=phase,message=str(message)[:400],updated=time.time(),**extra)
            atomic(self.root/'transaction.json',self.data)
            record=json.dumps(dict(at=time.time(),phase=phase,version=self.data.get('version'),
                candidate=self.data.get('candidate'),source_commit=self.data.get('source_commit'),
                zip_sha256=self.data.get('zip_sha256'),message=str(message)[:400]))+'\n'
            with (self.root/'audit.jsonl').open('a',encoding='utf-8') as stream:
                os.chmod(self.root/'audit.jsonl',0o600);stream.write(record);stream.flush();os.fsync(stream.fileno())

    def status(self):
        installed,revision,app=self.backend.installed()
        try:self.key_provider();configured=True;setup_error=''
        except Exception as exc:configured=False;setup_error='Automatische OS-Updates noch nicht eingerichtet: '+str(exc)[:250]
        with self.guard:
            durable=read(self.root/'transaction.json',self.data)
            value={k:v for k,v in durable.items() if k in ('phase','message','version','notes','channel','candidate','bytes','size','camera','updated')}
            value.update(installed=installed,revision=revision,app=app,configured=configured,setup_error=setup_error,
                         busy=bool(self.thread and self.thread.is_alive()) or durable.get('phase') in MUTATING|{'handover-pending'})
            # App 0.28.13's IPC vocabulary stays unchanged. A durable handover
            # has its own journal phase, but existing clients see verifying or
            # an actionable failure, never a premature success/old warning.
            if durable.get('phase')=='handover-pending' or (durable.get('phase')=='success' and durable.get('handover_confirmed') is not True):
                value.update(phase='verifying',message='OS-Abschluss und neuer Updater werden geprüft',busy=True)
            elif durable.get('phase')=='handover-failed':value['phase']='recovery-failed'
            if 'notes' in value:value['notes']=value['notes'][:500]
        return value

    def candidate(self):
        value=read(self.root/'candidate.json')
        if not value:raise RuntimeError('OS-Kandidat fehlt; erneut prüfen')
        raw=base64.b64decode(value['raw'],validate=True);signature=base64.b64decode(value['signature'],validate=True)
        data=verify(raw,signature,self.key_provider())
        installed,_,app=self.backend.installed()
        compatible(data,installed,app,read(self.root/'floor.json',{}),value['prerelease'])
        return dict(raw=raw,signature=signature,manifest=data,release=value['release'])

    def check(self,prerelease=False):
        source=self.source_factory(self.key_provider(),self.progress)
        installed,_,app=self.backend.installed()
        candidate=source.check(installed,app,read(self.root/'floor.json',{}),prerelease)
        if candidate is None:
            self.save('current','Keine neuere freigegebene OS-Version verfügbar',candidate=None,version=None,notes='')
            return
        data=candidate['manifest'];identifier=hashlib.sha256(candidate['raw']).hexdigest()
        atomic(self.root/'candidate.json',dict(raw=base64.b64encode(candidate['raw']).decode(),
            signature=base64.b64encode(candidate['signature']).decode(),release=candidate['release'],prerelease=prerelease))
        proofs=self.root/'manifests';proofs.mkdir(mode=0o700,exist_ok=True)
        for suffix,contents in (('.json',candidate['raw']),('.sig',candidate['signature'])):
            path=proofs/(identifier+suffix)
            if path.exists():
                if path.is_symlink() or path.read_bytes()!=contents:raise ValueError('OS manifest history collision')
            else:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
                with os.fdopen(fd,'wb') as stream:stream.write(contents);stream.flush();os.fsync(stream.fileno())
        self.save('available','OS-Version verfügbar',candidate=identifier,version=data['version'],
                  channel=data['channel'],notes=data['notes'],size=data['size'],bytes=0,
                  zip_sha256=data['sha256'],source_commit=data['source_commit'])

    def progress(self,phase,count):
        # Root-owned bounded progress; no per-chunk audit growth.
        with self.guard:self.data.update(bytes=count)

    def prepare(self,identifier):
        candidate=self.candidate();data=candidate['manifest']
        if hashlib.sha256(candidate['raw']).hexdigest()!=identifier:raise ValueError('OS-Kandidat hat gewechselt')
        if shutil.disk_usage(self.root).free<max(512*1024**2,data['size']*4):raise OSError('Zu wenig freier Speicher für OS-Update')
        self.save('downloading','Betriebssystem wird heruntergeladen',bytes=0)
        raw=self.source_factory(self.key_provider(),self.progress).download(candidate)
        # Re-read fixed trust anchor and verify again after network I/O.
        if verify(candidate['raw'],candidate['signature'],self.key_provider())!=data:raise ValueError('OS metadata changed')
        self.save('checking-package','Signatur und alle OS-Dateien werden geprüft')
        folder=unpack(raw,data,self.root/('validated-'+uuid.uuid4().hex))
        try:self.backend.preflight(folder,data)
        finally:shutil.rmtree(folder.parent)
        archive=self.root/(data['sha256']+'.zip')
        if archive.exists():
            if archive.is_symlink() or archive.read_bytes()!=raw:raise ValueError('OS stage collision')
        else:
            fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        self.save('prepared','Geprüft und bereit zur Bestätigung',bytes=data['size'])

    def install(self,identifier,pid):
        candidate=self.candidate();data=candidate['manifest']
        if self.data.get('phase')!='prepared' or hashlib.sha256(candidate['raw']).hexdigest()!=identifier:
            raise ValueError('OS-Installation nicht vorbereitet')
        # Last verification and fresh extraction BEFORE privileged changes.
        archive=self.root/(data['sha256']+'.zip')
        folder=unpack(archive.read_bytes(),data,self.root/('install-'+uuid.uuid4().hex))
        try:
            self.backend.preflight(folder,data)
            before=self.backend.consent(pid)
        except BaseException:
            shutil.rmtree(folder.parent)
            raise
        self.data.update(old_invocation=before.get('session_invocation'),camera_required=before.get('camera') is True,
                         previous=self.backend.installed()[0],rollback_attempts=0)
        changed=False
        try:
            with self.backend.transaction_guard(),self.backend.update_lock():
                # The owner is now independent of the GUI and blocks AppUpdate.
                self.backend.cover()
                self.save('awaiting-exit','SPOT wird sauber beendet; bitte warten')
                self.backend.wait_exit(pid)
                self.save('stopping','Grafische Sitzung wird angehalten')
                atomic(self.root/'session-blocked',dict(blocked=True))
                changed=True
                self.backend.stop()
                self.save('installing','Betriebssystem wird transaktional installiert')
                # Attempt-floor is not part of Installer rollback snapshots.
                atomic(self.root/'floor.json',dict(version=data['version'],sequence=data['sequence']))
                self.backend.upgrade(folder,data)
                self.save('starting','Neue SPOT-Sitzung wird gestartet')
                (self.root/'session-blocked').unlink(missing_ok=True)
            # App lock must be free before the signed launcher can start.
            self.backend.start()
            self.save('verifying','Neue Sitzung und Kamera werden geprüft')
            result=self.backend.healthy(self.data['old_invocation'],self.data['camera_required'])
            self.confirm(result,data['version'])
            # This is the durable installation commit. From this point onward
            # only the successor confirms its own identity; never roll back a
            # healthy committed OS because the old daemon is exiting.
            self.save('handover-pending','OS-Sitzung geprüft; neuer Updater wird bestätigt',
                camera=result.get('camera') is True,handover_origin=self.backend.process_identity())
            try:self.backend.activate_service()
            except Exception as exc:
                self.save('handover-failed','Updater-Prozesswechsel nicht bestätigt; administrative Prüfung erforderlich: '+str(exc))
        except Exception as exc:
            if changed:self.recover(str(exc))
            else:
                self.save('error','Keine OS-Änderung: '+str(exc))
                self.backend.cancel_cover()
        finally:shutil.rmtree(folder.parent)

    def recover(self,reason='Unterbrochene OS-Transaktion'):
        # One restoration attempt across service restarts/power failures. A further
        # interruption is reported, never turned into an endless rollback loop.
        attempts=self.data.get('rollback_attempts',0)
        if attempts>=2:
            self.save('recovery-failed','Rollback unterbrochen oder nicht bestätigt; SSH-Prüfung erforderlich')
            return
        self.save('rollback-stopping',reason,rollback_attempts=attempts+1)
        atomic(self.root/'session-blocked',dict(blocked=True))
        try:
            with self.backend.transaction_guard(),self.backend.update_lock():
                self.backend.stop()
                self.save('rolling-back','Vorheriger OS-Zustand wird wiederhergestellt')
                self.backend.rollback(self.data.get('version'),self.data.get('previous'))
                self.save('rollback-starting','Vorherige SPOT-Sitzung wird gestartet')
                (self.root/'session-blocked').unlink(missing_ok=True)
            self.backend.start()
            self.save('rollback-verifying','Wiederhergestellte Sitzung wird geprüft')
            result=self.backend.healthy(self.data.get('old_invocation'),self.data.get('camera_required',False))
            self.confirm(result,self.data.get('previous'))
            self.save('rolled-back','OS-Update fehlgeschlagen; vorheriges OS wiederhergestellt',camera=result.get('camera') is True)
        except Exception as exc:self.save('recovery-failed','Automatischer Rückweg nicht bestätigt: '+str(exc))

    def confirm(self,result,expected):
        if (not expected or self.backend.installed()[0] != expected or result.get('gui') is not True or
                (self.data.get('camera_required') and result.get('camera') is not True)):
            raise RuntimeError('OS-Version, GUI oder Kamera nicht bestätigt')

    def resume(self):
        try:
            with owner(self.root):
                self.data=read(self.root/'transaction.json',self.data)
                if self.data.get('phase') in MUTATING:
                    atomic(self.root/'session-blocked',dict(blocked=True))
                self._resume_owned()
        except OwnerBusy:
            # A manual recovery can start a GUI which opens the update socket.
            # This newly activated daemon must neither save nor recover its state.
            return

    def _resume_owned(self):
        phase=self.data.get('phase')
        # The 0.1.6 in-memory engine may have recorded either a normal success
        # or its misleading restart warning while installing this new build.
        legacy=(phase=='success' and self.data.get('handover_confirmed') is not True)
        if phase in HANDOVER or legacy:
            try:
                proof=self.backend.confirm_handover(self.data.get('version'),self.data.get('handover_origin'),self.data.get('camera_required',False))
                self.save('success','SPOT OS '+self.data['version']+' wurde erfolgreich installiert.',
                    handover_evidence=proof,handover_previous_message=self.data.get('message'),handover_confirmed=True)
            except Exception as exc:
                self.save('handover-failed','Updater-Abschluss nicht bestätigt; administrative Prüfung erforderlich: '+str(exc))
        elif phase in MUTATING:self.recover()
        elif phase not in TERMINAL:self.save('error','Vorbereitung unterbrochen; keine Installation fortgesetzt')

    def dispatch(self,request,pid,async_=True):
        if not isinstance(request,dict):raise ValueError('OS request object')
        action=request.get('action')
        fields={'status':{'action'},'check':{'action','prerelease'},'prepare':{'action','candidate'},'install':{'action','candidate'}}
        if action not in fields or set(request)!=fields[action]:raise ValueError('Unzulässige OS-Aktion oder Parameter')
        if action=='status':return self.status()
        # Reserve across PROCESSES before inspecting a mutable journal. Transfer
        # the open descriptor to the worker; release only after verified health.
        ownership=owner(self.root)
        ownership.__enter__()
        try:
            return self._dispatch_owned(request,pid,async_,ownership)
        except BaseException:
            ownership.__exit__(*__import__('sys').exc_info())
            raise

    def _dispatch_owned(self,request,pid,async_,ownership):
        self.data=read(self.root/'transaction.json',self.data)
        action=request['action']
        if self.data.get('phase') in HANDOVER|{'recovery-failed'} or (self.root/'session-blocked').exists():
            raise RuntimeError('OS-Wiederherstellung muss zuerst administrativ geprüft werden')
        if action=='check' and type(request['prerelease']) is not bool:raise ValueError('OS channel flag')
        if action in ('prepare','install') and (not isinstance(request['candidate'],str) or
                len(request['candidate'])!=64 or any(c not in '0123456789abcdef' for c in request['candidate'])):
            raise ValueError('OS candidate identity')
        with self.guard:
            if self.thread and self.thread.is_alive():raise RuntimeError('Ein OS-Vorgang läuft bereits')
            # Reserve before starting a thread; two simultaneous requests cannot race.
            if self.operation is not None:raise RuntimeError('OS-Vorgang reserviert')
            self.operation=action
        def run():
            try:
                if action=='check':self.save('checking','OS-Versionen werden geprüft');self.check(request['prerelease'])
                elif action=='prepare':self.prepare(request['candidate'])
                else:self.install(request['candidate'],pid)
            except Exception as exc:self.save('error',error_message(exc))
            finally:
                with self.guard:self.operation=None
                ownership.__exit__(None,None,None)
        if async_:
            self.thread=threading.Thread(target=run,name='spot-os-transaction',daemon=False)
            self.thread.start()
        else:run()
        return self.status()
