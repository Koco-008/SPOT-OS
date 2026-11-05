import os
from pathlib import Path
import pytest
from spot_os.update_backend import Backend
from test_installer import system
from spot_os.common import atomic
from test_os_updates_014 import engine,signed


def backend(system):
    atomic(system.path('/etc/spot-os/config.json'),dict(user='admin',uid=1000,prefix='/home/admin/.local/share/spot'))
    b=Backend(system);b.progress=lambda *args:None
    return b


def test_consumed_finite_window_gets_one_retry_without_reset(system,monkeypatch):
    b=backend(system);unit=system.path('/etc/systemd/system/spot-os-session.service')
    unit.parent.mkdir(parents=True,exist_ok=True);unit.write_text('StartLimitIntervalSec=120\n')
    calls=[];sleeps=[]
    def command(*args,**kwargs):
        calls.append(args)
        if 'start' in args and sum('start' in c for c in calls)==1:raise RuntimeError('start denied')
        return 'start-limit-hit'
    system.command=command;monkeypatch.setattr('spot_os.update_backend.time.sleep',sleeps.append)
    b.start()
    assert sleeps==[122] and sum('start' in c for c in calls)==2
    assert not any('reset-failed' in c for c in calls)


def test_real_start_failure_is_not_hidden_or_retried(system):
    b=backend(system)
    def command(*args,**kwargs):
        if 'start' in args:raise RuntimeError('notify failure')
        return 'exit-code'
    system.command=command
    with pytest.raises(RuntimeError):b.start()


def test_new_updater_is_activated_without_waiting_for_itself(system):
    b=backend(system);b.activate_service()
    assert b.handover_requested is True and system.calls==[]


@pytest.mark.skipif(os.name!='posix',reason='Real Unix installer/symlink journal')
def test_recovery_never_restores_previous_bootstrap_journal_for_new_attempt(system):
    from spot_os.installer import Installer
    Installer(system,target=('0.1.4','signed-os-updates-1')).install('admin')
    b=Backend(system)
    from contextlib import nullcontext
    b.maintenance=lambda:nullcontext(system)
    atomic(system.path('/var/lib/spot-os/upgrade.json'),dict(schema=1,phase='committed',
        from_version='0.1.3',to_version='0.1.4',entries=[]))
    # The 0.1.5 attempt failed BEFORE its installer created a journal.
    b.rollback('0.1.5','0.1.4')
    assert __import__('json').loads(system.path('/etc/spot-os/config.json').read_text())['os_version']=='0.1.4'


@pytest.mark.parametrize('active',[False,True])
def test_explicit_admin_recovery_is_bounded_and_requires_stopped_updater(system,engine,active):
    from spot_os.update_service import manual_recover
    engine.backend.system=system
    original=system.command
    system.command=lambda *args,**kwargs:('active' if active else 'inactive') if '--value' in args else original(*args,**kwargs)
    engine.save('recovery-failed','not confirmed',previous='0.1.4',version='0.1.5',rollback_attempts=2)
    if active:
        with pytest.raises(RuntimeError,match='Stop'):manual_recover(engine.backend,engine.root)
        assert engine.backend.events==[]
    else:
        assert manual_recover(engine.backend,engine.root)['ok']
        assert engine.backend.events.count('rollback')==1
        assert not (engine.root/'session-blocked').exists()
        with pytest.raises(RuntimeError,match='No failed'):manual_recover(engine.backend,engine.root)


def test_online_preflight_only_waives_ssh_connection():
    from test_installer import device
    from spot_os.system import validate
    data=device();data['ssh_connection']=False
    assert validate(data,online=True)['ok']
    assert not validate(data,online=False)['ok']
    for key,value in [('ssh','inactive'),('lepton','inactive'),('app_locked',True),('update_locked',True)]:
        assert not validate(dict(data,**{key:value}),online=True)['ok']
