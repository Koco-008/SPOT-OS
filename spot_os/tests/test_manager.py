import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from spot_os.common import atomic
from spot_os.manager import Manager, identity
from spot_os.model import Evidence, State


@pytest.fixture
def manager(tmp_path, monkeypatch):
    import spot_os.manager as module
    monkeypatch.setattr(module,'private_directory',lambda path:Path(path))
    m = Manager(tmp_path/'prefix',tmp_path/'runtime')
    m.runtime.mkdir()
    m.prefix.mkdir()
    atomic(m.prefix/'current.json',dict(version='0.28.10'))
    m.commands=[]
    m.splash_command=m.commands.append
    m.started_at=0
    m.milestones['splash_presented']=1
    return m


def test_before_update_helper_takes_lock_old_gui_must_remain_covered(manager,monkeypatch):
    m=manager
    monkeypatch.setattr('spot_os.manager.locked',lambda p:p.name=='app.lock')
    m.policy.state=State.UPDATING
    m.policy.started=0
    m.gui='old'
    m.clients['old']=dict(pid=123)
    m.update_origin=123
    m.last_evidence=5
    m.evidence=Evidence(True,True,True,True,True,True,True,True,(800,480),True,
                        screen=(800,480),fullscreen=True)
    m.covered=True
    m.step(5.1)
    assert not any(s.startswith('reveal') for s in m.commands)
    assert m.policy.state==State.UPDATING
    m.clients['old']['pid']=124 # Signed Handoff has started a distinct, authenticated app.
    m.step(5.2)
    assert m.commands[-1].startswith('reveal')


def test_update_owned_locks_never_launch_second_app(manager,monkeypatch):
    m=manager
    monkeypatch.setattr('spot_os.manager.locked',lambda _:True)
    calls=[]
    m.launch=lambda now:calls.append(now)
    m.step(10)
    assert not calls
    assert m.policy.state==State.UPDATING


def test_cover_not_acknowledged_by_request_or_timer(manager):
    m=manager
    m.cover_requested=False
    m.cover()
    assert m.covered is False
    first=m.cover_generation
    m.cover()
    assert m.cover_generation==first


def test_restart_rechecks_activity_after_presented_cover(manager,monkeypatch):
    m=manager
    monkeypatch.setattr('spot_os.manager.locked',lambda p:p.name=='app.lock')
    m.policy.state=State.RESTARTING
    m.restart_after_cover=True
    m.covered=True
    m.milestones['last_cover_presented']=10
    m.last_evidence=11
    m.busy=True
    m.step(11.1)
    assert m.policy.state==State.RUNNING
    assert m.policy.reason=='restart_cancelled_busy'
    assert m.commands[-1].startswith('reveal')
    assert m.restart_sent is None


def test_forged_app_identity_fails_closed(tmp_path):
    prefix=tmp_path/'prefix'
    prefix.mkdir()
    atomic(prefix/'current.json',dict(version='0.28.10'))
    assert not identity(123,prefix,'0.28.11',proc=tmp_path/'proc')
    p=tmp_path/'proc/123'
    p.mkdir(parents=True)
    (p/'cmdline').write_bytes(b'python\0/tmp/other.py\0')
    assert not identity(123,prefix,'0.28.10',proc=tmp_path/'proc')


def test_no_readiness_nonce_or_signature_files_changed_by_manager(manager):
    assert manager.status()['app']=='0.28.10'
    assert list(manager.prefix.iterdir())==[manager.prefix/'current.json']
