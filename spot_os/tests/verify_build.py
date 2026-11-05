"""Two independent deterministic builds, then verify every extracted member."""
import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from spot_os.package import build,verify_package


def main():
    output=ROOT/'outputs/spot-os-0.1.7'
    first=build(output)
    git=['git','-c','safe.directory='+ROOT.as_posix()]
    with tempfile.TemporaryDirectory(prefix='spot-os-repro-') as temporary:
        temporary=Path(temporary)
        second=build(temporary/'second')
        archive=Path(first['artifact'])
        assert archive.read_bytes()==Path(second['artifact']).read_bytes()
        with zipfile.ZipFile(archive) as bundle:
            assert bundle.testzip() is None
            bundle.extractall(temporary/'verify')  # our own just-generated archive
        manifest=verify_package(temporary/'verify/spot_os')
        # CI tests may leave ignored native outputs or diagnostic files. Compare
        # against a THIRD build from the clean immutable Git tree, so two builds
        # of the same contaminated workspace cannot masquerade as provenance.
        raw=subprocess.check_output(git+['archive','--format=zip','HEAD','spot_os'],cwd=ROOT)
        with zipfile.ZipFile(io.BytesIO(raw)) as clean:
            clean.extractall(temporary/'git-source')
        third=build(temporary/'git-build',temporary/'git-source/spot_os')
        assert archive.read_bytes()==Path(third['artifact']).read_bytes(),'Runtime payload differs from clean Git source'
    def revision(ref):
        return subprocess.check_output(git+['rev-parse',ref],cwd=ROOT,text=True).strip()
    proof=dict(source_commit=revision('HEAD'),source_tree=revision('HEAD^{tree}'),
        source_clean=not subprocess.check_output(git+['status','--porcelain'],cwd=ROOT,text=True).strip(),
        source_status=subprocess.check_output(git+['status','--porcelain'],cwd=ROOT,text=True).splitlines(),
        version=manifest['version'],revision=manifest['revision'],zip_sha256=first['sha256'],
        zip_bytes=archive.stat().st_size,files=first['files'],
        independent_builds=3,clean_git_build_identical=True,byte_identical=True,all_members_verified=True)
    (output/'build-verification.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(proof,indent=2))


if __name__=='__main__':main()
