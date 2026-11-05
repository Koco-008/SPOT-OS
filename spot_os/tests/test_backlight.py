import pytest
from spot_os.backlight import DoubleTap, RestoreGuard
from spot_os.common import atomic, read


def test_doubletap_and_reject_drag_hold_multitouch():
    d = DoubleTap()
    assert not d.contact(True,10,10,0)
    assert not d.contact(False,10,10,.1)
    assert not d.contact(True,10,10,.2)
    assert d.contact(False,10,10,.3)
    for x, duration, contacts in [(100,.1,1), (10,1,1), (10,.1,2)]:
        d = DoubleTap()
        d.contact(True,10,10,0)
        assert not d.contact(False,x,10,duration,contacts)


def test_guard_restores_exact_original_on_timeout_and_failure(tmp_path):
    brightness, record = tmp_path/'brightness', tmp_path/'record.json'
    brightness.write_text('193')
    atomic(record, dict(wake_verified=False))
    guard = RestoreGuard(brightness, 193, 5, record)
    brightness.write_text('0')
    guard.run(now=lambda: 6)
    assert brightness.read_text() == '193'
    assert read(record)['restored']
    atomic(record, dict(wake_verified=False))
    brightness.write_text('0')
    with pytest.raises(OSError):
        guard.run(now=lambda:0, sleep=lambda _: (_ for _ in ()).throw(OSError('app/SSH failed')))
    assert brightness.read_text() == '193'


def test_restore_write_permission_error_surfaces(tmp_path, monkeypatch):
    path = tmp_path/'brightness'
    path.write_text('255')
    guard = RestoreGuard(path,255,0,tmp_path/'record')
    monkeypatch.setattr(type(path), 'write_text', lambda *_: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError): guard.restore()


@pytest.mark.skipif(__import__('os').name!='posix',reason='POSIX private record permissions')
def test_crash_restores_existing_app_record(tmp_path):
    from spot_os.backlight import restore_app_record
    brightness=tmp_path/'brightness'
    brightness.write_text('0')
    brightness.with_name('max_brightness').write_text('255')
    info=brightness.stat()
    record=tmp_path/'display-restore.json'
    atomic(record,dict(name='11-0045',value=201,device=info.st_dev,inode=info.st_ino))
    assert restore_app_record(record,brightness)
    assert brightness.read_text()=='201'
    assert not record.exists()
