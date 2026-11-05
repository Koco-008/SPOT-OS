"""Production service main loop in a disposable PID1 unit, no physical hardware."""
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from spot_os import update_service as service
from spot_os.update_engine import Engine
from spot_os.update_backend import Backend
from spot_os.update_handover import evidence
from spot_os.system import System


def main():
    assert os.geteuid()==0 and not Path('/proc/device-tree/model').exists()
    base=Path(sys.argv[1]);unit=sys.argv[2];mode=sys.argv[3]
    class B(Backend):
        def __init__(self):
            self.system=System();self.handover_requested=False;self.config=dict(uid=65534)
        def installed(self):return '0.1.7','signed-os-updates-1','0.28.13'
        def confirm_handover(self,expected,origin,camera_required):
            if mode=='failure':raise RuntimeError('injected unconfirmed successor')
            return evidence(self.system,origin,unit=unit)
    class E(Engine):
        def __init__(self,backend):super().__init__(backend,base/'state')
        def resume(self):
            super().resume()
            (base/('resumed-'+os.environ['INVOCATION_ID'])).write_text('done')
        def _resume_owned(self):
            if self.data['phase']=='idle':
                phase='success' if mode=='legacy-error' else 'handover-pending'
                message='OS-Sitzung geprüft; Updater-Neustart ausstehend, SSH-Prüfung erforderlich: Command failed: /usr/bin/systemctl' if mode=='legacy-error' else 'committed test transaction'
                self.save(phase,message,version='0.1.7',handover_origin=self.backend.process_identity())
                self.backend.activate_service()
            else:super()._resume_owned()
    service.Backend=B;service.Engine=E
    service.state_directory=lambda:base/'state'
    service.SOCKET=base/'control.sock'
    return service.main()


if __name__=='__main__':raise SystemExit(main())
