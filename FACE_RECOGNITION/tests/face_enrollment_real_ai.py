"""Real-AI check for camera enrollment (no webcam, no DB writes, no images/vectors saved).

1. Equivalence: the face-centred crop pipeline used by camera enrollment vs the
   92 stored gallery vectors (built by DeepFace.represent on the full photo).
2. Timing of YuNet quality/pose and of crop RetinaFace+ArcFace embeddings.
3. Pose estimates on real registered/validation photos (mostly frontal).

Usage: .venv/Scripts/python.exe tests/face_enrollment_real_ai.py [--per-student N] [--limits 0,400,320]
Writes only aggregate numbers to outputs/face_enrollment/real_ai_check.json.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.face_recognition.runtime_service import RecognitionEngine  # noqa: E402
from backend.face_recognition.enrollment import load_config, assess_frame  # noqa: E402


def read_bgr(path):
    import cv2
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def summary(values):
    if not values:
        return None
    v = sorted(values)
    return dict(n=len(v), mean=round(statistics.mean(v), 4), median=round(statistics.median(v), 4),
                min=round(v[0], 4), max=round(v[-1], 4), p95=round(v[min(len(v) - 1, int(.95 * len(v)))], 4))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-student', type=int, default=4)
    ap.add_argument('--limits', default='0,400,320')
    args = ap.parse_args()
    cfg = load_config()
    mapping = {}
    with (ROOT / 'outputs/conversion_mapping.csv').open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if row['status'] == 'ok':
                mapping[row['source']] = ROOT / row['converted']
    gallery = json.loads((ROOT / 'outputs/face_embeddings.json').read_text(encoding='utf-8'))

    engine = RecognitionEngine()
    t0 = time.perf_counter()
    engine.start_loading()
    while engine.state == 'loading':
        time.sleep(1)
    if engine.state != 'ready':
        raise SystemExit('Engine failed to load; see log')
    load_s = time.perf_counter() - t0
    print(f'engine ready in {load_s:.1f}s', flush=True)

    limits = [int(x) for x in args.limits.split(',')]
    per = {}
    chosen = []
    for r in gallery:
        if per.get(r['student_id'], 0) < args.per_student:
            per[r['student_id']] = per.get(r['student_id'], 0) + 1
            chosen.append(r)
    results = {lim: dict(cosine=[], total_ms=[], detection_ms=[], arcface_ms=[], failures=0) for lim in limits}
    yunet_ms, poses, quality_codes = [], [], {}
    for i, r in enumerate(chosen):
        path = mapping.get(r['source'], ROOT / r['source'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != r['image_sha256']:
            print('hash mismatch, skipped', i, flush=True)
            continue
        bgr = read_bgr(path)
        s = time.perf_counter()
        faces = engine.enrollment_detect(bgr, cfg['quality']['detector_score_threshold'])
        yunet_ms.append((time.perf_counter() - s) * 1000)
        a = assess_frame(bgr, faces, cfg)
        quality_codes[a['code']] = quality_codes.get(a['code'], 0) + 1
        if a.get('pose'):
            poses.append(dict(yaw=a['pose']['yaw'], pitch=a['pose']['pitch'], nose_ratio=a['pose']['nose_ratio'],
                              yaw_ratio=a['pose']['yaw_ratio']))
        if len(faces) != 1:
            for lim in limits:
                results[lim]['failures'] += 1
            continue
        stored = np.asarray(r['embedding'], float)
        stored /= np.linalg.norm(stored)
        for lim in limits:
            coll = dict(cfg['collection'], crop_max_dimension=lim or None)
            try:
                vec, timings = engine.enrollment_embedding(bgr, faces[0]['box'], coll)
            except Exception as exc:  # report, keep going
                print('embed failed', i, lim, exc, flush=True)
                results[lim]['failures'] += 1
                continue
            results[lim]['cosine'].append(float(vec @ stored))
            results[lim]['total_ms'].append(timings['total_ms'])
            results[lim]['detection_ms'].append(timings['detection_ms'])
            results[lim]['arcface_ms'].append(timings['arcface_forward_ms'])
        print(i + 1, '/', len(chosen), {lim: round(results[lim]['cosine'][-1], 4) if results[lim]['cosine'] else None for lim in limits}, flush=True)

    # Validation photos (not in gallery): pose and quality distribution only.
    val_poses = []
    for path in sorted((ROOT / 'DATASET/validation').rglob('*.jpg'))[:40]:
        bgr = read_bgr(path)
        if bgr is None:
            continue
        a = assess_frame(bgr, engine.enrollment_detect(bgr, .6), cfg)
        if a.get('pose'):
            val_poses.append(dict(yaw=a['pose']['yaw'], pitch=a['pose']['pitch'], nose_ratio=a['pose']['nose_ratio']))

    report = dict(
        engine_load_s=round(load_s, 1), images=len(chosen),
        yunet_ms=summary(yunet_ms), quality_codes_on_photos=quality_codes,
        crop_pipeline={str(lim or 'none'): dict(cosine_vs_stored_gallery=summary(v['cosine']), total_ms=summary(v['total_ms']),
                       detection_ms=summary(v['detection_ms']), arcface_forward_ms=summary(v['arcface_ms']), failures=v['failures'])
                       for lim, v in results.items()},
        registered_pose=dict(yaw=summary([p['yaw'] for p in poses]), pitch=summary([p['pitch'] for p in poses]),
                             nose_ratio=summary([p['nose_ratio'] for p in poses]), yaw_ratio=summary([p['yaw_ratio'] for p in poses])),
        validation_pose=dict(yaw=summary([p['yaw'] for p in val_poses]), pitch=summary([p['pitch'] for p in val_poses]),
                             nose_ratio=summary([p['nose_ratio'] for p in val_poses])),
        note='Ảnh chân dung tĩnh, không phải webcam. Không lưu ảnh hay vector.')
    out = ROOT / 'outputs/face_enrollment'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'real_ai_check.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
