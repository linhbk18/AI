"""Closed-set validation against an immutable registered ArcFace gallery.

No rejection threshold is selected. Validation embeddings are cached separately
and never appended to the registered gallery. Exit 1 means image processing
errors; wrong identity predictions are evaluation results, not program errors.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

import numpy as np

try:
    from .convert_images import ROOT, DATASET, OUTPUTS, sha256, protect_originals, verify_originals
    from .create_embeddings import atomic_json, load_manifest, validate_vector
except ImportError:
    from convert_images import ROOT, DATASET, OUTPUTS, sha256, protect_originals, verify_originals
    from create_embeddings import atomic_json, load_manifest, validate_vector

GALLERY = OUTPUTS / 'face_embeddings.json'
CONFIG = OUTPUTS / 'face_config.json'
CACHE = OUTPUTS / 'validation_embedding_cache.json'
SETTING_KEYS = ('model_name', 'detector_backend', 'align', 'normalization',
                'enforce_detection', 'expand_percentage', 'anti_spoofing', 'max_faces')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def preflight():
    config = read_json(CONFIG)
    settings = {key: config[key] for key in SETTING_KEYS}
    if (settings['model_name'] != 'ArcFace' or settings['detector_backend'] != 'retinaface'
            or settings['align'] is not True or settings['enforce_detection'] is not True
            or settings['max_faces'] is not None or config['embedding_dimension'] != 512):
        raise ValueError('Unexpected or incomplete registered pipeline configuration')
    if config['status'] != 'complete':
        raise ValueError('Registered gallery generation is incomplete')
    versions = {name: importlib.metadata.version(name) for name in config['packages']}
    if versions != config['packages']:
        raise ValueError(f'Installed versions differ from enrollment: {versions}')
    manifest = Path(config['manifest'])
    if sha256(manifest) != config['manifest_sha256']:
        raise ValueError('Registered manifest checksum differs from face_config.json')
    inputs = load_manifest(manifest)
    expected = {r['source']: r for r in inputs}
    records = read_json(GALLERY)
    if not isinstance(records, list) or len(records) != 92 or len(inputs) != 92:
        raise ValueError('Expected exactly 92 registered embedding records')
    seen = set()
    for record in records:
        validate_vector(record['embedding'])
        source = record['source']
        if source in seen or source not in expected:
            raise ValueError(f'Duplicate or unregistered embedding source: {source}')
        original = expected[source]
        if (record['student_id'] != original['student'] or
                Path(record['image_path']).resolve() != Path(original['image_path']).resolve() or
                record['source_sha256'] != original['source_sha256'] or
                record['image_sha256'] != original['image_sha256']):
            raise ValueError(f'Embedding label, path or image checksum mismatch: {source}')
        for key in ('model_name','detector_backend','normalization','align','enforce_detection'):
            if record[key] != settings[key]:
                raise ValueError(f'Embedding configuration mismatch: {source}: {key}')
        if record['embedding_dimension'] != 512 or record['face_count'] != 1:
            raise ValueError(f'Invalid registered embedding metadata: {source}')
        seen.add(source)
    if seen != set(expected):
        raise ValueError('Registered embedding source coverage mismatch')
    students = sorted({r['student_id'] for r in records})
    if len(students) != 5:
        raise ValueError('Expected five registered identities')
    for filename, info in config['weights'].items():
        if sha256(OUTPUTS / '.deepface' / 'weights' / filename) != info['sha256']:
            raise ValueError(f'Pretrained weight checksum differs from enrollment: {filename}')
    validation = DATASET / 'validation'
    if {p.name for p in validation.iterdir() if p.is_dir()} != set(students):
        raise ValueError('Validation and registered student names differ')
    paths = sorted(p for p in validation.rglob('*') if p.is_file())
    if len(paths) != 25:
        raise ValueError(f'Expected 25 validation inputs, found {len(paths)}')
    samples = []
    training_hashes = {r['source_sha256'] for r in records} | {r['image_sha256'] for r in records}
    for path in paths:
        rel = path.relative_to(validation)
        if len(rel.parts) < 2 or rel.parts[0] not in students:
            raise ValueError(f'Validation image lacks an explicit student folder: {path}')
        checksum = sha256(path)
        if checksum in training_hashes:
            raise ValueError(f'Validation/registered exact-byte overlap: {path}')
        samples.append(dict(source=path.relative_to(ROOT).as_posix(), image_path=str(path.resolve()),
                            student=rel.parts[0], sha256=checksum))
    print('Gallery verified:', dict(Counter(r['student_id'] for r in records)), flush=True)
    print('Validation inputs:', dict(Counter(r['student'] for r in samples)), flush=True)
    print('Pipeline:', settings, flush=True)
    return config, settings, records, students, samples, versions


def cosine_scores(query, gallery):
    query = np.asarray(validate_vector(query), dtype=np.float64)
    matrix = np.asarray(gallery, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != query.size or not np.isfinite(matrix).all():
        raise ValueError('Malformed gallery matrix')
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms == 0):
        raise ValueError('Zero-norm registered vector')
    return np.clip((matrix @ query) / (norms * np.linalg.norm(query)), -1., 1.)


def rank_identities(query, records, students):
    scores = cosine_scores(query, [r['embedding'] for r in records])
    ranked = []
    for student in students:
        indices = [i for i, r in enumerate(records) if r['student_id'] == student]
        best = max(indices, key=lambda i: float(scores[i]))
        ranked.append(dict(student=student, score=float(scores[best]), source=records[best]['source']))
    return sorted(ranked, key=lambda r: (-r['score'], r['student']))


def summarize(results, students):
    matrix = [[0 for _ in students] for _ in students]
    processed = [r for r in results if r['status'] == 'ok']
    for row in processed:
        matrix[students.index(row['true_student'])][students.index(row['predicted_student'])] += 1
    correct = sum(r['correct'] for r in processed)
    errors = len(results) - len(processed)
    per_student = []
    for i, student in enumerate(students):
        actual = [r for r in results if r['true_student'] == student]
        valid = [r for r in actual if r['status'] == 'ok']
        hits = sum(r['correct'] for r in valid)
        per_student.append(dict(student=student, input_images=len(actual), processed_images=len(valid),
                                correct=hits, incorrect=len(valid)-hits, processing_errors=len(actual)-len(valid),
                                accuracy=hits/len(actual) if actual else None))
    hardest = sorted(processed, key=lambda r: (r['true_vs_best_other_margin'], r['source']))[:5]
    competitor_counts = Counter((r['true_student'], r['best_other_student']) for r in processed)
    return dict(input_images=len(results), processed_images=len(processed), correct=correct,
                incorrect=len(processed)-correct, processing_errors=errors,
                accuracy=correct/len(results) if results else None,
                accuracy_denominator='All validation inputs, including processing failures as unsuccessful',
                accuracy_on_processed_images=correct/len(processed) if processed else None,
                per_student=per_student,
                confusion_matrix=dict(labels=students, rows='true_student', columns='predicted_student',
                                      matrix=matrix, processing_errors_excluded=errors),
                misclassifications=[r for r in processed if not r['correct']],
                processing_failures=[r for r in results if r['status'] != 'ok'],
                lowest_true_vs_other_margins=hardest,
                strongest_other_identity_counts=[dict(true_student=a, competing_student=b, count=n)
                                                for (a,b),n in competitor_counts.most_common()])


def print_tables(results, summary, students):
    print('\nIMAGE RESULTS (cosine similarity, not a probability)')
    print('True           | Predicted      | Best     | Other    | Margin   | Correct | Image')
    for r in results:
        if r['status'] == 'ok':
            print(f"{r['true_student']:14} | {r['predicted_student']:14} | {r['cosine_similarity']:.6f} | "
                  f"{r['second_best_similarity']:.6f} | {r['top1_top2_margin']:.6f} | {str(r['correct']):7} | {r['source']}")
        else:
            print(f"{r['true_student']:14} | ERROR | {r['source']} | {r['error']}")
    print('\nPER-STUDENT RESULTS')
    print('Student        | Input | Correct | Incorrect | Error | Accuracy')
    for r in summary['per_student']:
        print(f"{r['student']:14} | {r['input_images']:5} | {r['correct']:7} | {r['incorrect']:9} | "
              f"{r['processing_errors']:5} | {r['accuracy']:.2%}")
    print('\nCONFUSION MATRIX (rows=true, columns=predicted)')
    print(' ' * 16 + ' | '.join(f'{s:14}' for s in students))
    for name, row in zip(students, summary['confusion_matrix']['matrix']):
        print(f'{name:14} | ' + ' | '.join(f'{n:14}' for n in row))
    print(f"TOTAL: correct={summary['correct']}, incorrect={summary['incorrect']}, "
          f"errors={summary['processing_errors']}, accuracy={summary['accuracy']:.2%}")
    print('Open-set rejection: NOT evaluated; no labelled unknown-person dataset. No rejection threshold selected.')


def run(args):
    before = protect_originals()
    protected = {p:sha256(p) for p in (GALLERY, CONFIG, OUTPUTS/'arcface_registered_manifest.csv')}
    config, settings, records, students, samples, versions = preflight()
    signature = hashlib.sha256(json.dumps(dict(settings=settings, versions=versions,
        gallery_sha256=protected[GALLERY], weights=config['weights'],
        preprocessing=config['input_preprocessing']), sort_keys=True).encode()).hexdigest()
    cache = dict(schema_version=1, split='validation', pipeline_signature=signature, records={})
    if CACHE.exists() and not args.refresh_cache:
        candidate = read_json(CACHE)
        if candidate.get('pipeline_signature') == signature and candidate.get('split') == 'validation':
            cache = candidate
    os.environ['DEEPFACE_HOME'] = str(OUTPUTS)
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL','2')
    results = []
    model = None
    startup_error = None
    try:
        for index, sample in enumerate(samples,1):
            row = dict(source=sample['source'], image_path=sample['image_path'],
                       true_student=sample['student'], predicted_student='', status='error',
                       correct=False, cosine_similarity='', second_best_student='',
                       second_best_similarity='', top1_top2_margin='', best_true_similarity='',
                       best_other_student='', best_other_similarity='', true_vs_best_other_margin='',
                       nearest_registered_source='', face_count='', cache_used=False, error='')
            try:
                cached = cache['records'].get(sample['source'])
                if (cached and cached.get('sha256') == sample['sha256']
                        and cached.get('student') == sample['student'] and cached.get('face_count') == 1):
                    vector = validate_vector(cached['embedding'])
                    row['cache_used'] = True
                    row['face_count'] = 1
                else:
                    if startup_error:
                        raise RuntimeError(startup_error)
                    if model is None:
                        try:
                            import tensorflow as tf
                            import cv2
                            from PIL import Image, ImageOps
                            tf.config.threading.set_intra_op_parallelism_threads(args.threads)
                            tf.config.threading.set_inter_op_parallelism_threads(1)
                            cv2.setNumThreads(args.threads)
                            from deepface import DeepFace
                            from deepface.modules import modeling
                            print('Loading the same pretrained ArcFace and RetinaFace weights...',flush=True)
                            DeepFace.build_model(model_name=settings['model_name'])
                            modeling.build_model(task='face_detector',model_name=settings['detector_backend'])
                            model = DeepFace
                        except Exception as exc:
                            startup_error = f'Model initialization: {type(exc).__name__}: {exc}'
                            raise
                    if sha256(Path(sample['image_path'])) != sample['sha256']:
                        raise ValueError('Validation image changed after preflight')
                    with Image.open(sample['image_path']) as image:
                        rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
                    faces = model.represent(img_path=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR), **settings)
                    row['face_count'] = len(faces)
                    if len(faces) != 1:
                        raise ValueError(f'Expected exactly one RetinaFace face, found {len(faces)}')
                    vector = validate_vector(faces[0]['embedding'])
                    cache['records'][sample['source']] = dict(**sample, embedding=vector, face_count=1)
                    atomic_json(CACHE, cache)
                ranked = rank_identities(vector, records, students)
                best, second = ranked[:2]
                true = next(r for r in ranked if r['student'] == sample['student'])
                other = next(r for r in ranked if r['student'] != sample['student'])
                row.update(status='ok', predicted_student=best['student'],
                           correct=best['student']==sample['student'], cosine_similarity=best['score'],
                           second_best_student=second['student'], second_best_similarity=second['score'],
                           top1_top2_margin=best['score']-second['score'], best_true_similarity=true['score'],
                           best_other_student=other['student'], best_other_similarity=other['score'],
                           true_vs_best_other_margin=true['score']-other['score'],
                           nearest_registered_source=best['source'])
            except Exception as exc:
                row['error'] = f'{type(exc).__name__}: {exc}'
            results.append(row)
            score = f"{row['cosine_similarity']:.6f}" if row['status']=='ok' else row['error']
            print(f"[{index:02}/{len(samples)}] {row['status']:5} | {sample['student']} -> "
                  f"{row['predicted_student']} | {score} | {Path(sample['source']).name}",flush=True)
        summary = summarize(results,students)
        summary.update(schema_version=1, evaluation_mode='closed_set',
                       matching_rule='maximum sample cosine per student, then highest-scoring student',
                       settings=settings, embedding_dimension=512, registered_embedding_count=len(records),
                       registered_counts=dict(Counter(r['student_id'] for r in records)),
                       embedding_gallery_sha256=protected[GALLERY], enrollment_config_sha256=protected[CONFIG],
                       installed_versions=versions, threshold=None, rejection_applied=False,
                       unknown_evaluation=dict(performed=False, unknown_samples=0,
                           reason='No explicitly labelled unknown-person dataset. Test/group images have no unknown ground-truth labels.',
                           reliable_rejection_threshold_available=False, FAR=None, FRR=None),
                       scope_limitations=['25 known-person images from five students; small closed-set evaluation.',
                           'Scores and margins are diagnostic; cosine similarity is not a probability.',
                           'Threshold tuning requires labelled unknown people and independent held-out evaluation.',
                           'Exact-byte enrollment/validation duplicates checked; near-duplicate leakage not assessed.'],
                       completed_at_utc=datetime.now(timezone.utc).isoformat())
        verify_originals(before)
        for path,checksum in protected.items():
            if sha256(path) != checksum:
                raise RuntimeError(f'Protected registered artifact changed: {path}')
        summary['dataset_unchanged'] = True
        summary['registered_artifacts_unchanged'] = True
        report = OUTPUTS/'validation_report.csv'
        with report.open('w',encoding='utf-8-sig',newline='') as stream:
            writer = csv.DictWriter(stream,fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)
        atomic_json(OUTPUTS/'validation_summary.json',summary)
        print_tables(results,summary,students)
        return 1 if summary['processing_errors'] else 0
    finally:
        verify_originals(before)
        for path,checksum in protected.items():
            if sha256(path) != checksum:
                raise RuntimeError(f'Protected registered artifact changed: {path}')
        print('PASS: registered embeddings, face_config and registered manifest unchanged.',flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-cache',action='store_true',help='Recompute validation-only cached embeddings')
    parser.add_argument('--threads',type=int,choices=range(1,17),default=2)
    args=parser.parse_args()
    try:
        sys.exit(run(args))
    except Exception as exc:
        print(f'VALIDATION ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        sys.exit(2)
