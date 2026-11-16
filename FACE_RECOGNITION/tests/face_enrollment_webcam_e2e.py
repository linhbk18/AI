"""Live webcam check AFTER enrolling a student through /students/new.

Opens a NEW webcam session (OpenCV), sends frames to the running Flask app's
existing recognition endpoint and reports who the gallery's top-1 is, with
latency. Frames are never written to disk. Run once with the enrolled student
in front of the camera (--expect "CODE · Name"), and once with a DIFFERENT
person (--expect-not "CODE · Name").

  .venv/Scripts/python.exe tests/face_enrollment_webcam_e2e.py --expect "SV001 · Nguyễn Văn A" --seconds 20
  .venv/Scripts/python.exe tests/face_enrollment_webcam_e2e.py --expect-not "SV001 · Nguyễn Văn A" --seconds 20

Note: identity is reported as the top-1 candidate + similarity. Whether that is
"recognised" for attendance still depends on the calibrated policy, which is
not changed by enrollment.
"""
import argparse
from collections import Counter
import json
import re
import statistics
import time
import urllib.request

import cv2

BASE = 'http://127.0.0.1:5055'


def call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(BASE + url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode('utf-8'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--expect')
    ap.add_argument('--expect-not')
    ap.add_argument('--seconds', type=float, default=20)
    ap.add_argument('--camera', type=int, default=0)
    ap.add_argument('--profile', default='retinaface_reuse')
    args = ap.parse_args()
    html = urllib.request.urlopen(BASE + '/', timeout=30).read().decode('utf-8')
    token = json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>', html).group(1))['csrfToken']
    h = {'X-Camera-Token': token, 'Content-Type': 'application/json'}
    sid = call('/api/camera/sessions', json.dumps(dict(profile=args.profile)).encode(), h, 'POST')['session_id']
    cam = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    tops, sims, latency = Counter(), [], []
    end = time.monotonic() + args.seconds
    try:
        if not cam.isOpened():
            raise SystemExit('Không mở được webcam.')
        while time.monotonic() < end:
            ok, frame = cam.read()
            if not ok:
                continue
            jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
            t = time.perf_counter()
            try:
                r = call('/api/recognize', jpg, {'X-Camera-Token': token, 'X-Camera-Session': sid, 'Content-Type': 'image/jpeg'}, 'POST')
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    continue
                raise
            latency.append((time.perf_counter() - t) * 1000)
            for f in r.get('faces', []):
                tops[f['top1']['candidate']] += 1
                sims.append((f['top1']['candidate'], f['top1']['similarity'], f.get('margin')))
    finally:
        cam.release()
        call(f'/api/camera/sessions/{sid}', None, {'X-Camera-Token': token}, 'DELETE')
    total = sum(tops.values())
    report = dict(frames_with_face=total, top1_counts=dict(tops),
                  latency_ms=dict(mean=round(statistics.mean(latency), 1), max=round(max(latency), 1)) if latency else None)
    if args.expect:
        mine = [s for c, s, _ in sims if c == args.expect]
        report['expected_top1_rate'] = round(len(mine) / total, 3) if total else None
        report['expected_similarity_median'] = round(statistics.median(mine), 3) if mine else None
    if args.expect_not:
        report['wrongly_top1_rate'] = round(tops.get(args.expect_not, 0) / total, 3) if total else None
        wrong = [s for c, s, _ in sims if c == args.expect_not]
        report['wrong_similarity_max'] = round(max(wrong), 3) if wrong else None
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
