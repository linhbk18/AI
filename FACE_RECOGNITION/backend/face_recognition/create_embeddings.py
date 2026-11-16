"""Generate pretrained ArcFace embeddings from the registered manifest only.

Run from any directory with the project's .venv Python. Exit codes:
0 = every input succeeded; 1 = image errors (see reports); 2 = preflight failure.
No training, camera access, identity inference, or image modification.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

from convert_images import ROOT, DATASET, OUTPUTS, sha256, protect_originals, verify_originals

SETTINGS = dict(model_name='ArcFace', detector_backend='retinaface',
                normalization='base', align=True, enforce_detection=True,
                expand_percentage=0, anti_spoofing=False, max_faces=None)
DIMENSION = 512
WEIGHTS = {
    'arcface_weights.h5': 137026640,
    'retinaface.h5': 118667368,
}
RELEASE = 'https://github.com/serengil/deepface_models/releases/download/v1.0/'


def atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def load_manifest(path):
    """Validate schema, labels, split, enrollment coverage, and converted paths."""
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        expected = ['split', 'student', 'source', 'image_path', 'preprocessing']
        if reader.fieldnames != expected:
            raise ValueError(f'Manifest columns {reader.fieldnames!r}; expected {expected!r}')
        rows = list(reader)
    if not rows:
        raise ValueError('Manifest is empty')
    with (OUTPUTS / 'conversion_mapping.csv').open(encoding='utf-8-sig', newline='') as stream:
        mappings = list(csv.DictReader(stream))
    converted = {r['source']: r for r in mappings if r['status'] == 'ok'}
    if len(converted) != sum(r['status'] == 'ok' for r in mappings):
        raise ValueError('Duplicate source in conversion mapping')
    registered = (DATASET / 'registered').resolve()
    baseline = json.loads((OUTPUTS / 'dataset_original_hashes.json').read_text(encoding='utf-8'))
    seen_source, seen_image = set(), set()
    for line, row in enumerate(rows, 2):
        if None in row or any(value is None or not value for value in row.values()):
            raise ValueError(f'Malformed manifest row {line}')
        if row['split'] != 'registered':
            raise ValueError(f'Non-registered split at row {line}: {row["split"]}')
        source = (ROOT / row['source']).resolve()
        relative = source.relative_to(registered)  # Reject validation, Test and path traversal.
        if len(relative.parts) < 2 or relative.parts[0] != row['student']:
            raise ValueError(f'Student label differs from registered source folder, row {line}')
        image = Path(row['image_path'])
        image = (ROOT / image).resolve() if not image.is_absolute() else image.resolve()
        if not source.is_file() or not image.is_file():
            raise FileNotFoundError(f'Missing source or image at row {line}: {source}, {image}')
        if source in seen_source or image in seen_image:
            raise ValueError(f'Duplicate source or image path at row {line}')
        source_key = source.relative_to(ROOT).as_posix()
        with source.open('rb') as stream:
            header = stream.read(64)
        if header[4:8] == b'ftyp':
            mapping = converted.get(source_key)
            if not mapping or image != (ROOT / mapping['converted']).resolve():
                raise ValueError(f'HEIC must use its verified converted JPEG, row {line}')
            if not image.is_relative_to((OUTPUTS / 'converted_registered' / row['student']).resolve()):
                raise ValueError(f'Converted image outside student output folder, row {line}')
            if sha256(source) != mapping['source_sha256'] or sha256(image) != mapping['converted_sha256']:
                raise ValueError(f'HEIC/JPEG conversion checksum mismatch, row {line}')
        elif image != source:
            raise ValueError(f'Unexpected image substitution, row {line}')
        if row['preprocessing'] != 'EXIF transpose, RGB':
            raise ValueError(f'Unsupported preprocessing at row {line}')
        source_hash, image_hash = sha256(source), sha256(image)
        if baseline.get(source.relative_to(DATASET).as_posix()) != source_hash:
            raise ValueError(f'Original source differs from baseline, row {line}')
        row.update(source=source_key, image_path=str(image), source_sha256=source_hash,
                   image_sha256=image_hash)
        seen_source.add(source)
        seen_image.add(image)
    enrollment_files = {p.resolve() for p in registered.rglob('*') if p.is_file()}
    if seen_source != enrollment_files:
        raise ValueError('Manifest does not cover registered exactly; missing or extra source files')
    students = {p.name for p in registered.iterdir() if p.is_dir()}
    if {r['student'] for r in rows} != students:
        raise ValueError('Manifest student labels do not cover all registered identities')
    print('Manifest columns:', ', '.join(expected), flush=True)
    print(f'Preflight: {len(rows)} registered images, {len(students)} students; all paths and labels verified.', flush=True)
    return rows


def validate_vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != DIMENSION:
        raise ValueError(f'Expected {DIMENSION} embedding components')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise ValueError('Embedding has non-numeric components')
    vector = [float(v) for v in value]
    if not all(math.isfinite(v) for v in vector):
        raise ValueError('Embedding contains NaN or Infinity')
    if not any(vector):
        raise ValueError('Embedding has zero norm')
    return vector


def ensure_weights():
    """Download official pretrained weights using TLS; never replace cached weights."""
    import h5py
    import requests
    directory = OUTPUTS / '.deepface' / 'weights'
    directory.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, expected_size in WEIGHTS.items():
        path = directory / name
        if not path.exists():
            temporary = None
            try:
                print(f'Downloading official pretrained {name} ({expected_size / 1e6:.1f} MB)...', flush=True)
                with requests.get(RELEASE + name, stream=True, timeout=(30, 120)) as response:
                    response.raise_for_status()
                    with tempfile.NamedTemporaryFile(dir=directory, suffix='.download', delete=False) as stream:
                        temporary = Path(stream.name)
                        total, last = 0, time.monotonic()
                        for chunk in response.iter_content(1024 * 1024):
                            stream.write(chunk)
                            total += len(chunk)
                            if time.monotonic() - last >= 10:
                                print(f'  {name}: {total / 1e6:.1f}/{expected_size / 1e6:.1f} MB', flush=True)
                                last = time.monotonic()
                if temporary.stat().st_size != expected_size:
                    raise ValueError(f'Unexpected downloaded size for {name}: {temporary.stat().st_size}')
                with h5py.File(temporary, 'r') as weights:
                    if not list(weights.keys()):
                        raise ValueError(f'Empty HDF5 weights: {name}')
                if path.exists():
                    raise FileExistsError(f'Weight file appeared during download: {path}')
                temporary.rename(path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        if path.stat().st_size != expected_size:
            raise ValueError(f'Cached weight size mismatch (not overwritten): {path}')
        with h5py.File(path, 'r'):
            pass
        result[name] = dict(url=RELEASE + name, size=path.stat().st_size, sha256=sha256(path))
    return result


def save_reports(rows, records, details, config):
    summaries = []
    for student in sorted({r['student'] for r in rows}):
        inputs = sum(r['student'] == student for r in rows)
        errors = [d for d in details if d['student'] == student and d['status'] != 'ok']
        success = sum(r['student_id'] == student for r in records)
        summaries.append(dict(student=student, input_images=inputs, successful_embeddings=success,
                              error_images=len(errors), error_reasons=' | '.join(
                                  f"{d['source']}: {d['error']}" for d in errors)))
    fields = ['student', 'input_images', 'successful_embeddings', 'error_images', 'error_reasons']
    with (OUTPUTS / 'embedding_report.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    with (OUTPUTS / 'embedding_image_report.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['student', 'source', 'image_path', 'status', 'face_count', 'error'])
        writer.writeheader()
        writer.writerows(details)
    # Round-trip validation before publishing records.
    lookup = {r['source']: r for r in rows}
    if len({r['source'] for r in records}) != len(records):
        raise ValueError('Duplicate embedding record source')
    for record in records:
        validate_vector(record['embedding'])
        expected = lookup[record['source']]
        if record['student_id'] != expected['student'] or record['image_path'] != expected['image_path']:
            raise ValueError('Embedding label/path association failed')
    config.update(successful_embeddings=len(records), error_images=len(rows)-len(records),
                  status='complete' if len(records) == len(rows) else 'partial' if records else 'failed',
                  completed_at_utc=datetime.now(timezone.utc).isoformat())
    atomic_json(OUTPUTS / 'face_embeddings.json', records)
    atomic_json(OUTPUTS / 'face_config.json', config)
    print('\nStudent        | Input | Embeddings | Errors')
    for item in summaries:
        print(f"{item['student']:14} | {item['input_images']:5} | {item['successful_embeddings']:10} | {item['error_images']:6}")
        if item['error_reasons']:
            print('  ' + item['error_reasons'])
    print(f'TOTAL          | {len(rows):5} | {len(records):10} | {len(rows)-len(records):6}', flush=True)


def run(args):
    OUTPUTS.mkdir(exist_ok=True)
    for name in ('face_embeddings.json', 'face_config.json', 'embedding_report.csv', 'embedding_image_report.csv'):
        if (OUTPUTS / name).exists() and not args.overwrite:
            raise FileExistsError(f'Output exists: {name}. Use --overwrite only to regenerate derived outputs.')
    before = protect_originals()
    rows = load_manifest(args.manifest.resolve())
    os.environ['DEEPFACE_HOME'] = str(OUTPUTS)
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
    ca = ROOT / '.venv' / 'system-ca.pem'
    if ca.exists():
        os.environ.setdefault('REQUESTS_CA_BUNDLE', str(ca))
    config = dict(schema_version=1, **SETTINGS, embedding_dimension=DIMENSION,
                  manifest=str(args.manifest.resolve()), manifest_sha256=sha256(args.manifest),
                  manifest_columns=['split','student','source','image_path','preprocessing'],
                  input_images=len(rows), split='registered', student_labels_source='manifest.student',
                  input_preprocessing='Pillow EXIF transpose -> RGB -> OpenCV BGR array',
                  pretrained=True, training_performed=False, python=sys.version,
                  packages={name: importlib.metadata.version(name) for name in
                            ['deepface','tensorflow','keras','opencv-python','numpy','Pillow','retina-face']},
                  started_at_utc=datetime.now(timezone.utc).isoformat())
    records, details = [], []
    startup_error = None
    try:
        try:
            import numpy as np
            import cv2
            import tensorflow as tf
            from PIL import Image, ImageOps
            tf.config.threading.set_intra_op_parallelism_threads(args.threads)
            tf.config.threading.set_inter_op_parallelism_threads(1)
            cv2.setNumThreads(args.threads)
            from deepface import DeepFace
            from deepface.modules import modeling
            config['weights'] = ensure_weights()
            print('Loading pretrained ArcFace and RetinaFace (inference only)...', flush=True)
            DeepFace.build_model(model_name='ArcFace')
            modeling.build_model(task='face_detector', model_name='retinaface')
        except Exception as exc:
            startup_error = f'Model/environment initialization failed: {type(exc).__name__}: {exc}'
            print(startup_error, flush=True)
        for index, row in enumerate(rows, 1):
            detail = dict(student=row['student'], source=row['source'], image_path=row['image_path'],
                          status='error', face_count='', error='')
            try:
                if startup_error:
                    raise RuntimeError(startup_error)
                image_path = Path(row['image_path'])
                if sha256(image_path) != row['image_sha256']:
                    raise ValueError('Image changed after manifest preflight')
                with Image.open(image_path) as image:
                    rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                results = DeepFace.represent(img_path=bgr, **SETTINGS)
                detail['face_count'] = len(results)
                if len(results) != 1:
                    raise ValueError(f'Exactly one RetinaFace face required; found {len(results)}')
                vector = validate_vector(results[0]['embedding'])
                record = dict(student_id=row['student'], source=row['source'], image_path=row['image_path'],
                              source_sha256=row['source_sha256'], image_sha256=row['image_sha256'],
                              embedding=vector, embedding_dimension=DIMENSION,
                              model_name='ArcFace', detector_backend='retinaface',
                              normalization='base', align=True, enforce_detection=True,
                              face_count=1, face_confidence=float(results[0]['face_confidence']))
                records.append(record)
                detail['status'] = 'ok'
            except Exception as exc:
                detail['error'] = f'{type(exc).__name__}: {exc}'
            details.append(detail)
            print(f"[{index:02}/{len(rows)}] {detail['status']:5} | {row['student']} | {Path(row['source']).name} | {detail['error']}", flush=True)
        verify_originals(before)
        for row in rows:
            if sha256(Path(row['image_path'])) != row['image_sha256']:
                raise RuntimeError(f'Input image changed during embedding: {row["image_path"]}')
        config['dataset_unchanged'] = True
        save_reports(rows, records, details, config)
        persisted = json.loads((OUTPUTS / 'face_embeddings.json').read_text(encoding='utf-8'))
        assert len(persisted) == len(records)
        for record in persisted:
            validate_vector(record['embedding'])
        print(f'Validated persisted output: {len(persisted)} finite, nonzero {DIMENSION}-D vectors.', flush=True)
        return 0 if len(records) == len(rows) else 1
    finally:
        verify_originals(before)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=OUTPUTS / 'arcface_registered_manifest.csv')
    parser.add_argument('--overwrite', action='store_true', help='Regenerate existing derived embedding outputs')
    parser.add_argument('--threads', type=int, choices=range(1, 17), default=2)
    options = parser.parse_args()
    try:
        sys.exit(run(options))
    except Exception as exc:
        print(f'EMBEDDING ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)
