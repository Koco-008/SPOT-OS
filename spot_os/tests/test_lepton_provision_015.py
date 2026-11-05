"""First-install fault matrix. No real package manager, camera or Pi is touched."""
from contextlib import nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import zipfile
import pytest

from spot_os.system import System
from spot_os.installer import Journal, STATE
from spot_os import lepton_provision as p, lepton_source as src

IDENTITY = dict(model='Raspberry Pi 5 Model B', arch='aarch64', os13=True, kernel=p.KERNELS[0])


class Target(System):
    def __init__(self, root):
        super().__init__(root)
        self.calls = []
        self.answers = {}
        self.fail = None
    def command(self, *args, **kwargs):
        self.calls.append(args)
        if self.fail and self.fail in args:
            raise RuntimeError('injected failure')
        return self.answers.get(args, '')
    def maintenance_guard(self, prefix):
        return nullcontext()


def put(system, name, data=b'x'):
    path = system.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def observe(system, *, cci=None, ready=True, identity=None):
    return p.probe(system, 'admin', identity=identity or IDENTITY,
        compatibility=lambda *_: cci or dict(compatible=False),
        format_probe=lambda: dict(format_ready=ready))


def test_missing_is_read_only_and_has_header_diagnosis(tmp_path):
    target = Target(tmp_path)
    assert observe(target)['state'] == 'missing'
    assert observe(target)['headers_exact'] is False
    assert not list(tmp_path.iterdir())
    assert all(any(x in args for x in ('-n', 'cat', 'passwd')) for args in target.calls)


@pytest.mark.parametrize('field,value,reason', [('model','Pi 4','unsupported_hardware'),
    ('arch','x86_64','unsupported_hardware'), ('os13',False,'unsupported_hardware'),
    ('kernel','6.12.1+rpt-rpi-2712','unsupported_kernel'), ('kernel','6.18.51+rpt-rpi-2712','unsupported_kernel')])
def test_unsupported_platform_never_mutates(tmp_path,field,value,reason):
    target=Target(tmp_path)
    result=observe(target,identity=dict(IDENTITY, **{field:value}))
    assert result['state']=='unsupported' and result['reason']==reason
    assert target.calls==[] and not list(tmp_path.iterdir())


def test_existing_compatible_is_exact_noop_even_without_install_privileges(tmp_path):
    target=Target(tmp_path)
    sentinel=put(target,p.BINARY,b'working streamer')
    before=sentinel.stat()
    observed=lambda *_:observe(target,cci=dict(compatible=True))
    result=p.Provisioner(target,observer=observed,downloader=lambda:pytest.fail('network'))
    assert result.run('admin')['unchanged']
    assert result.run('admin')['unchanged']
    after=sentinel.stat()
    assert (before.st_ino,before.st_mtime_ns,before.st_mode)==(after.st_ino,after.st_mtime_ns,after.st_mode)
    assert target.calls==[] and not target.path(STATE).exists()


@pytest.mark.parametrize('marker',[p.BINARY,p.SERVICE,p.LOCK,'/dev/video10','/dev/lepton-vspi',
    '/usr/src/lepton-1.0.0/Makefile','/sys/module/lepton/x','/sys/module/v4l2loopback/x'])
def test_foreign_installation_is_never_considered_missing(tmp_path,marker):
    target=Target(tmp_path);put(target,marker)
    assert observe(target)['state']=='incompatible'
    with pytest.raises(p.ProvisionError,match='existing_driver_refused'):
        # _run avoids POSIX lock/account plumbing, but uses the real refusal gate.
        p.Provisioner(target)._run('admin',observe(target),False)


def test_incomplete_own_record_or_failed_producer(tmp_path):
    target=Target(tmp_path)
    put(target,p.RECORD,json.dumps(dict(commit=p.REVIEWED_COMMIT)).encode())
    assert observe(target)['state']=='incomplete'
    assert observe(target,cci=dict(compatible=True),ready=False)['state']=='incomplete'


def test_exact_headers_require_release_and_symvers(tmp_path):
    target=Target(tmp_path);base='/lib/modules/'+p.KERNELS[0]+'/build/'
    put(target,base+'Makefile');put(target,base+'include/config/kernel.release',p.KERNELS[0].encode())
    assert not p.headers_exact(target,p.KERNELS[0])
    put(target,base+'Module.symvers')
    assert p.headers_exact(target,p.KERNELS[0])
    put(target,base+'include/config/kernel.release',b'wrong-kernel')
    assert not p.headers_exact(target,p.KERNELS[0])


@pytest.mark.skipif(os.name!='posix',reason='Root build must reject writable header Makefiles')
def test_writable_or_redirected_headers_cannot_execute_as_root(tmp_path):
    target=Target(tmp_path);base='/lib/modules/'+p.KERNELS[0]+'/build/'
    makefile=put(target,base+'Makefile');put(target,base+'Module.symvers')
    put(target,base+'include/config/kernel.release',p.KERNELS[0].encode())
    assert p.headers_exact(target,p.KERNELS[0])
    makefile.chmod(0o666)
    assert not p.headers_exact(target,p.KERNELS[0])


@pytest.mark.parametrize('text',['Remv useful-package [1.0]', 'Inst gcc [1.0] (2.0 Debian)',
    'Inst linux-image-6.18.50+rpt-rpi-2712 (1 Debian)', 'Inst raspi-firmware (1 Debian)',
    'Inst rpi-eeprom (1 Debian)', 'Inst raspberrypi-kernel (1 Debian)'])
def test_package_plan_rejects_implicit_upgrades_and_removals(text):
    with pytest.raises(p.ProvisionError):p.package_plan(text)


def test_exact_header_dependency_plan_is_allowed():
    assert p.package_plan('Inst linux-headers-'+p.KERNELS[0]+' (1 Debian)')


def archive_bytes():
    stream=io.BytesIO();prefix='Lepton-Pi5-'+p.REVIEWED_COMMIT+'/'
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr(prefix+'lepton_vospi_lib/lepton_vospi_funcs.c',b'checked source')
        link=zipfile.ZipInfo(prefix+'lepton_module/lepton_vospi_funcs.c')
        link.external_attr=0o120777<<16
        z.writestr(link,b'../lepton_vospi_lib/lepton_vospi_funcs.c')
    return stream.getvalue()


def test_archive_integrity_checked_before_any_extraction(tmp_path):
    with pytest.raises(src.SourceError,match='integrity'):src.extract(b'tampered',tmp_path/'source')
    assert not list(tmp_path.iterdir())


def test_verified_upstream_links_are_materialised_without_symlinks(tmp_path,monkeypatch):
    raw=archive_bytes();monkeypatch.setattr(src,'SHA256',hashlib.sha256(raw).hexdigest())
    folder=src.extract(raw,tmp_path/'source')
    path=folder/'lepton_module/lepton_vospi_funcs.c'
    assert path.read_bytes()==b'checked source' and not path.is_symlink()


@pytest.mark.skipif(os.name!='posix',reason='Real POSIX locks, owners and symlink attack checks')
def test_parallel_admin_operation_and_hostile_paths(tmp_path):
    target=Target(tmp_path)
    with p.admin_guard(target):
        with pytest.raises(p.ProvisionError,match='operation_busy'):
            with p.admin_guard(target):pytest.fail('second installer entered')
    outside=tmp_path/'outside';outside.mkdir()
    target.path('/etc').symlink_to(outside,target_is_directory=True)
    with pytest.raises(p.ProvisionError,match='unsafe_path'):p.secure_path(target,p.RECORD)


@pytest.mark.skipif(os.name!='posix',reason='Unix modes')
def test_group_writable_root_state_is_refused(tmp_path):
    target=Target(tmp_path);path=target.path(STATE);path.mkdir(parents=True);path.chmod(0o777)
    with pytest.raises(p.ProvisionError,match='unsafe_permissions'):
        with p.admin_guard(target):pytest.fail('unsafe state accepted')


class InstallerDouble(p.Provisioner):
    """Only external build effects are simulated; journal, apt gates and flow are real."""
    def build(self, source, kernel):
        if self.system.fail=='compile':raise RuntimeError('compile failed')
    def configure(self, source, user, kernel):
        self.write(p.BINARY,b'new-owned-binary',0o755)
        self.write('/boot/firmware/config.txt',b'owned-overlay')
        if self.system.fail=='systemd':raise RuntimeError('systemd setup failed')


@pytest.fixture
def fresh(tmp_path,monkeypatch):
    target=Target(tmp_path)
    put(target,'/boot/firmware/config.txt',b'original boot config')
    put(target,'/proc/sys/kernel/random/boot_id',b'boot-one')
    monkeypatch.setattr(p,'headers_exact',lambda *_:True)
    raw=archive_bytes();monkeypatch.setattr(src,'SHA256',hashlib.sha256(raw).hexdigest())
    return target, raw, dict(state='missing',kernel=p.KERNELS[0])


@pytest.mark.parametrize('failure,phase',[('network','failed'),('integrity','failed'),('compile','failed'),('systemd','failed')])
def test_failure_is_journalled_without_repair_loop(fresh,failure,phase):
    target,raw,initial=fresh
    def get():
        if failure=='network':raise src.SourceError('source_download_failed')
        return b'tampered' if failure=='integrity' else raw
    target.fail=failure
    worker=InstallerDouble(target,downloader=get)
    with pytest.raises(RuntimeError):worker._run('admin',initial,False)
    journal=Journal(target,p.JOURNAL)
    assert journal.data['phase']==phase and journal.data['package_transactional'] is False
    assert journal.data['source_commit']==p.REVIEWED_COMMIT
    assert not any('reboot' in args or 'restart' in args for args in target.calls)


def test_missing_header_fails_before_download_or_build(fresh,monkeypatch):
    target,raw,initial=fresh;monkeypatch.setattr(p,'headers_exact',lambda *_:False)
    worker=InstallerDouble(target,downloader=lambda:pytest.fail('must not download'))
    with pytest.raises(p.ProvisionError,match='matching_headers'):worker._run('admin',initial,False)
    assert Journal(target,p.JOURNAL).data['phase']=='failed'


def test_first_setup_stages_once_and_requires_manual_reboot(fresh):
    target,raw,initial=fresh;worker=InstallerDouble(target,downloader=lambda:raw)
    result=worker._run('admin',initial,False)
    assert result['pending'] and result['reboot_required'] and not result['reboot_performed']
    assert Journal(target,p.JOURNAL).data['phase']=='awaiting-reboot'
    assert target.path(p.BINARY).read_bytes()==b'new-owned-binary'
    target.calls.clear()
    assert worker._run('admin',dict(initial,state='incomplete'),True)['pending']
    assert target.calls==[]


def test_interrupted_partial_setup_requires_explicit_recovery(fresh):
    target,raw,initial=fresh;target.fail='systemd';worker=InstallerDouble(target,downloader=lambda:raw)
    with pytest.raises(RuntimeError):worker._run('admin',initial,False)
    with pytest.raises(p.ProvisionError,match='existing_driver_refused'):worker._run('admin',dict(initial,state='incomplete'),False)
    with pytest.raises(p.ProvisionError,match='requires_rollback'):worker._run('admin',dict(initial,state='incomplete'),True)


def test_resume_refuses_changed_files_or_kernel(fresh):
    target,raw,initial=fresh;worker=InstallerDouble(target,downloader=lambda:raw)
    worker._run('admin',initial,False)
    with pytest.raises(p.ProvisionError,match='kernel_changed'):
        worker._run('admin',dict(initial,state='incomplete',kernel='other'),True)
    target.path(p.BINARY).write_bytes(b'foreign change')
    with pytest.raises(p.ProvisionError,match='owned_files_changed'):
        worker._run('admin',dict(initial,state='incomplete'),True)


def test_normal_ota_has_no_provisioning_dependency():
    root=Path(p.__file__).parent
    for name in ('update_backend.py','update_service.py','update_engine.py','session.py','manager.py','camera_ready.py'):
        text=(root/name).read_text()
        assert 'lepton_provision' not in text and 'provision-lepton' not in text


def test_production_pin_and_dkms_are_distinct_from_historic_installer():
    assert p.REVIEWED_COMMIT=='20be84ea89c030822159ba70934bb66c3a1d45e6'
    assert src.SHA256=='388926f3b55422a645c289ed3cfee954ff19a2a588d48a8ce48dbe1dd1ef7cbf'
    assert 'f031edf' not in src.URL


def test_apt_failure_is_bounded_and_never_reaches_source(fresh):
    target,raw,initial=fresh;target.fail='update'
    worker=InstallerDouble(target,downloader=lambda:pytest.fail('download after failed apt'))
    with pytest.raises(p.ProvisionError,match='packages_failed'):
        worker._run('admin',initial,False)
    assert len(target.calls)==1


@pytest.mark.skipif(os.name!='posix',reason='Actual journal restoration with Unix locks')
def test_explicit_partial_setup_rollback_restores_boot_config(fresh):
    target,raw,initial=fresh;worker=InstallerDouble(target,downloader=lambda:raw)
    target.fail='systemd'
    with pytest.raises(p.ProvisionError):worker._run('admin',initial,False)
    target.fail=None
    result=worker.rollback()
    assert result['controlled_files_restored'] and result['packages_and_accounts_retained']
    assert target.path('/boot/firmware/config.txt').read_bytes()==b'original boot config'
    assert not target.path(p.BINARY).exists()


@pytest.mark.skipif(os.name!='posix',reason='Unix enable symlink and mode preservation')
def test_real_configure_journals_all_destinations_without_start_or_restart(fresh,tmp_path):
    target,raw,initial=fresh;source=tmp_path/'compiled';source.mkdir()
    for name in ('lepton_streamer/lepton_streamer','lepton_control/rpi_vsync_app',
        'lepton_control/rpi_recovery_app','tools/recover_lepton_rpi5.sh',
        'systemd/lepton-streamer.service','lepton_module/flir-lepton-rpi5.dtbo'):
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'compiled test fixture')
    (source/'lepton_sdk').mkdir()
    worker=p.Provisioner(target);worker.journal=Journal(target,p.JOURNAL)
    worker.configure(source,'admin',p.KERNELS[0])
    names={x['path'] for x in worker.journal.data['entries']}
    assert p.BINARY in names and p.SERVICE in names and '/boot/firmware/config.txt' in names
    assert target.path('/etc/systemd/system/multi-user.target.wants/lepton-streamer.service').is_symlink()
    assert b'0755 root root' in target.path('/etc/tmpfiles.d/spot-lepton.conf').read_bytes()
    assert b'flock -w 15 '+p.LOCK.encode() in target.path(p.SERVICE+'.d/zzzz-spot-cci-lock.conf').read_bytes()
    assert b'Restart=no' in target.path(p.SERVICE+'.d/zzzz-spot-cci-lock.conf').read_bytes()
    assert not any(x in args for args in target.calls for x in ('start','stop','restart','enable','modprobe','reboot'))
    worker.verify_owned()


def test_real_builder_refuses_kbuild_release_before_dkms(fresh,tmp_path):
    target,raw,initial=fresh;worker=p.Provisioner(target);worker.journal=Journal(target,p.JOURNAL)
    with pytest.raises(p.ProvisionError,match='kbuild_release_mismatch'):
        worker.build(tmp_path,p.KERNELS[0])
    assert len(target.calls)==1 and 'kernelrelease' in target.calls[0]
