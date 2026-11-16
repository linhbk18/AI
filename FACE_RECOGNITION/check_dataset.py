"""Read-only dataset audit. Run: python check_dataset.py [--dataset PATH].

Decode with Pillow when available, otherwise Windows System.Drawing.
Duplicate detection uses SHA-256 of file bytes (not visual similarity).
No images, embeddings or reports are written to disk.
"""

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unicodedata


EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff', '.webp'}
FORMAT_EXTENSIONS = {
    'JPEG': {'.jpg', '.jpeg'}, 'PNG': {'.png'}, 'BMP': {'.bmp'},
    'GIF': {'.gif'}, 'TIFF': {'.tif', '.tiff'}, 'WEBP': {'.webp'},
    'HEIC': {'.heic', '.heif'}, 'AVIF': {'.avif'},
}


def signature(path):
    with path.open('rb') as stream:
        head = stream.read(64)
    if head.startswith(b'\xff\xd8\xff'):
        return 'JPEG'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'PNG'
    if head[4:8] == b'ftyp':
        if head[8:12] in (b'heic', b'heix', b'hevc', b'hevx'):
            return 'HEIC'
        if head[8:12] in (b'avif', b'avis'):
            return 'AVIF'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'WEBP'
    return None


def table(headers, rows):
    rows = [[str(cell) for cell in row] for row in [headers, *rows]]
    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    for i, row in enumerate(rows):
        print(' | '.join(cell.ljust(width) for cell, width in zip(row, widths)))
        if i == 0:
            print('-+-'.join('-' * width for width in widths))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def snapshot(root):
    return {p.relative_to(root).as_posix(): ('dir' if p.is_dir() else digest(p))
            for p in sorted(root.rglob('*'))}


def decode_images(paths):
    try:
        from PIL import Image
    except ImportError:
        if os.name != 'nt':
            raise RuntimeError('Install Pillow to check image decoding on this platform.')
        # Paths are passed as JSON via stdin, never interpolated into shell code.
        script = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Drawing
$paths = [Console]::In.ReadToEnd() | ConvertFrom-Json
$results = @(foreach ($path in $paths) {
    $img = $null
    $bitmap = $null
    try {
        $img = [System.Drawing.Image]::FromFile($path)
        $fmt = $img.RawFormat.Guid.ToString()
        foreach ($codec in [System.Drawing.Imaging.ImageCodecInfo]::GetImageDecoders()) {
            if ($codec.FormatID -eq $img.RawFormat.Guid) { $fmt = $codec.FormatDescription; break }
        }
        # Rasterize to force pixel decoding rather than just reading headers.
        $bitmap = [System.Drawing.Bitmap]::new($img.Width, $img.Height)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try { $graphics.DrawImage($img, 0, 0, $img.Width, $img.Height) }
        finally { $graphics.Dispose() }
        @{path=$path; format=$fmt.ToUpperInvariant(); width=$img.Width; height=$img.Height; error=''}
    } catch {
        @{path=$path; format=''; width=0; height=0; error=$_.Exception.Message}
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $img) { $img.Dispose() }
    }
})
ConvertTo-Json -InputObject $results -Compress
'''
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            input=json.dumps([str(p) for p in paths], ensure_ascii=False),
            capture_output=True, encoding='utf-8', check=True,
        )
        print('Decoder: Windows System.Drawing (fallback; OpenCV/Pillow unavailable)')
        return {Path(r['path']): r for r in json.loads(result.stdout)}
    print('Decoder: Pillow')
    results = {}
    for path in paths:
        try:
            with Image.open(path) as img:
                fmt = img.format
                img.verify()
            with Image.open(path) as img:
                img.load()
                results[path] = dict(format=fmt, width=img.width, height=img.height, error='')
        except Exception as exc:
            results[path] = dict(format='', error=str(exc))
    return results


def audit(root):
    if not root.is_dir():
        raise RuntimeError(f'Dataset directory missing: {root}')
    before = snapshot(root)
    paths = sorted(p for p in root.rglob('*') if p.is_file())
    decoded = decode_images(paths) if paths else {}
    issues = []
    names = {}
    rows = []
    for split in ('registered', 'validation', 'Test'):
        folder = root / split
        if not folder.is_dir():
            issues.append(('MISSING DIRECTORY', split))
            names[split] = set()
            continue
        students = sorted(p for p in folder.iterdir() if p.is_dir())
        names[split] = {p.name for p in students}
        if split != 'Test' and len(students) != 5:
            issues.append(('STUDENT COUNT', f'{split}: {len(students)} (expected 5)'))
        groups = students if split != 'Test' else [folder]
        for group in groups:
            files = [p for p in paths if group in p.parents]
            readable = sum(not decoded[p]['error'] for p in files)
            standard = sum(p.suffix.lower() in EXTENSIONS for p in files)
            rows.append((split, group.name if split != 'Test' else '(all)',
                         len(files), standard, readable, len(files) - readable))
        if split != 'Test':
            for p in folder.iterdir():
                if p.is_file():
                    issues.append(('UNASSIGNED FILE', str(p.relative_to(root))))
    print('\nCOUNTS (decoded images include nonstandard extensions)')
    table(('Split', 'Student', 'Files', 'Image ext', 'Decoded', 'Failed'), rows)
    table(('TOTAL FILES', 'DECODED', 'FAILED'), [(
        len(paths), sum(not r['error'] for r in decoded.values()),
        sum(bool(r['error']) for r in decoded.values()))])
    for split, other in (('registered', 'validation'), ('validation', 'registered')):
        for name in sorted(names.get(split, set()) - names.get(other, set())):
            issues.append(('NAME MISMATCH', f'{name}: in {split}, missing from {other}'))
    print('\nStudent names match:', names.get('registered') == names.get('validation'))
    for split in ('registered', 'validation'):
        normalized = defaultdict(list)
        for name in names.get(split, set()):
            normalized[unicodedata.normalize('NFC', name).strip().casefold()].append(name)
        for group in normalized.values():
            if len(group) > 1:
                issues.append(('AMBIGUOUS NAME', f'{split}: {group}'))
    hashes = defaultdict(list)
    formats = defaultdict(int)
    for path in paths:
        relative = str(path.relative_to(root))
        hashes[before[path.relative_to(root).as_posix()]].append(relative)
        info = decoded[path]
        fmt = signature(path) or info['format']
        if fmt:
            formats[fmt] += 1
            if path.suffix.lower() not in FORMAT_EXTENSIONS.get(fmt, set()):
                issues.append(('EXTENSION MISMATCH', f'{relative}: actual {fmt}'))
        if info['error']:
            issues.append(('UNREADABLE / NON-IMAGE', relative + ': ' + info['error']))
    duplicates = [group for group in hashes.values() if len(group) > 1]
    for group in duplicates:
        issues.append(('EXACT DUPLICATE', ' = '.join(group)))
    for folder in [root, *sorted(p for p in root.rglob('*') if p.is_dir())]:
        if not any(folder.iterdir()):
            issues.append(('EMPTY DIRECTORY', str(folder.relative_to(root))))
    print('\nACTUAL FORMATS')
    table(('Format', 'Count'), sorted(formats.items()))
    print(f'\nExact duplicate groups: {len(duplicates)} (SHA-256; near-duplicates not assessed)')
    print('\nISSUES')
    table(('Type', 'Details'), issues or [('OK', 'No issues detected')])
    if before != snapshot(root):
        raise RuntimeError('Dataset changed during audit; investigate external modifications.')
    print('\nPASS: all dataset paths and file SHA-256 hashes unchanged.')
    print('Face presence, identity labels and ArcFace suitability are not assessed in this audit.')
    return 1 if issues else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path(__file__).resolve().parent / 'DATASET')
    args = parser.parse_args()
    try:
        sys.exit(audit(args.dataset.resolve()))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'AUDIT ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
