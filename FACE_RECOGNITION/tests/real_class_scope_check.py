"""REAL-model check (RetinaFace + ArcFace + real policy) of class-scoped attendance.

Uses a temporary COPY of outputs/management.sqlite3 and a temporary activation file;
the real database is hashed before/after to prove it is untouched.
Frames are still images (validation photos not in the gallery, and human-labelled
UNKNOWN faces from DATASET/Test) centred in a 640x480 canvas and sent through the
real HTTP routes (/api/detect then /api/recognize), repeated to emulate a person
standing still in front of the webcam. This is NOT a live webcam test.

Run: .venv/Scripts/python.exe tests/real_class_scope_check.py
Writes outputs/class_scope_real_check.json
"""
import hashlib
import json
from io import BytesIO
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import create_app  # noqa: E402
from backend.face_recognition.runtime_service import RecognitionEngine  # noqa: E402

PROFILE = 'retinaface_scaled_experimental'
CLASS_ID = 1  # real class MIS · 50K: DEMO-SV001, DEMO-SV002 (NOT DEMO-SV003/004/005)
FOLDERS = {'DEMO-SV001': 'Bùi Kiều Linh', 'DEMO-SV002': 'Hà Vi', 'DEMO-SV003': 'Nhi', 'DEMO-SV004': 'PThao', 'DEMO-SV005': 'Thu Thảo'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canvas_jpeg(image):
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((640, 480))
    canvas = Image.new('RGB', (640, 480), (40, 40, 40))
    canvas.paste(image, ((640 - image.width) // 2, (480 - image.height) // 2))
    out = BytesIO(); canvas.save(out, format='JPEG', quality=92)
    return out.getvalue()


def unknown_faces(limit=10):
    labels = json.loads((ROOT / 'outputs/test_labeling/labels.json').read_text(encoding='utf-8'))['faces']
    manifest = json.loads((ROOT / 'outputs/test_labeling/manifest.json').read_text(encoding='utf-8'))
    found = []
    for image in manifest['images']:
        for face in image['faces']:
            if labels.get(face['face_id'], {}).get('label') == 'UNKNOWN' and face['box']['w'] >= 120 and face['origin'] == 'retinaface':
                b = face['box']
                with Image.open(ROOT / image['source']) as src:
                    m = int(max(b['w'], b['h']) * .6)
                    crop = src.crop((max(0, b['x'] - m), max(0, b['y'] - m), min(src.width, b['x'] + b['w'] + m), min(src.height, b['y'] + b['h'] + m)))
                    found.append((face['face_id'], canvas_jpeg(crop)))
                if len(found) >= limit:
                    return found
    return found


def main():
    real_db = ROOT / 'outputs/management.sqlite3'
    before = sha(real_db)
    tmp = Path(tempfile.mkdtemp(prefix='class-scope-real-', dir=ROOT / '.venv'))
    db_copy = tmp / 'management.sqlite3'
    with sqlite3.connect(real_db) as s, sqlite3.connect(db_copy) as d:
        s.backup(d)
    engine = RecognitionEngine()
    t0 = time.perf_counter()
    app = create_app(engine, start_models=True, overrides={'DATABASE_PATH': db_copy, 'AUTO_ATTENDANCE_ACTIVATION': tmp / 'activation.json'})
    client = app.test_client()
    while engine.health()['state'] not in ('ready', 'error'):
        time.sleep(1)
    assert engine.state == 'ready', 'model failed to load'
    startup_s = time.perf_counter() - t0
    html = client.get('/attendance').get_data(as_text=True)
    token = json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>', html).group(1))['csrfToken']
    H = {'X-Camera-Token': token}
    status = client.get('/api/attendance/automation-status?profile=' + PROFILE).json
    enable = client.post('/api/attendance/automation-mode', headers=H, json=dict(enabled=True, profile=PROFILE))
    session = client.post('/api/attendance/sessions', headers=H, json=dict(class_id=CLASS_ID, session_name='REAL CHECK (temp copy)', request_key='real-check-' + str(time.time()))).json
    sid = session['id']
    roster = [r['student_code_snapshot'] for r in session['records']]
    service = app.extensions['attendance_service']

    def run(label, data, frames):
        cam = client.post('/api/camera/sessions', headers=H, json=dict(profile=PROFILE, attendance_session_id=sid)).json['session_id']
        hh = dict(H, **{'X-Camera-Session': cam})
        rows = []
        for i in range(frames):
            start = time.perf_counter()
            preview = client.post('/api/detect', data=data, content_type='image/jpeg', headers=hh)
            p_ms = (time.perf_counter() - start) * 1000
            r = client.post('/api/recognize', data=data, content_type='image/jpeg', headers=hh)
            total_ms = (time.perf_counter() - start) * 1000
            body = r.json
            faces = body.get('faces', [])
            text = json.dumps(body, ensure_ascii=False)
            rows.append(dict(frame=i + 1, http=r.status_code, preview_faces=len(preview.json.get('faces', [])),
                             statuses=[f['status'] for f in faces],
                             shown=[(f['student'] or {}).get('student_code') for f in faces],
                             similarity=[f.get('similarity') for f in faces],
                             attendance=[(f.get('attendance') or {}).get('label') for f in faces],
                             auto_changed=(body.get('automatic_attendance') or {}).get('changed'),
                             consistency=[(f.get('verification_consistency') or {}).get('observed') for f in faces],
                             preview_ms=round(p_ms, 1), face_in_to_colour_ms=round(total_ms, 1),
                             leaks_outsider=any(code in text for code in ('DEMO-SV003', 'DEMO-SV004', 'DEMO-SV005', 'Nhi', 'PThao', 'Thu Thảo'))))
        client.delete('/api/camera/sessions/' + cam, headers=H)
        return dict(case=label, frames=rows)

    cases = []
    for code, folder in FOLDERS.items():
        for path in sorted((ROOT / 'DATASET/validation' / folder).glob('*'))[:6]:
            with Image.open(path) as im:
                data = canvas_jpeg(im)
            cases.append(dict(expected='IN_CLASS' if code in roster else 'UNKNOWN', code=code, source=path.relative_to(ROOT).as_posix(),
                              **run(code, data, 8 if code in roster else 3)))
            print(code, path.name, [r['statuses'] for r in cases[-1]['frames']], flush=True)
    for face_id, data in unknown_faces():
        cases.append(dict(expected='UNKNOWN', code='UNREGISTERED', source=face_id, **run('UNREGISTERED', data, 3)))
        print('UNKNOWN', face_id, [r['statuses'] for r in cases[-1]['frames']], flush=True)

    detail = service.detail(sid)
    wrong = [c for c in cases for r in c['frames'] if c['expected'] == 'UNKNOWN' and 'IN_CLASS' in r['statuses']]
    leaks = [c['source'] for c in cases for r in c['frames'] if r['leaks_outsider']]
    first = [c['frames'][0]['face_in_to_colour_ms'] for c in cases if c['frames'][0]['statuses']]
    allms = [r['face_in_to_colour_ms'] for c in cases for r in c['frames'] if r['statuses']]
    in_class = [c for c in cases if c['expected'] == 'IN_CLASS']
    summary = dict(
        note='Real RetinaFace+ArcFace on still images via real HTTP routes, temp SQLite copy. Not a live webcam test.',
        startup_s=round(startup_s, 1), automation_status_before_enable=status, enable_http=enable.status_code,
        enable_state=enable.json.get('state'), class_roster=roster,
        in_class_images=len(in_class),
        in_class_images_green_at_least_once=sum(any('IN_CLASS' in r['statuses'] for r in c['frames']) for c in in_class),
        in_class_images_reaching_stability=sum(any(r['auto_changed'] or (r['attendance'] and r['attendance'][0] == 'Đã điểm danh') for r in c['frames']) for c in in_class),
        outsider_or_unknown_images=len(cases) - len(in_class),
        outsider_or_unknown_shown_green=len(wrong), identity_leaks_in_responses=leaks,
        saved_records=[dict(code=r['student_code_snapshot'], status=r['attendance_status'], source=r['source'], similarity=r['similarity'], margin=r['margin']) for r in detail['records']],
        stats=detail['stats'],
        latency_ms=dict(first_frame_face_in_to_colour_mean=round(statistics.mean(first), 1) if first else None,
                        first_frame_median=round(statistics.median(first), 1) if first else None,
                        all_frames_mean=round(statistics.mean(allms), 1) if allms else None, frames=len(allms)),
        real_database_unchanged=sha(real_db) == before, cases=cases)
    out = ROOT / 'outputs/class_scope_real_check.json'
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'cases'}, ensure_ascii=False, indent=2))
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
