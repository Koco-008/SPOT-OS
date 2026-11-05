"""Real root/unprivileged runtime and actual-interpreter dependency CI probes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from spot_os.runtime import publish_boot_status, root_readable_directory
from spot_os.system import System, qt_network_probe


def main():
    base, user, interpreter = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    assert os.geteuid() == 0
    folder = base/'root-public-runtime'
    public_config = base/'root-public-config'
    mask = os.umask(0o077)
    try:
        root_readable_directory(public_config,0o755)
        assert public_config.stat().st_mode & 0o777 == 0o755
        assert subprocess.run(['/usr/sbin/runuser','-u',user,'--','/usr/bin/test','-x',str(public_config)]).returncode==0
        for _ in range(3):
            publish_boot_status(dict(attempts=1,boot='CI',blocked=False,secret='never-publish'),folder)
            result = subprocess.run(['/usr/sbin/runuser','-u',user,'--','/usr/bin/python3','-c',
                'import json,sys;print(json.load(open(sys.argv[1])))',str(folder/'boot-blocked.json')],
                capture_output=True,text=True,check=True)
            assert 'never-publish' not in result.stdout
            assert folder.stat().st_uid == 0 and folder.stat().st_mode & 0o777 == 0o711
            assert (folder/'boot-blocked.json').stat().st_mode & 0o777 == 0o644
            os.chmod(folder,0o700)  # reproduce the previous prepare-service state
        publish_boot_status(dict(attempts=1,boot='CI',blocked=False),folder)
        private = base/'root-private-state'
        private.mkdir(mode=0o700)
        (private/'secret').write_text('not public')
        assert subprocess.run(['/usr/sbin/runuser','-u',user,'--','/usr/bin/cat',str(private/'secret')],
            capture_output=True).returncode != 0
    finally:
        os.umask(mask)
    system = System()
    assert qt_network_probe(system,user,interpreter),'Actual CI GUI interpreter QLocalSocket import failed'
    isolated = base/'without-qtnetwork'
    subprocess.run([interpreter,'-m','venv','--without-pip',str(isolated)],check=True)
    assert not qt_network_probe(system,user,str(isolated/'bin/python'))
    # These three uniquely named children were created by this root-only helper.
    # Remove them here so the unprivileged fixture can remove its own temp root.
    for path in (folder,public_config,private,isolated):
        assert path.parent == base and path.resolve().is_relative_to(base.resolve())
        shutil.rmtree(path)
    print(json.dumps(dict(real_root_UMask_0077=True, unprivileged_read=True,
        repeat_repair=True,private_state_unreadable=True,
        actual_app_QLocalSocket_import=True,isolated_interpreter_missing_module_rejected=True)))


if __name__ == '__main__':
    main()
