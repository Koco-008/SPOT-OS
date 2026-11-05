"""One-time administrative trust provisioning; never exposed through IPC."""
import hashlib
import os
from pathlib import Path
import re
from .common import atomic
from .update_contract import REPOSITORY


def provision(key_file,expected,directory=Path('/etc/spot-os')):
    if not re.fullmatch('[a-f0-9]{64}',expected):raise ValueError('Independently verified OS key SHA256 required')
    fd=os.open(key_file,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        import stat
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>4096:raise ValueError('OS key must be a small regular file')
        key=stream.read(4097)
    if hashlib.sha256(key).hexdigest()!=expected:raise ValueError('Independent OS fingerprint differs')
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(load_pem_public_key(key),Ed25519PublicKey):raise ValueError('OS Ed25519 key required')
    destination=Path(directory)/'os-release.pub'
    anchor=Path(directory)/'os-trust.json'
    if destination.exists() or anchor.exists():
        if destination.is_symlink() or anchor.is_symlink() or destination.read_bytes()!=key:
            raise ValueError('Existing OS key cannot be silently replaced/rotated')
        old=__import__('json').loads(anchor.read_bytes()) if anchor.exists() else None
        if old is not None and old!=dict(schema=1,repository=REPOSITORY,sha256=expected):raise ValueError('Existing OS trust policy differs')
    else:
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        with os.fdopen(fd,'wb') as stream:stream.write(key);stream.flush();os.fsync(stream.fileno())
    atomic(anchor,dict(schema=1,repository=REPOSITORY,sha256=expected),mode=0o644)
    return dict(provisioned=True,sha256=expected,repository=REPOSITORY)
