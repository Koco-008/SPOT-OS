"""Build tested pinned userspace/overlay on Linux, without installing any driver."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from spot_os import lepton_source as source
from spot_os.lepton import MARKERS, REVIEWED_COMMIT


def main():
    data=source.download()
    with tempfile.TemporaryDirectory(prefix='spot-lepton-build-') as tmp:
        folder=source.extract(data,Path(tmp)/'source')
        for name,targets in (('lepton_sdk',['all']),('lepton_control',['rpi_vsync_app','rpi_recovery_app','check']),
                             ('lepton_streamer',['all','check']),('lepton_module',['overlay'])):
            subprocess.run(['make','-C',str(folder/name),*targets],check=True,timeout=180)
        binary=(folder/'lepton_streamer/lepton_streamer').read_bytes()
        assert all(marker in binary for marker in MARKERS)
        assert (folder/'lepton_module/flir-lepton-rpi5.dtbo').stat().st_size>100
    proof=dict(source_commit=REVIEWED_COMMIT,source_sha256=hashlib.sha256(data).hexdigest(),
        native_userspace_build=True,radiometry_tests=True,frame_pipeline_tests=True,cci_tests=True,
        overlay_build=True,kernel_module_build=False,hardware_tested=False,driver_installed=False)
    out=ROOT/'outputs/spot-os-0.1.7';out.mkdir(parents=True,exist_ok=True)
    (out/'lepton-source-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps(proof))


if __name__=='__main__':main()
