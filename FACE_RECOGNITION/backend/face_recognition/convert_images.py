"""Convert registered HEIC files to separate RGB JPEGs; never modify DATASET."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / 'DATASET'
OUTPUTS = ROOT / 'outputs'
MAPPING = OUTPUTS / 'conversion_mapping.csv'


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def original_snapshot():
    return {p.relative_to(DATASET).as_posix():
            ('directory' if p.is_dir() else sha256(p))
            for p in sorted(DATASET.rglob('*'))}


def protect_originals():
    OUTPUTS.mkdir(exist_ok=True)
    path = OUTPUTS / 'dataset_original_hashes.json'
    current = original_snapshot()
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != current:
            raise RuntimeError('DATASET differs from recorded baseline; investigate before continuing.')
    else:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(current, stream, ensure_ascii=False, indent=2)
    return current


def verify_originals(before):
    if original_snapshot() != before:
        raise RuntimeError('DATASET changed during processing.')
    print('PASS: original DATASET paths and SHA-256 hashes unchanged.')


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def convert():
    from PIL import Image, ImageOps
    import pillow_heif
    pillow_heif.register_heif_opener()
    before = protect_originals()
    previous = {}
    if MAPPING.exists():
        with MAPPING.open(encoding='utf-8-sig', newline='') as stream:
            previous = {r['source']: r for r in csv.DictReader(stream)}
    rows = []
    try:
        for source in sorted((DATASET / 'registered').rglob('*')):
            if not source.is_file():
                continue
            with source.open('rb') as stream:
                header = stream.read(64)
            if header[4:8] != b'ftyp' or header[8:12] not in (b'heic', b'heix', b'hevc', b'hevx', b'mif1'):
                continue
            relative = source.relative_to(DATASET / 'registered')
            target = OUTPUTS / 'converted_registered' / relative.with_suffix('.jpg')
            source_key = source.relative_to(ROOT).as_posix()
            row = dict(source=source_key, converted=target.relative_to(ROOT).as_posix(),
                       source_sha256=sha256(source), converted_sha256='',
                       width='', height='', status='error', error='')
            try:
                with Image.open(source) as image:
                    if image.format != 'HEIF':
                        raise ValueError(f'Expected HEIF decoder, got {image.format}')
                    rgb = ImageOps.exif_transpose(image).convert('RGB')
                    rgb.load()
                if target.exists():
                    old = previous.get(source_key, {})
                    if (old.get('source_sha256') != row['source_sha256'] or
                            old.get('converted_sha256') != sha256(target)):
                        raise FileExistsError(f'Refusing overwrite of unverified existing file: {target}')
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation: an existing JPEG is never overwritten.
                    with target.open('xb') as stream:
                        rgb.save(stream, format='JPEG', quality=95, subsampling=0)
                with Image.open(target) as check:
                    check.load()
                    if check.format != 'JPEG' or check.mode != 'RGB' or check.size != rgb.size:
                        raise ValueError('JPEG verification failed')
                    row.update(width=check.width, height=check.height)
                row.update(converted_sha256=sha256(target), status='ok')
                rgb.close()
            except Exception as exc:
                row['error'] = f'{type(exc).__name__}: {exc}'
            rows.append(row)
            print(f"{row['status']:5} | {source_key} -> {row['converted']} | {row['error']}")
        fields = ['source', 'converted', 'source_sha256', 'converted_sha256', 'width', 'height', 'status', 'error']
        write_csv(MAPPING, fields, rows)
    finally:
        verify_originals(before)
    success = sum(r['status'] == 'ok' for r in rows)
    print(f'Converted and reopened: {success}/{len(rows)}. Mapping: {MAPPING}')
    return 0 if rows and success == len(rows) else 1


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(convert())
