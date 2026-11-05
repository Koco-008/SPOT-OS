"""Deterministic private OS ZIP. Not an application update or signed app tag."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from .installer import VERSION
from . import REVISION


def build(output, source=Path(__file__).parent, *, target=None):
    from .assets import verify
    release,revision=target or (VERSION,REVISION)
    source, output = Path(source), Path(output)
    verify(source/'artwork')
    files = {}
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('No package symlinks')
        if (not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc'
                or 'tests' in path.relative_to(source).parts
                or path.name in ('spot-splash', 'presentation.h', 'presentation.c','foreign.h','foreign.c','PACKAGE.json')):
            continue
        relative=path.relative_to(source)
        # Public OS artifact contains runtime assets and operator documentation,
        # not private CI/tests/plans or historical engineering documents.
        if len(relative.parts)==1 and path.name in ('release_tools.py','publish_release.py'):
            continue
        if len(relative.parts)==1 and path.suffix=='.md' and path.name not in ('README.md','ACCEPTANCE.md','BUILD_VERIFY.md','RELEASE_015.md','BOOTSTRAP_014.md','OS_SIGNING_SETUP.md','LEPTON_PROVISIONING.md','OTA_TEST_015.md','RELEASE_016.md','OTA_RELIABILITY_016.md','MIGRATION_014_016.md','OTA_TEST_016.md','RELEASE_017.md','OTA_TEST_017.md','HANDOVER_017.md','CHANGELOG.md'):
            continue
        name = 'spot_os/'+path.relative_to(source).as_posix()
        data = path.read_bytes()
        if path.suffix in ('.py','.sh','.md','.json','.txt','.xml','.service','.socket','.conf','.script','.plymouth') or path.name in ('Makefile','autostart','environment','VERSION','LICENSE'):
            data = data.replace(b'\r\n',b'\n')
        files[name] = data
    manifest = dict(schema=1, kind='os-installer', version=release, revision=revision, app_version='unchanged',
                    base_commit='c8c10f2c016a6a8a46ac313b75a51d8d79979889',
                    files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    files['spot_os/PACKAGE.json'] = (json.dumps(manifest, sort_keys=True, indent=2)+'\n').encode()
    output.mkdir(parents=True, exist_ok=True)
    archive = output/('spot-os-'+release+'.zip')
    # Stored ZIP avoids platform-dependent zlib streams. Content hashes remain identical.
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(digest+'  '+archive.name+'\n', encoding='ascii')
    (output/'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
    (output/'INSTALL.txt').write_bytes((source/'INSTALL.txt').read_bytes().replace(b'\r\n',b'\n'))
    return dict(artifact=str(archive), sha256=digest, files=len(files))


def verify_package(source, expected=None):
    source = Path(source)
    release,revision=expected or (VERSION,REVISION)
    manifest = json.loads((source/'PACKAGE.json').read_text())
    if manifest['kind'] != 'os-installer' or manifest['version'] != release or manifest.get('revision') != revision:
        raise ValueError('Wrong OS package')
    for name, digest in manifest['files'].items():
        path = source.parent/name
        if not path.resolve().is_relative_to(source.resolve()) or path.is_symlink():
            raise ValueError('Invalid package path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('OS package hash mismatch')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('outputs/spot-os-'+VERSION))
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
