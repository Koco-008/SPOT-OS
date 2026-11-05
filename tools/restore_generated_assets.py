#!/usr/bin/env python3
"""Reconstruct the original immutable 800x480 24-bit TGA from public PNG pixels.

No download, no signing, no re-encoding of the two original PNG files.
"""
from hashlib import sha256
import json
from pathlib import Path
import struct
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / 'spot_os' / 'artwork'
EXPECTED = '71f79634787c37e62a52cac2b072716fca48f8fb0786ddd92850f91496587c11'

def main():
    manifest = json.loads((ART / 'assets.json').read_text(encoding='utf-8'))
    assert manifest['files']['splash.tga'] == EXPECTED, 'Original TGA digest differs'
    for name in ('original.png', 'splash.png'):
        expected = manifest['files'][name]
        assert sha256((ART / name).read_bytes()).hexdigest() == expected, f'{name}: source digest mismatch'
    with Image.open(ART / 'splash.png') as file:
        img = file.convert('RGB')
    assert img.size == (800, 480), 'Unexpected native image resolution'
    assert len(img.getcolors(1000000)) <= 224, 'Unexpected quantization'
    header = struct.pack('<BBBHHBHHHHBB', 0, 0, 2, 0, 0, 0, 0, 0, 800, 480, 24, 0)
    data = header + img.tobytes('raw', 'BGR')
    assert sha256(data).hexdigest() == EXPECTED, 'Reconstructed TGA does not match signed source'
    output = ART / 'splash.tga'
    if output.is_file() and output.read_bytes() != data:
        raise ValueError('Existing TGA has unexpected content; will not overwrite')
    if not output.exists():
        output.write_bytes(data)
    from spot_os.assets import verify
    verify(ART)
    print(f'SPOT OS 0.1.7 TGA restored and verified: SHA256 {EXPECTED}')

if __name__ == '__main__':
    main()
