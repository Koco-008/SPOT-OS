"""Full suite from the exact public signed app; no rebuild, network or Pi use."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from spot_update import verify_signature,extract_verified


def main():
    fixture=Path(__file__).with_name('fixtures')/'spot-0.28.12'
    provenance=json.loads((fixture/'provenance.json').read_text(encoding='utf-8'))
    for name,digest in provenance['files'].items():
        assert hashlib.sha256((fixture/name).read_bytes()).hexdigest()==digest,name
    key=ROOT/'assets/trust/spot-release.pub'
    assert hashlib.sha256(key.read_bytes()).hexdigest()==provenance['trust_key_sha256']
    manifest=verify_signature((fixture/'update.json').read_bytes(),(fixture/'update.json.sig').read_bytes(),key)
    assert manifest['version']=='0.28.12' and manifest['sha256']==provenance['files'][manifest['asset']]
    output=ROOT/'outputs/spot-os-0.1.7'
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='spot-os-signed-app-') as temporary:
        release=Path(temporary)
        extract_verified(fixture/manifest['asset'],release,manifest['version'])
        result=subprocess.run([sys.executable,'-m','pytest','tests','-q',
            '--junitxml='+str(output/'signed-app-02812-junit.xml')],cwd=release,
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen',
                PYTHONPATH=str(release)+os.pathsep+os.environ.get('PYTHONPATH','')),timeout=900)
    proof=dict(source_commit=provenance['source_commit'],version=manifest['version'],
        zip_sha256=manifest['sha256'],signature_valid=True,unmodified_published_fixture=True,
        returncode=result.returncode,hardware_tested=False)
    (output/'signed-app-02812-proof.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(proof),flush=True)
    return result.returncode


if __name__=='__main__':sys.exit(main())
