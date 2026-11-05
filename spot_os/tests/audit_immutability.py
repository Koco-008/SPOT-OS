"""Compare immutable app/launcher/driver/trust files to the supplied OS 0.1 base."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
BASE='adb8aa549393b40c3e00e95423be23f8a3715761'
APP_BASE='29fd45d32eb2be7bf759cf82876ec7d373cade70'
OS_BASE='7cd5b7efe79e74fd4148ee586749be7935fd05d4'
OLD_ZIP_SHA='9f86c74815ba23000e59658c13eeec1955b57cf96ee0b85ecc064cd30481b764'


def main():
    git=['git','-c','safe.directory='+ROOT.as_posix()]
    def command(*args):return subprocess.run(git+list(args),cwd=ROOT,capture_output=True,check=True).stdout
    names=command('ls-tree','-r','--name-only',BASE).decode().splitlines()
    protected=[p for p in names if not p.startswith('spot_os/') and p not in (
        '.github/workflows/spot-os.yml','.github/workflows/spot-os-session.yml',
        'SPOT_v27.py','VERSION','CHANGELOG.md','spot_update_ui.py','spot_device_control.py',
        'tests/test_updates_0285.py','.github/workflows/tests.yml',
        '.gitattributes','tests/test_ui_audit_0283.py')]
    changed=command('diff','--name-only',BASE,'--',*protected).decode().splitlines()
    assert not changed,changed
    # Freeze ALL published 0.28.13 application/test/driver files, including the
    # previous audit's UI allowlist. OS-only workflow and architecture docs are
    # not part of the app payload.
    app_names=command('ls-tree','-r','--name-only',APP_BASE).decode().splitlines()
    frozen=[p for p in app_names if not p.startswith(('spot_os/','.github/','docs/OS_'))]
    app_changed=command('diff','--name-only',APP_BASE,'--',*frozen).decode().splitlines()
    assert not app_changed,app_changed
    # These two pre-existing OS-0.1.3 changes are independent of App 0.28.12:
    # binary cursor attributes and a clock fixture away from a float boundary.
    # Freeze them to the actual OS base instead of allowing arbitrary edits.
    for name in ('.gitattributes','tests/test_ui_audit_0283.py'):
        expected=command('show',OS_BASE+':'+name).replace(b'\r\n',b'\n')
        assert (ROOT/name).read_bytes().replace(b'\r\n',b'\n')==expected,name
    import io,zipfile
    with zipfile.ZipFile(io.BytesIO(command('archive','--format=zip',BASE))) as archive:
        original=ast.parse(archive.read('SPOT_v27.py').decode())
    current=ast.parse((ROOT/'SPOT_v27.py').read_text(encoding='utf-8'))
    for name in ('LeptonStream','VideoRecorder'):
        a=next(n for n in original.body if isinstance(n,ast.ClassDef) and n.name==name)
        b=next(n for n in current.body if isinstance(n,ast.ClassDef) and n.name==name)
        assert ast.dump(a)==ast.dump(b),name

    assert (ROOT/'VERSION').read_text().strip()=='0.28.13'
    for path in (ROOT/'spot_os').rglob('*.py'):ast.parse(path.read_text(encoding='utf-8'))
    old=ROOT/'outputs/spot-os-0.1/spot-os-0.1.zip'
    old_sha=hashlib.sha256(old.read_bytes()).hexdigest() if old.is_file() else None
    if old_sha is not None:assert old_sha==OLD_ZIP_SHA
    old011=ROOT/'outputs/spot-os-0.1.1/spot-os-0.1.1.zip'
    if old011.is_file(): assert hashlib.sha256(old011.read_bytes()).hexdigest()=='84b9569506e937e228e8655008054f31b1a5a287c92f759d92935ebcedf94b59'
    result=dict(base=BASE,head=command('rev-parse','HEAD').decode().strip(),app_version='0.28.13',
        protected_files=len(protected),changed_protected=changed,app_base=APP_BASE,
        app_02813_frozen_files=len(frozen),app_02813_changes=app_changed,old_01_zip_sha256=old_sha,
        old_01_zip_present=old.is_file(),scope='repository and local old ZIP; no device access',
        trust_sha256=hashlib.sha256((ROOT/'assets/trust/spot-release.pub').read_bytes()).hexdigest())
    out=ROOT/'outputs/spot-os-0.1.7'
    out.mkdir(parents=True,exist_ok=True)
    (out/'immutability-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
