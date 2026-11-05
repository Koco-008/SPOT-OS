import os
from pathlib import Path
import pytest
from spot_os.offline_restore import OfflineSystem, restore
from spot_os.installer import Journal


def test_offline_refuses_host_root_and_traversal(tmp_path):
    with pytest.raises(ValueError):restore('/')
    system=OfflineSystem(tmp_path)
    with pytest.raises(ValueError):system.path('/etc/../../outside')


@pytest.mark.skipif(os.name!='posix',reason='Linux mount/symlink semantics')
def test_offline_restore_does_not_execute_target_and_checks_hashes(tmp_path):
    system=OfflineSystem(tmp_path)
    path=system.path('/boot/firmware/cmdline.txt')
    path.parent.mkdir(parents=True)
    path.write_text('original')
    j=Journal(system,'activation')
    j.write('/boot/firmware/cmdline.txt',b'new')
    j.data['phase']='active';j.save()
    assert restore(tmp_path)['system_commands_executed'] is False
    assert path.read_text()=='original'


def test_corrupt_backup_cannot_delete_current_config(tmp_path):
    system=OfflineSystem(tmp_path)
    path=system.path('/boot/firmware/cmdline.txt')
    path.parent.mkdir(parents=True)
    path.write_bytes(b'old')
    journal=Journal(system,'activation')
    journal.write('/boot/firmware/cmdline.txt',b'new')
    digest=journal.data['entries'][0]['blob']
    system.path('/var/lib/spot-os/backups/'+digest).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='hash mismatch'):journal.restore()
    assert path.read_bytes()==b'new'
