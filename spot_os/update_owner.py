"""One cross-process OTA owner, including session startup and health verification.

Never unlink this inode. Service restart and administrative recovery use the
same lock. The installer lock remains separate and is released before launch.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat


class OwnerBusy(RuntimeError):
    pass


@contextmanager
def owner(root=Path('/var/lib/spot-os/updates')):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root/'owner.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink()
                or (os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077))):
            raise PermissionError('Untrusted OTA owner lock')
        try:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                if not info.st_size: os.write(fd, b'0')
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise OwnerBusy('Eine andere OS-Transaktion besitzt die Wiederherstellung') from exc
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise PermissionError('OTA owner lock inode changed')
        yield
    finally:
        os.close(fd)
