"""Camera face enrollment: pose math, quality gates, diversity, API, atomicity.

No webcam and no TensorFlow: landmarks are projected from a 3D head model with
known yaw/pitch, embeddings come from a deterministic fake. These tests prove
the logic, not that a live webcam works (see tests/face_enrollment_webcam_e2e.py).
"""
from io import BytesIO
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unittest

import numpy as np
from PIL import Image

from backend.app import create_app
from backend.face_recognition.enrollment import (
    EnrollmentSession, assess_frame, compare_with_gallery, estimate_pose, load_config, pose_guidance,
    sharpness_and_brightness)
from tests.test_camera_api import FakeEngine

ROOT = Path(__file__).resolve().parents[1]
CFG = load_config()
MODEL_3D = dict(right_eye=(-165, 170, -135), left_eye=(165, 170, -135), nose=(0, 0, 0),
                mouth_right=(-100, -150, -125), mouth_left=(100, -150, -125))


def project(yaw=0., pitch=0., roll=0., cx=320., cy=230., scale=.55, score=.95):
    """Orthographic projection; yaw>0 = subject's own left, pitch>0 = chin up."""
    a, b, r = map(math.radians, (yaw, pitch, roll))
    face = {}
    for name, (x, y, z) in MODEL_3D.items():
        y, z = y * math.cos(b) + z * math.sin(b), -y * math.sin(b) + z * math.cos(b)
        x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
        u, v = x * scale, -y * scale
        u, v = u * math.cos(r) - v * math.sin(r), u * math.sin(r) + v * math.cos(r)
        face[name] = (cx + u, cy + v)
    half = 200 * scale
    face['box'] = dict(x=cx - half, y=cy - half * 1.2, w=2 * half, h=2.4 * half)
    face['score'] = score
    return face


def good_measure(bgr, box, q):
    return 300.0, 128.0


class PoseMath(unittest.TestCase):
    def test_known_angles_recovered(self):
        for yaw in (-40, -25, -10, 0, 10, 25, 40):
            got = estimate_pose(project(yaw=yaw), CFG['pose_model'])
            self.assertAlmostEqual(got['yaw'], yaw, delta=1.0)
            self.assertAlmostEqual(got['pitch'], 0, delta=1.0)
        for pitch in (-25, -15, 0, 15, 25):
            got = estimate_pose(project(pitch=pitch), CFG['pose_model'])
            self.assertAlmostEqual(got['pitch'], pitch, delta=1.5)
            self.assertAlmostEqual(got['yaw'], 0, delta=1.0)

    def test_roll_invariant_and_combined(self):
        a = estimate_pose(project(yaw=25, roll=15), CFG['pose_model'])
        self.assertAlmostEqual(a['yaw'], 25, delta=1.0)
        self.assertAlmostEqual(a['roll'], 15, delta=1.0)
        b = estimate_pose(project(yaw=20, pitch=15), CFG['pose_model'])
        self.assertAlmostEqual(b['yaw'], 20, delta=3)
        self.assertAlmostEqual(b['pitch'], 15, delta=3)

    def test_guidance_only_accepts_requested_pose(self):
        ranges = CFG['pose_ranges_deg']
        cases = dict(front=(0, 0), left=(30, 0), right=(-30, 0), up=(0, 20), down=(0, -20))
        for pose, (yaw, pitch) in cases.items():
            for other, (y2, p2) in cases.items():
                ok, msg = pose_guidance(pose, y2, p2, ranges)
                self.assertEqual(ok, pose == other, (pose, other, msg))
                self.assertTrue(msg)
        self.assertIn('trái', pose_guidance('left', 5, 0, ranges)[1])
        self.assertIn('quá nhiều', pose_guidance('left', 60, 0, ranges)[1])
        self.assertIn('Ngẩng', pose_guidance('up', 0, 3, ranges)[1])


class Quality(unittest.TestCase):
    frame = np.zeros((480, 640, 3), np.uint8)

    def code(self, faces, measure=good_measure):
        return assess_frame(self.frame, faces, CFG, measure)['code']

    def test_gates(self):
        self.assertEqual(self.code([]), 'NO_FACE')
        self.assertEqual(self.code([project(), project(cx=500)]), 'MULTIPLE_FACES')
        self.assertEqual(self.code([project(score=.3)]), 'NO_FACE')  # below detector threshold
        self.assertEqual(self.code([project(scale=.2)]), 'TOO_SMALL')
        self.assertEqual(self.code([project(scale=1.0)]), 'TOO_CLOSE')
        self.assertEqual(self.code([project(cx=60)]), 'NOT_INSIDE')
        self.assertEqual(self.code([project()], lambda *a: (20., 128.)), 'BLURRY')
        self.assertEqual(self.code([project()], lambda *a: (300., 40.)), 'TOO_DARK')
        self.assertEqual(self.code([project()], lambda *a: (300., 230.)), 'TOO_BRIGHT')
        ok = assess_frame(self.frame, [project(yaw=30)], CFG, good_measure)
        self.assertTrue(ok['ok'])
        self.assertAlmostEqual(ok['pose']['yaw'], 30, delta=1)

    def test_real_sharpness_and_brightness(self):
        rng = np.random.default_rng(1)
        sharp = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
        import cv2
        blurred = cv2.GaussianBlur(sharp, (31, 31), 10)
        box = dict(x=200, y=120, w=220, h=260)
        s1, b1 = sharpness_and_brightness(sharp, box, CFG['quality'])
        s2, _ = sharpness_and_brightness(blurred, box, CFG['quality'])
        self.assertGreater(s1, CFG['quality']['min_laplacian_variance'])
        self.assertLess(s2, CFG['quality']['min_laplacian_variance'])
        self.assertAlmostEqual(b1, 127, delta=5)


def unit(seed):
    v = np.random.default_rng(seed).normal(size=512)
    return v / np.linalg.norm(v)


class SessionRules(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(json.dumps(CFG))
        for p in self.cfg['poses']:
            p['samples'] = 2
        self.s = EnrollmentSession(self.cfg, 'new', {})
        self.t = 100.0

    def frame(self, yaw=0, pitch=0, faces=None):
        self.t += 0.5
        a = assess_frame(np.zeros((480, 640, 3), np.uint8), faces if faces is not None else [project(yaw, pitch)], self.cfg, good_measure)
        return self.s.observe(a, self.t)

    def sample(self, plan, seed):
        return self.s.add_embedding(plan['generation'], plan['pose'], unit(seed), plan['yaw'], plan['pitch'], .9, self.t, 10.)

    def test_full_flow_wrong_pose_interval_and_completion(self):
        angles = dict(front=(0, 0), left=(30, 0), right=(-30, 0), up=(0, 20), down=(0, -20))
        seed = 0
        for pose in ('front', 'left', 'right', 'up', 'down'):
            wrong = self.frame(*angles['left' if pose != 'left' else 'right'])
            self.assertFalse(wrong['request_embedding'])
            for _ in range(2):
                plan = self.frame(*angles[pose])
                self.assertTrue(plan['request_embedding'], plan)
                busy = self.frame(*angles[pose])  # one job in flight only
                self.assertFalse(busy['request_embedding'])
                seed += 1
                self.assertEqual(self.sample(plan, seed), 'ACCEPTED')
        self.assertEqual(self.s.state, 'complete')
        self.assertEqual(len(self.s.vectors()), 10)
        self.assertEqual(self.s.progress()['percent'], 100)

    def test_min_interval(self):
        plan = self.frame()
        self.sample(plan, 1)
        self.t -= 0.4  # next frame only 0.1 s later
        self.assertFalse(self.frame()['request_embedding'])

    def test_duplicates_rejected_same_pose_only(self):
        plan = self.frame()
        self.assertEqual(self.sample(plan, 1), 'ACCEPTED')
        plan = self.frame()
        self.assertEqual(self.s.add_embedding(plan['generation'], 'front', unit(1) + 1e-4 * unit(2), 0, 0, .9, self.t, 1), 'DUPLICATE')
        plan = self.frame()
        near = unit(1) * .95 + unit(3) * .31
        self.assertEqual(self.s.add_embedding(plan['generation'], 'front', near, 0.5, 0, .9, self.t, 1), 'NEAR_DUPLICATE')
        self.assertEqual(len(self.s.samples['front']), 1)

    def test_timeout_keeps_samples_and_retry(self):
        plan = self.frame()
        self.sample(plan, 1)
        for _ in range(200):
            r = self.frame(30, 0)  # wrong pose, face visible: timer runs
            if self.s.state == 'pose_timeout':
                break
        self.assertEqual(self.s.state, 'pose_timeout')
        self.assertFalse(self.frame()['request_embedding'])
        self.assertEqual(len(self.s.samples['front']), 1)
        self.s.retry_pose(self.t)
        self.assertEqual(self.s.state, 'collecting')
        self.assertTrue(self.frame()['request_embedding'])
        self.assertTrue(r['guidance'])

    def test_no_face_pauses_timer(self):
        for _ in range(300):
            self.frame(faces=[])
        self.assertEqual(self.s.state, 'collecting')
        self.assertEqual(self.s.progress()['pose_time_left_s'], self.cfg['collection']['pose_timeout_s'])

    def test_reset_and_stale_results(self):
        plan = self.frame()
        self.s.reset(self.t)
        self.assertEqual(self.sample(plan, 1), 'STALE')
        self.assertEqual(self.s.vectors(), [])

    def test_personal_baseline_from_front(self):
        for seed in (1, 2):
            self.sample(self.frame(0, 8), seed)  # camera below eye level: neutral looks "up" 8 deg
        self.assertAlmostEqual(self.s.baseline['pitch'], 8, delta=1)
        plan = self.frame(30, 8)
        self.assertTrue(plan['request_embedding'])
        self.assertAlmostEqual(plan['pitch'], 0, delta=1)


class GalleryCheck(unittest.TestCase):
    def test_other_student_risk_and_own_consistency(self):
        a, b = unit(1), unit(2)
        records = [dict(student_id='A', embedding=a.tolist()), dict(student_id='B', embedding=b.tolist())]
        new = [a + 0.1 * unit(9), a + 0.1 * unit(8)]
        r = compare_with_gallery(new, records, None, CFG)
        self.assertEqual([x['student'] for x in r['risks']], ['A'])
        self.assertEqual(r['warnings'][0]['code'], 'SIMILAR_TO_OTHER_STUDENT')
        r = compare_with_gallery(new, records, 'B', CFG)
        self.assertEqual({w['code'] for w in r['warnings']}, {'SIMILAR_TO_OTHER_STUDENT', 'DIFFERENT_FROM_EXISTING'})
        r = compare_with_gallery(new, records, 'A', CFG)
        self.assertEqual(r['warnings'], [])


# ------------------------------------------------------------------------- API
def noise_jpeg(size=(640, 480)):
    rng = np.random.default_rng(7)
    data = rng.integers(60, 200, (size[1], size[0], 3), dtype=np.uint8)
    out = BytesIO()
    Image.fromarray(data).save(out, format='JPEG', quality=90)
    return out.getvalue()


class FakeEnrollEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.pose = (0, 0)
        self.faces = None
        self.seed = 100
        self.store = None
        self.refresh_calls = 0
        self.refresh_error = None
        self.validate_error = None
        self.extra_records = []
        self.revision = -1
        self.embedding_calls = 0

    def configure_store(self, store):
        self.store = store

    def enrollment_detect(self, bgr, score_threshold=.6):
        return self.faces if self.faces is not None else [project(*self.pose)]

    def enrollment_embedding(self, bgr, box, collection, cancelled=lambda: False):
        self.embedding_calls += 1
        self.seed += 1
        return unit(self.seed), dict(total_ms=5.0, detection_ms=3.0, arcface_forward_ms=2.0)

    def refresh_gallery(self, wait=0):
        self.refresh_calls += 1
        if self.refresh_error:
            raise self.refresh_error
        self.revision = self.store.gallery_revision()
        return True

    def loaded_gallery_revision(self):
        return self.revision

    def validate_gallery(self, records):
        if self.validate_error:
            raise self.validate_error

    def gallery_records(self):
        records, _ = self.store.gallery_snapshot()
        return records + self.extra_records


class EnrollmentAPI(unittest.TestCase):
    ANGLES = dict(front=(0, 0), left=(30, 0), right=(-30, 0), up=(0, 20), down=(0, -20))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='enrollment-test-', dir=ROOT / '.venv')
        self.addCleanup(self.temp.cleanup)
        tmp = Path(self.temp.name)
        cfg = json.loads(json.dumps(CFG))
        for p in cfg['poses']:
            p['samples'] = 2
        cfg['collection']['min_sample_interval_s'] = 0
        (tmp / 'cfg.json').write_text(json.dumps(cfg), encoding='utf-8')
        self.db = tmp / 'management.sqlite3'
        self.backups = tmp / 'backups'
        self.engine = FakeEnrollEngine()
        self.app = create_app(self.engine, start_models=False, overrides={
            'TESTING': True, 'DATABASE_PATH': self.db, 'ENROLLMENT_CONFIG_PATH': tmp / 'cfg.json',
            'ENROLLMENT_BACKUP_DIR': self.backups})
        self.client = self.app.test_client()
        html = self.client.get('/students/new').get_data(as_text=True)
        self.assertIn('face_enrollment.js', html)
        token = json.loads(re.search(r'<script id="management-config" type="application/json">(.*?)</script>', html).group(1))['csrfToken']
        self.h = {'X-Camera-Token': token}
        self.store = self.app.extensions['management_store']
        self.cls = self.client.post('/api/classes', json=dict(class_code='L1', class_name='Lớp 1'), headers=self.h).json

    def start(self, **body):
        body.setdefault('mode', 'new')
        if body['mode'] == 'new':
            body.setdefault('student_code', 'SV001')
            body.setdefault('full_name', 'Nguyễn Văn A')
            body.setdefault('class_id', self.cls['id'])
        return self.client.post('/api/enrollment/sessions', json=body, headers=self.h)

    def send(self, sid):
        return self.client.post(f'/api/enrollment/sessions/{sid}/frames', data=noise_jpeg(), content_type='image/jpeg', headers=self.h)

    def wait_idle(self, sid):
        for _ in range(200):
            p = self.client.get(f'/api/enrollment/sessions/{sid}').json
            if not p['progress']['pending']:
                return p
            time.sleep(.01)
        self.fail('worker did not finish')

    def collect(self, sid):
        for pose in ('front', 'left', 'right', 'up', 'down'):
            self.engine.pose = self.ANGLES[pose]
            for _ in range(10):
                r = self.send(sid)
                self.assertEqual(r.status_code, 200, r.json)
                p = self.wait_idle(sid)
                if p['progress']['state'] == 'complete' or p['progress']['current_pose'] != pose:
                    break
        p = self.wait_idle(sid)
        self.assertEqual(p['progress']['state'], 'complete')
        for _ in range(100):
            p = self.client.get(f'/api/enrollment/sessions/{sid}').json
            if p['verification'] is not None:
                return p
            time.sleep(.01)
        return p

    def counts(self):
        with sqlite3.connect(self.db) as db:
            return (db.execute('SELECT count(*) FROM students').fetchone()[0],
                    db.execute('SELECT count(*) FROM face_embeddings').fetchone()[0],
                    db.execute("SELECT value FROM management_meta WHERE key='gallery_revision'").fetchone()[0])

    def test_code_checked_at_step_one(self):
        self.client.post('/api/students', json=dict(student_code='SV001', full_name='Đã có'), headers=self.h)
        self.assertFalse(self.client.get('/api/enrollment/code-status?code=sv001').json['available'])
        self.assertTrue(self.client.get('/api/enrollment/code-status?code=SV002').json['available'])
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.start(student_code='SV002', full_name='').status_code, 400)
        self.assertEqual(self.start(student_code='SV002', class_id=999).status_code, 404)

    def test_full_new_student_flow_saves_only_after_confirmation(self):
        before = self.counts()
        sid = self.start().json['session_id']
        p = self.collect(sid)
        self.assertEqual(p['message'], 'Đã thu thập đủ dữ liệu khuôn mặt')
        self.assertEqual(self.counts(), before)  # nothing persisted yet
        # After completion sampling stops: more frames do not call ArcFace.
        calls = self.engine.embedding_calls
        self.send(sid)
        self.assertEqual(self.engine.embedding_calls, calls)
        url = f'/api/enrollment/sessions/{sid}/complete'
        self.assertEqual(self.client.post(url, json={}, headers=self.h).status_code, 400)
        r = self.client.post(url, json=dict(identity_confirmed=True), headers=self.h)
        self.assertEqual(r.status_code, 201, r.json)
        self.assertTrue(r.json['gallery_loaded'])
        self.assertEqual(r.json['embeddings_saved'], 10)
        self.assertEqual(r.json['per_pose'], dict(front=2, left=2, right=2, up=2, down=2))
        self.assertNotIn('"embedding"', json.dumps(r.json))
        self.assertFalse(any(isinstance(v, list) for v in r.json.values()))
        students, faces, revision = self.counts()
        self.assertEqual((students, faces), (before[0] + 1, before[1] + 10))
        self.assertEqual(revision, before[2] + 1)
        self.assertEqual(self.engine.loaded_gallery_revision(), revision)
        new_id = r.json['student_id']
        with sqlite3.connect(self.db) as db:
            rows = db.execute('SELECT student_id,pose_label,image_sha256,embedding_json,enrollment_id FROM face_embeddings').fetchall()
            self.assertEqual({row[0] for row in rows}, {new_id})
            self.assertEqual({row[1] for row in rows}, {'front', 'left', 'right', 'up', 'down'})
            self.assertTrue(all(row[2] is None for row in rows))  # no image hash: no image
            self.assertEqual(len({row[4] for row in rows}), 1)
            for row in rows:
                self.assertAlmostEqual(float(np.linalg.norm(json.loads(row[3]))), 1.0, places=6)
            self.assertEqual(db.execute('SELECT count(*) FROM class_students WHERE student_id=?', (new_id,)).fetchone()[0], 1)
        detail = self.client.get(f"/api/students/{r.json['student_id']}").json
        self.assertEqual(detail['face_count'], 10)
        self.assertEqual(self.client.get(f'/api/enrollment/sessions/{sid}').status_code, 404)
        self.assertEqual(len(list(self.backups.glob('*.sqlite3'))), 1)
        # No frame/image file anywhere in the temp project area.
        self.assertEqual([p for p in Path(self.temp.name).rglob('*') if p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp')], [])

    def test_multiple_faces_and_no_face_pause_without_sampling(self):
        sid = self.start().json['session_id']
        self.engine.faces = [project(), project(cx=520)]
        r = self.send(sid).json
        self.assertEqual(r['quality']['code'], 'MULTIPLE_FACES')
        self.assertFalse(r['sampling'])
        self.engine.faces = []
        r = self.send(sid).json
        self.assertEqual(r['quality']['code'], 'NO_FACE')
        self.assertEqual(self.engine.embedding_calls, 0)

    def test_transaction_failure_leaves_database_untouched(self):
        sid = self.start().json['session_id']
        self.collect(sid)
        before = self.counts()
        self.engine.validate_error = ValueError('bad gallery')
        r = self.client.post(f'/api/enrollment/sessions/{sid}/complete', json=dict(identity_confirmed=True), headers=self.h)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.counts(), before)
        self.assertTrue(self.client.get('/api/enrollment/code-status?code=SV001').json['available'])

    def test_runtime_reload_failure_reverts_commit(self):
        sid = self.start().json['session_id']
        self.collect(sid)
        before = self.counts()
        self.engine.refresh_error = RuntimeError('cannot load')
        r = self.client.post(f'/api/enrollment/sessions/{sid}/complete', json=dict(identity_confirmed=True), headers=self.h)
        self.assertEqual(r.status_code, 500)
        students, faces, _ = self.counts()
        self.assertEqual((students, faces), before[:2])

    def test_similar_face_warning_requires_acknowledgement(self):
        self.engine.extra_records = [dict(student_id='KHAC · Người khác', embedding=unit(s).tolist()) for s in range(101, 111)]
        sid = self.start().json['session_id']
        p = self.collect(sid)
        self.assertEqual(p['verification']['warnings'][0]['code'], 'SIMILAR_TO_OTHER_STUDENT')
        url = f'/api/enrollment/sessions/{sid}/complete'
        self.assertEqual(self.client.post(url, json=dict(identity_confirmed=True), headers=self.h).status_code, 409)
        self.assertEqual(self.client.post(url, json=dict(identity_confirmed=True, acknowledge_warnings=True), headers=self.h).status_code, 201)

    def test_reenroll_replaces_camera_vectors_keeps_legacy_and_needs_confirmation(self):
        original = (ROOT / 'outputs/face_embeddings.json').read_bytes()
        sid = self.start().json['session_id']
        self.collect(sid)
        student_id = self.client.post(f'/api/enrollment/sessions/{sid}/complete', json=dict(identity_confirmed=True), headers=self.h).json['student_id']
        label = self.client.get('/api/legacy-gallery').json['items'][0]
        self.client.post(f'/api/students/{student_id}/legacy-mapping', json=dict(dataset_label=label['dataset_label'], confirmed=True), headers=self.h)
        with sqlite3.connect(self.db) as db:
            legacy_before = db.execute('SELECT count(*) FROM face_embeddings WHERE embedding_reference IS NOT NULL').fetchone()[0]
            old_ids = {r[0] for r in db.execute('SELECT id FROM face_embeddings WHERE embedding_json IS NOT NULL')}
        self.assertEqual(self.start(mode='reenroll', student_id=999).status_code, 404)
        sid = self.start(mode='reenroll', student_id=student_id).json['session_id']
        p = self.collect(sid)
        self.assertIsNotNone(p['verification']['own'])
        url = f'/api/enrollment/sessions/{sid}/complete'
        ack = dict(identity_confirmed=True, acknowledge_warnings=True)
        self.assertEqual(self.client.post(url, json=ack, headers=self.h).status_code, 409)  # no confirm_replace
        with sqlite3.connect(self.db) as db:  # old data kept until confirmed save
            self.assertEqual({r[0] for r in db.execute('SELECT id FROM face_embeddings WHERE embedding_json IS NOT NULL')}, old_ids)
        r = self.client.post(url, json=dict(ack, confirm_replace=True), headers=self.h)
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(r.json['replaced_embeddings'], 10)
        with sqlite3.connect(self.db) as db:
            new_ids = {r[0] for r in db.execute('SELECT id FROM face_embeddings WHERE embedding_json IS NOT NULL')}
            self.assertEqual(len(new_ids), 10)
            self.assertFalse(new_ids & old_ids)
            self.assertEqual(db.execute('SELECT count(*) FROM face_embeddings WHERE embedding_reference IS NOT NULL').fetchone()[0], legacy_before)
        self.assertEqual((ROOT / 'outputs/face_embeddings.json').read_bytes(), original)

    def test_reset_retry_cancel_and_limits(self):
        sid = self.start().json['session_id']
        self.send(sid)
        self.wait_idle(sid)
        r = self.client.post(f'/api/enrollment/sessions/{sid}/reset', headers=self.h).json
        self.assertEqual(r['progress']['collected'], 0)
        self.assertEqual(self.client.post(f'/api/enrollment/sessions/{sid}/retry-pose', headers=self.h).status_code, 200)
        other = self.start(student_code='SV002').json['session_id']
        self.assertEqual(self.start(student_code='SV003').status_code, 429)  # max 2 sessions
        self.client.delete(f'/api/enrollment/sessions/{other}', headers=self.h)
        self.client.delete(f'/api/enrollment/sessions/{sid}', headers=self.h)
        self.assertEqual(self.send(sid).status_code, 404)
        self.assertEqual(self.counts()[0], 0)
        # CSRF token required for writes.
        self.assertEqual(self.client.post('/api/enrollment/sessions', json=dict(mode='new')).status_code, 403)
        bad = self.client.post(f'/api/enrollment/sessions/{self.start(student_code="SV9").json["session_id"]}/frames',
                               data=b'not jpeg', content_type='image/png', headers=self.h)
        self.assertEqual(bad.status_code, 415)

    def test_legacy_photo_upload_disabled_by_default(self):
        sid = self.client.post('/api/students', json=dict(student_code='X1', full_name='Y'), headers=self.h).json['id']
        r = self.client.post(f'/api/students/{sid}/faces', headers=self.h,
                             data=dict(image=(BytesIO(noise_jpeg()), 'a.jpg'), confirmed='true'), content_type='multipart/form-data')
        self.assertEqual(r.status_code, 410)
        page = (ROOT / 'frontend/static/js/management.js').read_text(encoding='utf-8')
        enroll = (ROOT / 'frontend/static/js/face_enrollment.js').read_text(encoding='utf-8')
        for needle in ('type="file"', 'capture-enroll', 'Chụp ảnh', "'/faces'"):
            self.assertNotIn(needle, page)
            self.assertNotIn(needle, enroll)

    def test_engine_not_ready(self):
        self.engine.ready = False
        r = self.start()
        self.assertEqual(r.status_code, 503)


if __name__ == '__main__':
    unittest.main()
