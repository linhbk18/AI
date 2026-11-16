"""Read-only face-count audit. No recognition model or embedding is created.

OpenCV YuNet detector is a baseline: its misses/false positives
require human review. Only exact-one-face images enter the candidate manifest.
"""
import csv
from collections import Counter, defaultdict
import json
import os
import sys

from convert_images import (ROOT, DATASET, OUTPUTS, MAPPING, protect_originals,
                            sha256, verify_originals, write_csv)


def audit():
    # Keep any DeepFace cache inside ignored project outputs, not the user profile.
    os.environ.setdefault('DEEPFACE_HOME', str(OUTPUTS))
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    model = OUTPUTS / 'models' / 'face_detection_yunet_2023mar.onnx'
    expected = '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'
    if not model.exists() or sha256(model) != expected:
        raise RuntimeError(f'Missing or unexpected official OpenCV YuNet model: {model}')
    detector = cv2.FaceDetectorYN.create(str(model), '', (320, 320), 0.9, 0.3, 5000)
    config = dict(detector='OpenCV YuNet 2023mar', model=str(model), sha256=expected,
                  score_threshold=0.9, nms_threshold=0.3, image_dimensions=[320, 640, 1600],
                  merge='NMS across scales at IoU 0.3; boxes mapped to original coordinates',
                  source='https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet',
                  note='Face detection only; ArcFace not loaded. Manual review still required.')
    (OUTPUTS / 'face_detection_config.json').write_text(
        json.dumps(config, indent=2), encoding='utf-8')

    before = protect_originals()
    mapping = {}
    if MAPPING.exists():
        with MAPPING.open(encoding='utf-8-sig', newline='') as stream:
            mapping = {r['source']: r for r in csv.DictReader(stream)}
    rows = []
    counts = defaultdict(Counter)
    try:
        for split in ('registered', 'validation'):
            folder = DATASET / split
            if not folder.is_dir():
                raise FileNotFoundError(folder)
            for source in sorted(p for p in folder.rglob('*') if p.is_file()):
                rel = source.relative_to(folder)
                student = rel.parts[0] if len(rel.parts) > 1 else '(unassigned)'
                key = source.relative_to(ROOT).as_posix()
                actual = source
                row = dict(split=split, student=student, source=key, image_path='',
                           status='processing_error', face_count='', width='', height='',
                           blur_laplacian_variance='', detector='yunet', boxes='[]', error='')
                try:
                    with source.open('rb') as stream:
                        header = stream.read(64)
                    if header[4:8] == b'ftyp':
                        conversion = mapping.get(key)
                        if not conversion or conversion['status'] != 'ok':
                            raise ValueError('HEIC conversion missing or failed')
                        actual = ROOT / conversion['converted']
                        if (sha256(source) != conversion['source_sha256'] or
                                sha256(actual) != conversion['converted_sha256']):
                            raise ValueError('Conversion checksum mismatch')
                    row['image_path'] = str(actual.resolve())
                    with Image.open(actual) as image:
                        rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
                    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                    row.update(width=bgr.shape[1], height=bgr.shape[0],
                               blur_laplacian_variance=round(float(cv2.Laplacian(
                                   cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()), 3))
                    proposals, scores = [], []
                    for dimension in (320, 640, 1600):
                        scale = min(1.0, dimension / max(bgr.shape[:2]))
                        size = (max(1, round(bgr.shape[1] * scale)),
                                max(1, round(bgr.shape[0] * scale)))
                        resized = cv2.resize(bgr, size)
                        detector.setInputSize(size)
                        _, faces = detector.detect(resized)
                        if faces is not None:
                            # Actual x/y scale includes rounding of resized dimensions.
                            sx, sy = size[0] / bgr.shape[1], size[1] / bgr.shape[0]
                            for face in faces:
                                proposals.append([round(float(face[0]) / sx),
                                                  round(float(face[1]) / sy),
                                                  round(float(face[2]) / sx),
                                                  round(float(face[3]) / sy)])
                                scores.append(float(face[-1]))
                    keep = cv2.dnn.NMSBoxes(proposals, scores, 0.9, 0.3) if proposals else []
                    boxes = []
                    for index in np.asarray(keep).reshape(-1):
                        x, y, w, h = proposals[int(index)]
                        boxes.append(dict(x=x, y=y, w=w, h=h,
                                          confidence=round(scores[int(index)], 6)))
                    count = len(boxes)
                    row.update(face_count=count, status=('one_face' if count == 1 else
                               'no_face' if count == 0 else 'multiple_faces'),
                               boxes=json.dumps(boxes))
                except Exception as exc:
                    row['error'] = f'{type(exc).__name__}: {exc}'
                rows.append(row)
                counts[(split, student)][row['status']] += 1
                print(f"{len(rows):3} | {split:10} | {student:14} | {source.name:22} | {row['status']}", flush=True)
        fields = ['split', 'student', 'source', 'image_path', 'status', 'face_count',
                  'width', 'height', 'blur_laplacian_variance', 'detector', 'boxes', 'error']
        write_csv(OUTPUTS / 'dataset_quality_report.csv', fields, rows)
        candidates = [dict(split=r['split'], student=r['student'], source=r['source'],
                           image_path=r['image_path'], preprocessing='EXIF transpose, RGB')
                      for r in rows if r['status'] == 'one_face']
        write_csv(OUTPUTS / 'valid_image_paths.csv',
                  ['split', 'student', 'source', 'image_path', 'preprocessing'], candidates)
        write_csv(OUTPUTS / 'arcface_registered_manifest.csv',
                  ['split', 'student', 'source', 'image_path', 'preprocessing'],
                  [r for r in candidates if r['split'] == 'registered'])
        print('\nSplit      | Student        | One | None | Multi | Error')
        for (split, student), c in sorted(counts.items()):
            print(f"{split:10} | {student:14} | {c['one_face']:3} | {c['no_face']:4} | {c['multiple_faces']:5} | {c['processing_error']:5}")
        enrolled = {p.name for p in (DATASET / 'registered').iterdir() if p.is_dir()}
        ready = {r['student'] for r in candidates if r['split'] == 'registered'}
        print('Registered identities with candidates:', ', '.join(sorted(ready)))
        print('Missing identities:', ', '.join(sorted(enrolled - ready)) or 'none')
        print('Candidate manifest:', OUTPUTS / 'valid_image_paths.csv')
        print('ArcFace enrollment manifest (registered only):', OUTPUTS / 'arcface_registered_manifest.csv')
        print('Manual review required for detector misses, multiple faces, and visual quality.')
    finally:
        verify_originals(before)
    return 1 if any(r['status'] == 'processing_error' for r in rows) else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(audit())
