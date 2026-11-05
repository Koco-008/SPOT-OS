"""Real Linux root ownership and competing-process locks in a disposable /run tree."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[2]


def main():
    script=r'''
import json,os,shutil,sys,subprocess,zipfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from spot_os.lepton_provision import admin_guard,secure_path,ProvisionError
from spot_os.system import System
root=Path(sys.argv[2]);root.mkdir(mode=0o700)
system=System(root)
cases=[]
try:
 with admin_guard(system):
  pid=os.fork()
  if pid==0:
   try:
    with admin_guard(system):os._exit(9)
   except ProvisionError:os._exit(0)
  _,status=os.waitpid(pid,0);assert os.waitstatus_to_exitcode(status)==0
 cases.append('second_process_excluded')
 lock=system.path('/var/lib/spot-os/installer.lock');lock.unlink();os.mkfifo(lock)
 try:
  with admin_guard(system):raise AssertionError('FIFO lock accepted')
 except ProvisionError:pass
 cases.append('fifo_refused');lock.unlink()
 outside=root/'outside';outside.mkdir()
 lock.symlink_to(outside/'victim')
 try:
  with admin_guard(system):raise AssertionError('symlink lock accepted')
 except ProvisionError:pass
 assert not (outside/'victim').exists();cases.append('symlink_refused');lock.unlink()
 private=system.path('/var/lib/spot-os');os.chown(private,65534,65534)
 try:
  with admin_guard(system):raise AssertionError('foreign-owned state accepted')
 except ProvisionError:pass
 os.chown(private,0,0);cases.append('foreign_owner_refused')
 # The actual fresh-package entrypoint must work before OS installation and
 # ignore hostile imports, using only system Python (no preinstalled Pillow).
 package=root/'verified';package.mkdir()
 with zipfile.ZipFile(sys.argv[3]) as archive:archive.extractall(package)
 hostile=Path(sys.argv[4])
 result=subprocess.run(['/usr/bin/python3','-I',str(package/'spot_os/setup_entry.py'),'driver-status'],
  env=dict(os.environ,PYTHONPATH=str(hostile)),cwd=hostile,capture_output=True,text=True,timeout=15)
 assert result.returncode==1,result.stderr
 assert json.loads(result.stdout)['state']=='unsupported',result.stdout
 assert not (hostile/'ATTACK').exists()
 cases.append('actual_fresh_entrypoint_isolated_before_os_install')
 print(json.dumps(dict(real_root=True,real_processes=True,cases=cases,hardware_tested=False)))
finally:
 assert root.parent==Path('/run') and root.name.startswith('spot-test-provision-')
 shutil.rmtree(root)
'''
    root='/run/spot-test-provision-'+uuid.uuid4().hex
    sys.path.insert(0,str(ROOT))
    from spot_os.package import build
    with tempfile.TemporaryDirectory(prefix='spot-bootstrap-security-') as temporary:
        temporary=Path(temporary);temporary.chmod(0o755)
        archive=build(temporary/'build')['artifact']
        hostile=temporary/'hostile';hostile.mkdir();hostile.chmod(0o755)
        (hostile/'sitecustomize.py').write_text('from pathlib import Path\nPath('+repr(str(hostile/'ATTACK'))+').write_text("UNSAFE ROOT IMPORT")\n')
        result=subprocess.run(['sudo','/usr/bin/python3','-I','-c',script,str(ROOT),root,archive,str(hostile)],
            capture_output=True,text=True,timeout=40,check=True)
    proof=json.loads(result.stdout)
    output=ROOT/'outputs/spot-os-0.1.7';output.mkdir(parents=True,exist_ok=True)
    (output/'provision-security-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps(proof))


if __name__=='__main__':main()
