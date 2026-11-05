"""Pure startup fault simulations plus the real read-only ioctl buffer contract."""
import ctypes
import os
from pathlib import Path
import struct
import sys
import subprocess
import importlib.util
from types import SimpleNamespace
import pytest
from spot_os import camera_ready as camera
from spot_os import REVISION
from spot_os.common import read
from spot_os.installer import Installer
from test_installer import system
from test_boot_012 import snapshot

GOOD = dict(width=160,height=120,fourcc='Y16 ',bytesperline=320,sizeimage=38400)
BAD = dict(width=640,height=480,fourcc='BGR3',bytesperline=1920,sizeimage=921600)


def simulate(observations, *, owned=True, units=None, timeout=1):
    ticks=[0.]
    calls=[]
    info=dict(ActiveState='active',SubState='running',MainPID='100',InvocationID='one',
              ExecMainStartTimestampMonotonic='1')
    class System:
        def command(self,*args,**kwargs):
            calls.append(args)
            value=(units(ticks[0]) if units else info)
            return '\n'.join(k+'='+v for k,v in value.items())
    result=camera.wait_for_format(System(),timeout=timeout,interval=.1,
        format_reader=lambda _:observations(ticks[0]),owner=lambda *_:owned,
        clock=lambda:ticks[0],pause=lambda n:ticks.__setitem__(0,ticks[0]+n))
    assert all(call[:2]==('/usr/bin/systemctl','show') for call in calls)
    return result


def test_cold_boot_waits_for_format_after_simple_service_is_active():
    result=simulate(lambda now:BAD if now<.5 else GOOD)
    assert result['format_ready'] and result['elapsed']>=.6


def test_session_restart_rechecks_even_when_streamer_remains_active():
    assert simulate(lambda _:GOOD)['format_ready']
    result=simulate(lambda now:BAD if now<.3 else GOOD)
    assert result['format_ready'] and result['elapsed']>=.4


@pytest.mark.parametrize('fmt',[BAD,dict(GOOD,fourcc='Y16\0'),dict(GOOD,width=640),
    dict(GOOD,bytesperline=0),dict(GOOD,sizeimage=0)])
def test_wrong_or_incomplete_format_times_out_to_gui(fmt):
    result=simulate(lambda _:fmt)
    assert not result['format_ready'] and result['gui_fallback'] and result['elapsed']<=1


def test_retained_y16_without_open_producer_is_not_ready():
    result=simulate(lambda _:GOOD,owned=False)
    assert not result['format_ready'] and result['reason']=='producer_not_open'


def test_failed_streamer_releases_gui_without_retrying_service():
    result=simulate(lambda _:GOOD,units=lambda _:dict(ActiveState='failed',MainPID='0'))
    assert not result['format_ready'] and result['reason']=='streamer_failed' and result['checks']==1


def test_service_invocation_change_resets_two_sample_confirmation():
    def service(now):
        return dict(ActiveState='active',SubState='running',MainPID='100',
                    InvocationID=str(round(now,2)),ExecMainStartTimestampMonotonic='1')
    assert not simulate(lambda _:GOOD,units=service)['format_ready']


@pytest.mark.parametrize('error',[PermissionError(),FileNotFoundError(),OSError(25,'not V4L2')])
def test_probe_failure_does_not_hold_gui_forever(error):
    def reader(_):raise error
    result=simulate(reader)
    assert not result['format_ready'] and result['gui_fallback'] and result['elapsed']<=1


@pytest.mark.skipif(os.name!='posix',reason='Linux open flags and ioctl ABI')
def test_get_format_only_uses_read_only_open_and_g_fmt(monkeypatch):
    calls=[]
    def opened(path,flags):
        calls.append(('open',path,flags));return 42
    def ioctl(fd,request,buffer,mutate):
        calls.append(('ioctl',request))
        assert len(buffer)==ctypes.sizeof(camera.Format)
        assert int.from_bytes(buffer[:4],'little')==1
        struct.pack_into('=6I',buffer,camera.Format.fmt.offset,160,120,
            int.from_bytes(b'Y16 ','little'),1,320,38400)
    monkeypatch.setattr(os,'open',opened)
    monkeypatch.setattr(os,'fstat',lambda _:SimpleNamespace(st_mode=0o020660))
    monkeypatch.setattr(os,'close',lambda _:calls.append(('close',)))
    import fcntl
    monkeypatch.setattr(fcntl,'ioctl',ioctl)
    assert camera.read_format('/dev/video10')==GOOD
    assert calls[0][2] & os.O_ACCMODE == os.O_RDONLY
    assert len([c for c in calls if c[0]=='ioctl'])==1
    assert calls[1][1] & 0xffff == 0x5604  # G_FMT, never S_FMT (0x5605).


def test_weak_ordering_and_repeatable_bounded_root_gate():
    root=Path(__file__).resolve().parents[1]/'config'
    session=(root/'spot-os-session.service').read_text()
    gate=(root/'spot-os-lepton-ready.service').read_text()
    after=next(line for line in session.splitlines() if line.startswith('After=')).split()
    assert 'lepton-streamer.service' in after and 'spot-os-lepton-ready.service' in after
    assert 'Requires=spot-os-prepare.service\n' in session
    assert 'Requires=lepton' not in session and 'Requires=spot-os-lepton-ready' not in session
    assert 'RemainAfterExit=' not in gate and 'PartOf=' not in gate
    assert 'TimeoutStartSec=14' in gate and 'TimeoutStopSec=1' in gate and 'Restart=no' in gate
    assert 'PAMName=' not in gate and 'TTYPath=' not in gate


@pytest.mark.skipif(os.name!='posix',reason='Linux videodev2 ABI and C compiler')
def test_ioctl_layout_matches_system_uapi_header(tmp_path):
    code=tmp_path/'layout.c'
    code.write_text('#include <stdio.h>\n#include <stddef.h>\n#include <time.h>\n#include <linux/videodev2.h>\n'
        'int main(void){printf("%zu %zu %lu",sizeof(struct v4l2_format),'
        'offsetof(struct v4l2_format,fmt),(unsigned long)VIDIOC_G_FMT);return 0;}\n')
    executable=tmp_path/'layout'
    subprocess.run(['cc',str(code),'-o',str(executable)],check=True,capture_output=True)
    size,offset,request=map(int,subprocess.check_output([str(executable)]).split())
    assert size==ctypes.sizeof(camera.Format) and offset==camera.Format.fmt.offset
    assert request==(3<<30)|(size<<16)|(ord('V')<<8)|4


@pytest.fixture(scope='module')
def previous_candidate(tmp_path_factory):
    root=Path(__file__).resolve().parents[2]
    folder=tmp_path_factory.mktemp('actual-os-012-before-ordering')
    git=['git','-c','safe.directory='+root.as_posix()]
    base='a9a613ee04daf757ee5079b89f757fb90ed18357'
    names=subprocess.check_output(git+['ls-tree','-r','--name-only',base,'spot_os'],cwd=root,text=True).splitlines()
    for name in names:
        path=folder/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(subprocess.check_output(git+['show',base+':'+name],cwd=root))
    spec=importlib.util.spec_from_file_location('spot_os._candidate_before_ordering',folder/'spot_os/installer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.VERSION='0.1.2';module.INSTALL='/usr/local/lib/spot-os/0.1.2'
    return module.Installer,folder/'spot_os'


@pytest.mark.skipif(os.name!='posix',reason='Exact candidate filesystem upgrade and rollback')
@pytest.mark.parametrize('failure',[False,True])
def test_already_installed_012_candidate_gets_gate_or_exact_rollback(system,previous_candidate,failure):
    cls,source=previous_candidate
    old=cls(system,source);old.install('admin');old.activate('admin')
    before=snapshot(system)
    with pytest.raises(RuntimeError):Installer(system).install('admin')
    if failure:
        system.fail='make'
        with pytest.raises(RuntimeError):Installer(system).upgrade('admin')
        assert snapshot(system)==before
    else:
        new=Installer(system)
        assert new.upgrade('admin')['active'] and new.check()['ok']
        assert read(system.path('/etc/spot-os/config.json'))['os_revision']==REVISION
        assert system.path('/etc/systemd/system/spot-os-lepton-ready.service').is_file()
        assert new.upgrade('admin')['unchanged']
        assert new.rollback_upgrade()['version']=='0.1.2'
        assert snapshot(system)==before
