"""Independent boot health. Failure leaves the presented recovery session alive."""
import time
from pathlib import Path
from .common import atomic, read


def check(config, request, output, *, timeout=110, stable=10, clock=time.monotonic, pause=time.sleep):
    deadline = clock()+timeout
    healthy_since = None
    reason = 'manager_unreachable'
    state = None
    while clock() < deadline:
        try:
            status = request(config, 'status')
            state = status.get('state')
            verified = (state == 'running' and status.get('gui') is True
                and bool(status.get('milestones', {}).get('splash_presented'))
                and bool(status.get('milestones', {}).get('app_revealed')))
            reason = status.get('reason') or 'gui_not_ready'
            if verified:
                healthy_since = clock() if healthy_since is None else healthy_since
                if clock()-healthy_since >= stable:
                    result = dict(boot_verified=True, state=state, reason='ready', utc=time.time(),
                                  session_invocation=status.get('session_invocation'))
                    atomic(output, result)
                    return result
            else:
                healthy_since = None
            if state in ('recovery', 'failed'):
                break
        except (OSError, ValueError, RuntimeError, KeyError):
            healthy_since = None
            reason = 'manager_unreachable'
        pause(.5)
    result = dict(boot_verified=False, state=state, reason=reason,
                  recovery_action='graphical_recovery_if_available_else_SSH_rollback', utc=time.time())
    atomic(output, result)
    return result


def prepare(system):
    # No TTY/PAM descriptors on this root oneshot. Never revoke an existing login.
    from .supervisor import session_info
    sessions = system.command('/usr/bin/loginctl', 'list-sessions', '--no-legend', '--no-pager')
    for line in sessions.splitlines():
        info = session_info(line.split()[0])
        if info.get('VTNr') == '1':
            raise RuntimeError('tty1 still belongs to a logind session; inspect doctor, do not steal it')
    system.command('/usr/bin/chvt', '1')
    return dict(vt_prepared=1)
