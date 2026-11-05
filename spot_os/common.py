"""Small, bounded local protocol; no shell commands or user supplied executables."""
import json
import os
from pathlib import Path
import stat
import tempfile

MAX_MESSAGE = 16384


def read(path, default=None):
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            raise ValueError('Oversized state file')
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def atomic(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.chmod(temporary, mode)
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == 'posix':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('OS runtime directory must be private and owned by session user')
    return path


def encode(value):
    result = json.dumps(value, separators=(',', ':')).encode() + b'\n'
    if len(result) > MAX_MESSAGE:
        raise ValueError('Oversized message')
    return result
