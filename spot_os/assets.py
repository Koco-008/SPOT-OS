"""Compose only a user-supplied original; no substitute SPOT artwork."""
import argparse
import hashlib
from pathlib import Path
import shutil
import struct
from PIL import Image, ImageChops
from .common import atomic


def build(source, output):
    source, output = Path(source), Path(output)
    original = Image.open(source).convert('RGBA')
    white = Image.new('RGBA', original.size, 'white')
    white.alpha_composite(original)
    rgb = white.convert('RGB')
    # Ignore canvas padding; retain the complete symbol AND wordmark ink box.
    # The supplied AI-exported PNG has almost invisible alpha specks outside the
    # artwork. Use visible ink for optical centering, not those padding specks.
    bbox = rgb.convert('L').point(lambda v: 255 if v < 240 else 0).getbbox()
    if not bbox:
        raise ValueError('Original logo has no visible ink')
    crop = rgb.crop(bbox)
    scale = min(560 / crop.width, 240 / crop.height)
    size = (round(crop.width * scale), round(crop.height * scale))
    logo = crop.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (800, 480), 'white')
    position = ((800 - size[0]) // 2, (480 - size[1]) // 2)
    canvas.paste(logo, position)
    # Identical quantized pixels for all phases, including early kernel <=224 colours.
    canvas = canvas.quantize(colors=224, method=Image.Quantize.MEDIANCUT).convert('RGB')
    output.mkdir(parents=True, exist_ok=True)
    if source.resolve() != (output / 'original.png').resolve():
        shutil.copyfile(source, output / 'original.png')
    canvas.save(output / 'splash.png')
    # Kernel framebuffer loader expects top-to-bottom pixel stream, descriptor=0.
    # This corresponds to upstream ImageMagick -flip example (inverted TGA convention).
    header = struct.pack('<BBBHHBHHHHBB', 0, 0, 2, 0, 0, 0, 0, 0, 800, 480, 24, 0)
    (output / 'splash.tga').write_bytes(header + canvas.tobytes('raw', 'BGR'))
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(output.iterdir()) if p.name in ('original.png', 'splash.png', 'splash.tga')}
    atomic(output / 'assets.json', dict(schema=1, resolution=[800, 480], source_bbox=bbox,
           logo_size=size, position=position, colours=len(canvas.getcolors(1000000)), files=hashes), 0o644)
    return canvas


def verify(directory):
    from .common import read
    directory = Path(directory)
    info = read(directory / 'assets.json')
    if not info or info['resolution'] != [800, 480]:
        raise ValueError('Original logo/assets missing; run assets builder first')
    for name, digest in info['files'].items():
        if name not in ('original.png', 'splash.png', 'splash.tga'):
            raise ValueError('Invalid asset manifest')
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Asset hash mismatch')
    data = (directory / 'splash.tga').read_bytes()
    if len(data) != 18 + 800 * 480 * 3 or data[2] != 2 or data[16:] [:2] != bytes([24, 0]):
        raise ValueError('Unsupported TGA format')
    png = Image.open(directory / 'splash.png').convert('RGB')
    if data[18:] != png.tobytes('raw', 'BGR'):
        raise ValueError('Boot phases have different pixels')
    if png.size != (800, 480) or len(png.getcolors(1000000)) > 224:
        raise ValueError('Invalid dimensions/colour count')
    if any(png.getpixel(p) != (255, 255, 255) for p in ((0, 0), (799, 0), (0, 479), (799, 479))):
        raise ValueError('Background must be exact white')
    return info


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('original', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'artwork')
    args = parser.parse_args()
    build(args.original, args.output)
    verify(args.output)
