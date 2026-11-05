"""Fixed reviewed upstream source; no downloaded installer is executed.

Upstream sources and SDK are downloaded on explicit device setup, never bundled
in the OS release. The archive and every extracted path are checked before make.
"""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import urllib.request
import zipfile

from .lepton import REVIEWED_COMMIT

URL = 'https://codeload.github.com/Koco-008/Lepton-Pi5/zip/' + REVIEWED_COMMIT
SHA256 = '388926f3b55422a645c289ed3cfee954ff19a2a588d48a8ce48dbe1dd1ef7cbf'
LIMIT = 32 * 1024**2


class SourceError(RuntimeError):
    pass


def download():
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, url):
            raise SourceError('source_redirect_refused')
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect())
        request = urllib.request.Request(URL, headers={'User-Agent': 'SPOT-OS-Lepton-setup'})
        with opener.open(request, timeout=45) as response:
            data = response.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise SourceError('source_too_large')
        return data
    except OSError as exc:
        raise SourceError('source_download_failed: check network and retry explicit setup') from exc


def extract(data, destination):
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SourceError('source_integrity_failed')
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise SourceError('source_destination_exists')
    prefix = 'Lepton-Pi5-' + REVIEWED_COMMIT
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.infolist()) > 300 or sum(i.file_size for i in archive.infolist()) > 64 * 1024**2:
            raise SourceError('source_limits')
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if (not path.parts or path.parts[0] != prefix or '..' in path.parts
                    or '\\' in item.filename or path.is_absolute()):
                raise SourceError('source_path')
            if item.is_dir():
                continue
            name = PurePosixPath(*path.parts[1:]).as_posix()
            if name in files:
                raise SourceError('source_duplicate')
            files[name] = (archive.read(item), item.external_attr >> 16)
    # Upstream's four relative VoSPI links are materialised as ordinary files.
    # No source symlink is ever created, followed or handed to the root builder.
    resolved = {}
    for name, (raw, mode) in files.items():
        if stat.S_ISLNK(mode):
            expected = '../lepton_vospi_lib/' + PurePosixPath(name).name
            if (PurePosixPath(name).parent.as_posix() not in ('lepton_module', 'lepton_data_collector')
                    or raw.decode() != expected):
                raise SourceError('source_symlink')
            target = 'lepton_vospi_lib/' + PurePosixPath(name).name
            raw, mode = files[target]
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
            raise SourceError('source_special_file')
        resolved[name] = raw
    destination.mkdir(mode=0o700)
    for name, raw in resolved.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    return destination
