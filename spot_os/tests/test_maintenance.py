import os
from pathlib import Path
import subprocess
import sys
import pytest
from spot_os.system import System
from spot_os.installer import Journal


@pytest.mark.skipif(os.name!='posix',reason='Real POSIX flock against a separate process')
def test_maintenance_guard_holds_both_existing_locks_and_releases(tmp_path):
    for name in ('app.lock','update.lock'):(tmp_path/name).write_bytes(b'')
    system=System(tmp_path)
    code='''import fcntl,sys
for path in sys.argv[1:]:
 with open(path,'rb') as f:
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:continue
  raise SystemExit(2)
'''
    paths=[str(tmp_path/name) for name in ('app.lock','update.lock')]
    with system.maintenance_guard(tmp_path):
        assert subprocess.run([sys.executable,'-c',code,*paths]).returncode==0
    assert subprocess.run([sys.executable,'-c',code,*paths]).returncode==2
    assert all((tmp_path/name).read_bytes()==b'' for name in ('app.lock','update.lock'))


@pytest.mark.skipif(os.name!='posix',reason='Unix boot mount permission metadata')
def test_boot_file_permissions_preserved_and_rollback_restores_owner(tmp_path):
    system=System(tmp_path)
    path=system.path('/boot/firmware/config.txt')
    path.parent.mkdir(parents=True)
    path.write_bytes(b'original')
    path.chmod(0o755)
    info=path.stat()
    j=Journal(system,'permissions')
    j.write('/boot/firmware/config.txt',b'new',0o644)
    assert path.stat().st_mode & 0o777 == 0o755
    j.restore()
    assert path.read_bytes()==b'original'
    assert (path.stat().st_uid,path.stat().st_gid,path.stat().st_mode)==(info.st_uid,info.st_gid,info.st_mode)
