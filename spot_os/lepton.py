"""Read-only prerequisite audit; never install/restart/reset the external driver."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
from .common import atomic, read

REVIEWED_COMMIT = '20be84ea89c030822159ba70934bb66c3a1d45e6'
RECORD = '/etc/spot-os/lepton-compat.json'
BINARY = '/usr/local/bin/lepton_streamer'
LOCK = '/run/lock/spot-lepton/cci.lock'
PROTOCOL = 'linux-flock-exclusive-v1'
MARKERS = (LOCK.encode(), b'Lepton CCI flock failed:', b'dropping unverified frame')


def trusted_file(path):
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022


def inspect(system, user):
    result = dict(compatible=False, prerequisite='Lepton-Pi5 PR #3 installed and explicitly attested separately',
        reviewed_commit=REVIEWED_COMMIT, protocol=PROTOCOL, lock=LOCK)
    try:
        path = system.path(RECORD)
        binary = system.path(BINARY)
        if not trusted_file(path) or not trusted_file(binary):
            raise ValueError('Untrusted driver/attestation ownership')
        record = read(path)
        data = binary.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        result.update(driver_commit=record.get('commit'), binary_sha256=digest)
        if (record.get('commit') != REVIEWED_COMMIT or record.get('protocol') != PROTOCOL
            or record.get('binary_sha256') != digest or not all(marker in data for marker in MARKERS)):
            raise ValueError('Driver version/hash/protocol differs from reviewed prerequisite')
        lock = system.path(LOCK)
        info = lock.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o002:
            raise ValueError('CCI lock missing, symlinked or world-writable')
        # No flock is taken: a busy lock during a legitimate CCI operation is OK.
        code = ("import os,stat; p='"+LOCK+"';fd=os.open(p,os.O_RDWR|os.O_NOFOLLOW);"
            "s=os.fstat(fd);assert stat.S_ISREG(s.st_mode);print(str(s.st_dev)+':'+str(s.st_ino));os.close(fd)")
        access = system.command('/usr/sbin/runuser','-u',user,'--','/usr/bin/python3','-c',code,check=False)
        if access != str(info.st_dev)+':'+str(info.st_ino):
            raise ValueError('App cannot open the shared CCI inode read/write')
        pid = int(system.command('/usr/bin/systemctl','show','lepton-streamer.service','-p','MainPID','--value'))
        proc = system.path('/proc/'+str(pid))
        if pid <= 1 or (proc/'exe').resolve() != binary.resolve():
            raise ValueError('Running streamer is not the attested binary')
        if hashlib.sha256((proc/'exe').read_bytes()).hexdigest() != digest:
            raise ValueError('Running streamer executable differs from disk')
        opened = False
        for fd in (proc/'fd').iterdir():
            try:
                current = fd.stat()
                opened |= (current.st_dev,current.st_ino) == (info.st_dev,info.st_ino)
            except FileNotFoundError:
                continue
        if not opened:
            raise ValueError('Streamer has not opened the SAME CCI lock inode; restart separately after driver setup')
        result.update(compatible=True, provenance='explicit administrator build attestation plus live binary/inode checks')
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        result['reason'] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(description='Explicit build attestation, not a driver installer')
    parser.add_argument('--attest-reviewed-build', choices=(REVIEWED_COMMIT,), required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('Root attestation required')
    data = Path(BINARY).read_bytes()
    if not trusted_file(Path(BINARY)) or not all(marker in data for marker in MARKERS):
        raise ValueError('Installed binary lacks required CCI guards')
    # This explicit prerequisite can run BEFORE a fresh OS install. atomic()
    # otherwise creates a private 0700 parent that the session cannot traverse.
    from .runtime import root_readable_directory
    root_readable_directory(Path(RECORD).parent,0o755)
    atomic(RECORD,dict(commit=args.attest_reviewed_build, protocol=PROTOCOL,
        binary_sha256=hashlib.sha256(data).hexdigest()),0o644)


if __name__ == '__main__':
    main()
