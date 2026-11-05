import hashlib
import json
from pathlib import Path
import struct
import zipfile
from PIL import Image
from spot_os.assets import build, verify
from spot_os.package import build as package, verify_package

ROOT = Path(__file__).parents[1]


def test_original_assets_identical_phases_and_optically_centered(tmp_path):
    image = build(ROOT/'artwork/original.png', tmp_path/'assets')
    info = verify(tmp_path/'assets')
    original_info=verify(ROOT/'artwork')
    assert info['files']['original.png'] == original_info['files']['original.png']
    assert info['files']['splash.tga'] == original_info['files']['splash.tga']
    # zlib-ng (Windows Pillow) and zlib (Linux) may encode PNG differently;
    # compare every decoded pixel rather than requiring the same compression stream.
    assert image.tobytes() == Image.open(ROOT/'artwork/splash.png').convert('RGB').tobytes()
    assert image.size == (800, 480)
    bbox = image.convert('L').point(lambda v: 255 if v < 200 else 0).getbbox()
    assert abs((bbox[0]+bbox[2])/2-400) <= 1
    assert abs((bbox[1]+bbox[3])/2-240) <= 1
    ratio = info['logo_size'][0]/info['logo_size'][1]
    box = info['source_bbox']
    assert abs(ratio-(box[2]-box[0])/(box[3]-box[1])) < .02
    tga = (tmp_path/'assets/splash.tga').read_bytes()
    assert struct.unpack_from('<HH', tga, 12) == (800, 480)
    assert tga[18:] == image.tobytes('raw', 'BGR')


def test_reproducible_package_and_content_verification(tmp_path):
    a, b = package(tmp_path/'a'), package(tmp_path/'b')
    assert a['sha256'] == b['sha256']
    with zipfile.ZipFile(a['artifact']) as z:
        z.extractall(tmp_path/'unpacked')
    manifest = verify_package(tmp_path/'unpacked/spot_os')
    assert manifest['app_version'] == 'unchanged'
    assert not any(name.endswith('.pyc') for name in manifest['files'])
    (tmp_path/'unpacked/spot_os/VERSION').write_text('bad')
    import pytest
    with pytest.raises(ValueError, match='hash mismatch'):
        verify_package(tmp_path/'unpacked/spot_os')


def test_cursor_is_transparent():
    data = (ROOT/'cursors/spot-transparent/cursors/left_ptr').read_bytes()
    assert data[:4] == b'Xcur'
    assert data[-4:] == b'\0'*4
