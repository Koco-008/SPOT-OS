"""Allowlisted structured milestones, retained per invocation across logout/reboot.

Never dump process environments, protocol payloads, tokens, SSIDs or credentials.
User-writable session diagnostics are evidence, never an authorization source.
"""
import json
import os
from pathlib import Path
import time
from .common import atomic, private_directory, read


def event(name, **fields):
    fields = {k: v for k, v in fields.items() if k in (
        'component', 'pid', 'exit_status', 'terminating_signal', 'recovery_action',
        'reason', 'state', 'wayland', 'leader', 'session', 'seat', 'vt', 'active')}
    item = dict(event=name, monotonic=time.monotonic(), utc=time.time(), **fields)
    print(json.dumps(item, sort_keys=True), flush=True)
    directory = os.environ.get('SPOT_OS_SESSION_LOG')
    if directory:
        folder = private_directory(directory)
        # One atomic milestone file per event; append-only chronological stream too.
        atomic(folder/(name+'.json'), item)
        fd = os.open(folder/'events.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(item, sort_keys=True)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
    return item


def milestones(directory):
    folder = Path(directory)
    return {p.stem: read(p) for p in folder.glob('*.json') if p.name != 'health.json'}
