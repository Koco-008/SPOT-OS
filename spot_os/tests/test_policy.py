from dataclasses import replace
import pytest
from spot_os.model import Evidence, Policy, State


def ready(camera=True):
    return Evidence(True, True, True, True, True, True, True, True, (800, 480), camera,
                    position=(0,0),screen=(800,480),fullscreen=True)


def test_normal_and_delayed_gui():
    p = Policy()
    assert p.start(10)
    assert p.observe(11, Evidence()) == 'cover'
    assert p.observe(16, ready()) == 'reveal'
    assert p.state == State.RUNNING


@pytest.mark.parametrize('missing', ['authenticated', 'release', 'wayland', 'mapped', 'painted',
                                    'responsive', 'enabled', 'initialized', 'size'])
def test_visible_window_is_insufficient(missing):
    p = Policy()
    p.start(0)
    value = (640, 480) if missing == 'size' else False
    assert p.observe(10, replace(ready(), **{missing: value})) == 'cover'


def test_camera_fault_opens_real_gui_after_grace_without_faking_camera():
    p = Policy(camera_grace=12)
    p.start(0)
    assert p.observe(3, ready(False)) == 'cover'
    assert p.observe(14, ready(False)) == 'cover'
    assert p.observe(15, ready(False)) == 'reveal'
    assert p.reason == 'camera_unavailable'
    assert p.camera_available is False


def test_update_lock_and_pending_never_reveal_candidate():
    p = Policy()
    p.start(0)
    assert p.observe(20, ready(), update_locked=True) == 'cover'
    assert p.exited(22, update_locked=True) == 'defer'
    assert p.state == State.UPDATING


def test_retry_limit_and_timeout():
    p = Policy(startup_timeout=5)
    p.start(0)
    p.observe(6, Evidence())
    assert p.state == State.RECOVERY
    assert p.exited(7, False) == 'retry'
    assert p.next_attempt == 9
    p.start(9)
    p.exited(10, False)
    assert p.next_attempt == 14
    p.start(14)
    assert p.exited(15, False) == 'recovery'
    assert not p.start(16)
