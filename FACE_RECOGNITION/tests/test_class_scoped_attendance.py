"""Class-scoped live attendance: business-rule tests with FIXTURES (pytest/unittest compatible).

What is real here: Flask routes, RecognitionEngine.analyze (global matcher, label->student_id
mapping), the verification policy file backend/attendance_policy.json (unchanged thresholds),
backend classification, continuity tracking, 5-of-7 stability, AttendanceService and SQLite.
What is a fixture: the RetinaFace/ArcFace pipeline returns chosen 512-d vectors and YuNet returns
chosen boxes. These tests are NOT evidence of ArcFace accuracy on real faces; see
tests/real_class_scope_check.py for the real-model check.

Run:  .venv/Scripts/python.exe -m unittest tests.test_class_scoped_attendance -v
 or:  python -m pytest tests/test_class_scoped_attendance.py   (if pytest is installed)
"""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from backend.app import create_app
from backend.database.import_legacy_students import import_students
from backend.face_recognition.runtime_service import RecognitionEngine
from backend.face_recognition.verification_policy import VerificationPolicy, POLICY_PATH

ROOT = Path(__file__).resolve().parents[1]
CALIBRATED = 'retinaface_scaled_experimental'
BOXES = [(40, 120, 140, 170), (250, 120, 140, 170), (460, 120, 140, 170)]


class ScriptedPipeline:
    """Stands in for RetinaFace+ArcFace (analyze) and YuNet (preview boxes)."""
    def __init__(self):
        self.faces = []  # [(box, embedding)]
        self.delay = 0.0
        self.yunet = SimpleNamespace(detect_faces=lambda bgr, dimension=640, score_threshold=.75: [
            SimpleNamespace(x=b[0], y=b[1], w=b[2], h=b[3], confidence=.99) for b, _ in self.faces])

    def run(self, bgr, profile, cancelled):
        if self.delay:
            time.sleep(self.delay)
        return [dict(embedding=e, facial_area=dict(x=b[0], y=b[1], w=b[2], h=b[3])) for b, e in self.faces], {}


def unit(v):
    v = np.asarray(v, dtype=float)
    return (v / np.linalg.norm(v)).tolist()


class ClassScopedAttendance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='class-scope-', dir=ROOT / '.venv')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.config = json.loads((ROOT / 'outputs/face_config.json').read_text(encoding='utf-8'))
        self.db_path = self.folder / 'management.sqlite3'
        self.pipeline = ScriptedPipeline()
        self.engine = self._engine()
        self.app = self._app()
        self.store = self.app.extensions['management_store']
        self.attendance = self.app.extensions['attendance_service']
        rows = import_students(self.store, self.config)['students']
        self.ids = {r['student_code']: r['student_id'] for r in rows}
        self.labels = {code: self.store.gallery_label(sid) for code, sid in self.ids.items()}
        # Class A: SV001, SV002.  Class B: SV001, SV003.  SV004/SV005 are registered but in neither.
        self.class_a = self.store.save('classes', dict(class_code='LOP-A', class_name='Lớp A'))['id']
        self.class_b = self.store.save('classes', dict(class_code='LOP-B', class_name='Lớp B'))['id']
        for code in ('DEMO-SV001', 'DEMO-SV002'): self.store.membership(self.class_a, self.ids[code])
        for code in ('DEMO-SV001', 'DEMO-SV003'): self.store.membership(self.class_b, self.ids[code])
        self._bind_policy()
        rng = np.random.default_rng(7)
        gallery = {}
        for record in self.store.legacy:
            gallery.setdefault(record['student_id'], []).append(np.asarray(record['embedding'], dtype=float))
        self.probe = {}
        with self.store.connection() as db:
            for row in db.execute('SELECT m.dataset_label,s.student_code FROM legacy_mappings m JOIN students s ON s.id=m.student_id'):
                vectors = gallery[row['dataset_label']]
                v = vectors[0] / np.linalg.norm(vectors[0])
                self.probe[row['student_code']] = unit(v + .25 * rng.normal(size=512) / np.sqrt(512))
        self.probe['STRANGER'] = unit(rng.normal(size=512))
        self.probe['AMBIGUOUS'] = unit(np.asarray(self.probe['DEMO-SV001']) + np.asarray(self.probe['DEMO-SV002']))

    # ------------------------------------------------------------------ helpers
    def _engine(self):
        engine = RecognitionEngine()
        engine._gallery_config = self.config
        engine._base_gallery_hash = 'fixture'
        engine._base_policy = None
        engine.pipeline = self.pipeline
        engine.state = 'ready'
        engine.verification_policy = VerificationPolicy.load(POLICY_PATH, self.config)
        return engine

    def _app(self, engine=None):
        app = create_app(engine or self.engine, start_models=False, overrides={
            'TESTING': True, 'DATABASE_PATH': self.db_path,
            'AUTO_ATTENDANCE_ACTIVATION': self.folder / 'automation_activation.json'})
        self.client = app.test_client()
        html = self.client.get('/attendance').get_data(as_text=True)
        token = json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>', html).group(1))['csrfToken']
        self.headers = {'X-Camera-Token': token}
        return app

    def _bind_policy(self, engine=None):
        """Fixture: bind the (unchanged) thresholds to THIS temporary gallery's signature."""
        engine = engine or self.engine
        self.assertTrue(engine.refresh_gallery(wait=5))
        self.assertTrue(engine.verification_policy.valid, engine.verification_policy.reasons)
        engine.verification_policy.calibration_pipeline_signature = engine.matcher.signature

    def begin(self, class_id, key='k1'):
        r = self.client.post('/api/attendance/sessions', headers=self.headers,
                             json=dict(class_id=class_id, session_name='Buổi kiểm thử', session_date='2026-11-16', request_key=key))
        self.assertEqual(r.status_code, 201, r.json)
        return r.json['id']

    def camera(self, attendance_id, profile=CALIBRATED):
        r = self.client.post('/api/camera/sessions', headers=self.headers, json=dict(profile=profile, attendance_session_id=attendance_id))
        self.assertEqual(r.status_code, 200, r.json)
        return r.json['session_id']

    def auto(self, enabled=True, profile=CALIBRATED):
        return self.client.post('/api/attendance/automation-mode', headers=self.headers, json=dict(enabled=enabled, profile=profile))

    @staticmethod
    def image(people):
        canvas = np.full((480, 640, 3), 90, dtype=np.uint8)
        for key, box in people:
            seed = sum(map(ord, key))
            patch_ = np.random.default_rng(seed).integers(0, 255, size=(box[3], box[2], 3), dtype=np.uint8)
            canvas[box[1]:box[1] + box[3], box[0]:box[0] + box[2]] = patch_
        stream = BytesIO()
        Image.fromarray(canvas).save(stream, format='JPEG', quality=95)
        return stream.getvalue()

    def frame(self, cam, people, preview=True):
        """people: [(probe_key, box)]. Runs one YuNet preview then one ArcFace request."""
        self.pipeline.faces = [(box, self.probe[key]) for key, box in people]
        data = self.image(people)
        headers = dict(self.headers, **{'X-Camera-Session': cam})
        if preview:
            self.assertEqual(self.client.post('/api/detect', data=data, content_type='image/jpeg', headers=headers).status_code, 200)
        return self.client.post('/api/recognize', data=data, content_type='image/jpeg', headers=headers)

    def record(self, session_id, code):
        return next(r for r in self.attendance.detail(session_id)['records'] if r['student_id'] == self.ids[code])

    def audit_rows(self):
        with sqlite3.connect(self.db_path) as db:
            return db.execute('SELECT count(*) FROM attendance_auto_audit').fetchone()[0]

    def assert_no_identity(self, response_json, code):
        text = json.dumps(response_json, ensure_ascii=False)
        name = self.labels[code].split(' · ', 1)[1]
        self.assertNotIn(code, text)
        self.assertNotIn(name, text)
        self.assertNotIn('Ngoài lớp', text)

    # ------------------------------------------------------------------ Test 1
    def test_1_in_class_green_then_auto_present_once(self):
        sid = self.begin(self.class_a)
        cam = self.camera(sid)
        # Auto OFF: green with name/code + "Chờ xác nhận"; nothing written.
        for _ in range(8):
            body = self.frame(cam, [('DEMO-SV001', BOXES[0])]).json
        face = body['faces'][0]
        self.assertEqual(face['status'], 'IN_CLASS')
        self.assertEqual(face['student']['student_code'], 'DEMO-SV001')
        self.assertEqual(face['attendance'], dict(status='PENDING', saved=False, label='Chờ xác nhận'))
        self.assertEqual(body['automatic_attendance']['status']['state'], 'off')
        self.assertIsNone(self.record(sid, 'DEMO-SV001')['attendance_status'])
        # Teacher enables auto (real endpoint) -> camera restarts.
        r = self.auto(True)
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['state'], 'on')
        cam = self.camera(sid)
        writes = []
        for i in range(1, 11):
            body = self.frame(cam, [('DEMO-SV001', BOXES[0])]).json
            writes.append(body['automatic_attendance']['changed'])
            face = body['faces'][0]
            self.assertEqual(face['status'], 'IN_CLASS')
            if i < 7:  # stability 5-of-7 not reached yet: never "Đã điểm danh"
                self.assertFalse(face['attendance']['saved'])
                self.assertIsNone(self.record(sid, 'DEMO-SV001')['attendance_status'])
        self.assertEqual(writes.count(True), 1, writes)
        self.assertTrue(writes[6])  # 7th consistent ArcFace result writes
        row = self.record(sid, 'DEMO-SV001')
        self.assertEqual((row['attendance_status'], row['recognition_source'], row['source']), ('PRESENT', 'AI_VERIFIED', 'auto'))
        self.assertIsNotNone(row['confirmed_at'])
        self.assertGreaterEqual(row['similarity'], self.engine.verification_policy.min_similarity)
        self.assertGreaterEqual(row['margin'], self.engine.verification_policy.min_margin)
        self.assertEqual(face['attendance'], dict(status='PRESENT', saved=True, label='Đã điểm danh'))
        stats = self.attendance.detail(sid)['stats']
        self.assertEqual((stats['present'], stats['pending'], stats['total']), (1, 1, 2))
        self.assertEqual(self.audit_rows(), 1)  # no duplicate while the student stays in view

    # ------------------------------------------------------------------ Test 2
    def test_2_registered_but_not_in_class_is_red_unknown(self):
        sid = self.begin(self.class_a)  # class A does NOT contain DEMO-SV003
        self.assertEqual(self.auto(True).status_code, 200)
        cam = self.camera(sid)
        for _ in range(10):
            body = self.frame(cam, [('DEMO-SV003', BOXES[1])]).json
            face = body['faces'][0]
            self.assertEqual(face['status'], 'UNKNOWN')
            self.assertEqual(face['label'], 'Không xác định')
            self.assertIsNone(face['student'])
            self.assert_no_identity(body, 'DEMO-SV003')
            self.assertFalse(body['automatic_attendance']['changed'])
        detail = self.attendance.detail(sid)
        self.assertEqual(detail['stats']['present'], 0)
        self.assertNotIn(self.ids['DEMO-SV003'], [r['student_id'] for r in detail['records']])
        self.assertEqual(self.audit_rows(), 0)
        # Registered in the gallery but in no class at all (DEMO-SV004) -> same.
        body = self.frame(cam, [('DEMO-SV004', BOXES[1])]).json
        self.assertEqual(body['faces'][0]['status'], 'UNKNOWN')
        self.assert_no_identity(body, 'DEMO-SV004')

    # ------------------------------------------------------------------ Test 3
    def test_3_unregistered_person_is_red_unknown(self):
        sid = self.begin(self.class_a)
        self.assertEqual(self.auto(True).status_code, 200)
        cam = self.camera(sid)
        with self.store.connection() as db:
            before = db.execute('SELECT (SELECT count(*) FROM students),(SELECT count(*) FROM face_embeddings),(SELECT count(*) FROM class_students)').fetchone()
        for _ in range(8):
            body = self.frame(cam, [('STRANGER', BOXES[2])]).json
            self.assertEqual(body['faces'][0]['status'], 'UNKNOWN')
            self.assertEqual(body['faces'][0]['label'], 'Không xác định')
            self.assertIsNone(body['faces'][0]['student'])
            self.assertIsNone(body['faces'][0]['similarity'])
        with self.store.connection() as db:
            after = db.execute('SELECT (SELECT count(*) FROM students),(SELECT count(*) FROM face_embeddings),(SELECT count(*) FROM class_students)').fetchone()
        self.assertEqual(tuple(before), tuple(after))  # no profile, embedding or enrollment created
        self.assertEqual(self.attendance.detail(sid)['stats']['present'], 0)

    # ------------------------------------------------------------------ Test 4
    def test_4_low_confidence_or_uncalibrated_candidate_is_unknown(self):
        sid = self.begin(self.class_a)
        self.assertEqual(self.auto(True).status_code, 200)
        cam = self.camera(sid)
        for _ in range(8):  # top-1 is an in-class student but margin is too small
            body = self.frame(cam, [('AMBIGUOUS', BOXES[0])]).json
            self.assertEqual(body['faces'][0]['status'], 'UNKNOWN')
            self.assert_no_identity(body, 'DEMO-SV001')
            self.assert_no_identity(body, 'DEMO-SV002')
        self.assertEqual(self.attendance.detail(sid)['stats']['present'], 0)
        # Genuine in-class face on a profile the thresholds were NOT calibrated for -> unknown.
        cam2 = self.camera(sid, profile='retinaface_reuse')
        body = self.frame(cam2, [('DEMO-SV001', BOXES[0])]).json
        self.assertEqual(body['faces'][0]['status'], 'UNKNOWN')
        self.assertEqual(body['automatic_attendance']['status']['state'], 'locked')
        self.assertIn('camera_profile_not_calibrated', body['automatic_attendance']['status']['blocking_reasons'])

    # ------------------------------------------------------------------ Test 5
    def test_5_switching_class_reverifies_and_ignores_old_session(self):
        self.assertEqual(self.auto(True).status_code, 200)
        sid_b = self.begin(self.class_b, 'kb')
        cam_b = self.camera(sid_b)
        for _ in range(6):  # SV003 belongs to B: green, stability building
            body = self.frame(cam_b, [('DEMO-SV003', BOXES[0])]).json
        self.assertEqual(body['faces'][0]['status'], 'IN_CLASS')
        # Teacher switches to class A: frontend stops the camera (DELETE) and opens a new one.
        self.client.delete('/api/camera/sessions/' + cam_b, headers=self.headers)
        self.assertEqual(self.frame(cam_b, [('DEMO-SV003', BOXES[0])], preview=False).status_code, 409)  # old pipeline is dead
        sid_a = self.begin(self.class_a, 'ka')
        cam_a = self.camera(sid_a)
        for _ in range(8):
            body = self.frame(cam_a, [('DEMO-SV003', BOXES[0])]).json
            self.assertEqual(body['faces'][0]['status'], 'UNKNOWN')
            self.assertEqual(body['attendance_session_id'], sid_a)
            self.assert_no_identity(body, 'DEMO-SV003')
        self.assertIsNone(self.record(sid_b, 'DEMO-SV003')['attendance_status'])  # 6 old frames never written
        self.assertEqual(self.attendance.detail(sid_a)['stats']['present'], 0)
        # Session closed while a camera is still bound to it -> backend refuses.
        self.client.post(f'/api/attendance/sessions/{sid_a}/finish', headers=self.headers, json={})
        self.assertEqual(self.frame(cam_a, [('DEMO-SV001', BOXES[0])], preview=False).status_code, 409)

    # ------------------------------------------------------------------ Test 6 (fault tolerance)
    def test_6a_late_result_after_face_left_is_not_used(self):
        self.assertEqual(self.auto(True).status_code, 200)
        sid = self.begin(self.class_a)
        cam = self.camera(sid)
        for _ in range(6):
            self.frame(cam, [('DEMO-SV001', BOXES[0])])
        self.pipeline.delay = self.engine.verification_policy.track_max_gap_s + .3  # no preview during inference
        body = self.frame(cam, [('DEMO-SV001', BOXES[0])]).json
        self.pipeline.delay = 0
        self.assertEqual(body['faces'][0]['status'], 'PROCESSING')
        self.assertIsNone(body['faces'][0]['student'])
        self.assertFalse(body['automatic_attendance']['changed'])
        self.assertIsNone(self.record(sid, 'DEMO-SV001')['attendance_status'])

    def test_6b_sqlite_failure_reports_error_and_does_not_claim_saved(self):
        self.assertEqual(self.auto(True).status_code, 200)
        sid = self.begin(self.class_a)
        cam = self.camera(sid)
        with patch.object(self.attendance, 'confirm_auto', side_effect=sqlite3.OperationalError('disk I/O error')):
            for _ in range(8):
                body = self.frame(cam, [('DEMO-SV001', BOXES[0])]).json
        self.assertIn('database_write_failed', body['automatic_attendance']['errors'])
        self.assertFalse(body['faces'][0]['attendance']['saved'])
        self.assertEqual(body['faces'][0]['attendance']['label'], 'Chờ xác nhận')
        self.assertNotIn('session', body['automatic_attendance'])
        self.assertEqual(self.attendance.detail(sid)['stats']['present'], 0)
        with patch.object(self.attendance, 'context', side_effect=sqlite3.OperationalError('locked')):
            r = self.frame(cam, [('DEMO-SV001', BOXES[0])], preview=False)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json['error']['code'], 'DATABASE_UNAVAILABLE')
        # Recovery: next frames write exactly once.
        for _ in range(3):
            body = self.frame(cam, [('DEMO-SV001', BOXES[0])]).json
        self.assertEqual(self.record(sid, 'DEMO-SV001')['attendance_status'], 'PRESENT')
        self.assertEqual(self.audit_rows(), 1)

    def test_6c_multiple_faces_each_classified_independently(self):
        self.assertEqual(self.auto(True).status_code, 200)
        sid = self.begin(self.class_a)
        cam = self.camera(sid)
        people = [('DEMO-SV001', BOXES[0]), ('DEMO-SV003', BOXES[1]), ('STRANGER', BOXES[2])]
        for _ in range(8):
            body = self.frame(cam, people).json
        statuses = [f['status'] for f in sorted(body['faces'], key=lambda f: f['box']['x'])]
        self.assertEqual(statuses, ['IN_CLASS', 'UNKNOWN', 'UNKNOWN'])
        self.assert_no_identity(body, 'DEMO-SV003')
        detail = self.attendance.detail(sid)
        self.assertEqual(detail['stats']['present'], 1)
        self.assertEqual(self.record(sid, 'DEMO-SV001')['attendance_status'], 'PRESENT')

    def test_6d_person_swap_does_not_inherit_identity_or_stability(self):
        self.assertEqual(self.auto(True).status_code, 200)
        sid = self.begin(self.class_a)
        cam = self.camera(sid)
        for _ in range(6):
            self.frame(cam, [('DEMO-SV001', BOXES[0])])
        # A different person steps into the same spot: new track, no green carried over.
        body = self.frame(cam, [('DEMO-SV003', BOXES[0])]).json
        self.assertNotEqual(body['faces'][0]['status'], 'IN_CLASS')
        self.assertIsNone(body['faces'][0]['student'])
        # Swap to SV002 (in class): must rebuild 7 results of its own before any write.
        for i in range(1, 8):
            body = self.frame(cam, [('DEMO-SV002', BOXES[0])]).json
            if i < 7:
                self.assertFalse(body['automatic_attendance']['changed'], i)
        self.assertEqual(self.record(sid, 'DEMO-SV002')['attendance_status'], 'PRESENT')
        self.assertIsNone(self.record(sid, 'DEMO-SV001')['attendance_status'])

    def test_6e_camera_restart_and_flask_restart_keep_data_no_duplicates(self):
        self.assertEqual(self.auto(True).status_code, 200)
        sid = self.begin(self.class_a)
        cam = self.camera(sid)
        for _ in range(7):
            self.frame(cam, [('DEMO-SV001', BOXES[0])])
        self.assertEqual(self.audit_rows(), 1)
        self.client.delete('/api/camera/sessions/' + cam, headers=self.headers)  # camera off
        cam = self.camera(sid)                                                    # camera on
        for _ in range(8):
            body = self.frame(cam, [('DEMO-SV001', BOXES[0])]).json
        self.assertEqual(body['faces'][0]['attendance']['label'], 'Đã điểm danh')
        # "Restart Flask": brand-new engine + app on the same SQLite and activation file.
        self.pipeline = ScriptedPipeline()
        engine = self._engine()
        self.engine = engine
        self.app = self._app(engine)
        self.attendance = self.app.extensions['attendance_service']
        self._bind_policy(engine)
        self.assertEqual(self.record(sid, 'DEMO-SV001')['attendance_status'], 'PRESENT')
        cam = self.camera(sid)
        for _ in range(8):
            body = self.frame(cam, [('DEMO-SV001', BOXES[0])]).json
        self.assertTrue(body['faces'][0]['attendance']['saved'])
        self.assertEqual(self.audit_rows(), 1)

    def test_6f_concurrent_writes_and_manual_status_never_overwritten(self):
        sid = self.begin(self.class_a)
        sv1, sv2 = self.ids['DEMO-SV001'], self.ids['DEMO-SV002']
        barrier = threading.Barrier(8)
        def write(_):
            barrier.wait()
            return self.attendance.confirm_auto(sid, sv1, .9, .3, lambda: True)['changed']
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(write, range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.audit_rows(), 1)
        # Teacher confirms SV002 manually; a later AI write must not touch it.
        r = self.client.post(f'/api/attendance/sessions/{sid}/confirm', headers=self.headers,
                             json=dict(student_id=sv2, confirmed=True, recognition_source='MANUAL'))
        self.assertEqual(r.status_code, 200)
        out = self.attendance.confirm_auto(sid, sv2, .9, .3, lambda: True)
        self.assertEqual((out['changed'], out['reason']), (False, 'already_recorded'))
        self.assertEqual(self.record(sid, 'DEMO-SV002')['recognition_source'], 'MANUAL')

    # ------------------------------------------------------------------ Test 7 (security)
    def test_7_backend_rejects_direct_writes_for_outsiders_and_closed_sessions(self):
        sid = self.begin(self.class_a)
        outsider = self.ids['DEMO-SV003']
        url = f'/api/attendance/sessions/{sid}/confirm'
        self.assertEqual(self.client.post(url, headers=self.headers, json=dict(student_id=outsider, confirmed=True, recognition_source='MANUAL')).status_code, 422)
        self.assertEqual(self.client.post(url, headers=self.headers, json=dict(student_id=self.ids['DEMO-SV001'], confirmed=True, recognition_source='AI_VERIFIED')).status_code, 400)
        self.assertEqual(self.client.post(url, json=dict(student_id=self.ids['DEMO-SV001'], confirmed=True)).status_code, 403)  # no CSRF token
        self.assertEqual(self.attendance.confirm_auto(sid, outsider, .99, .5, lambda: True)['reason'], 'student_not_in_class')
        # The old client-driven commit endpoint no longer exists.
        cam = self.camera(sid)
        self.assertIn(self.client.post(f'/api/camera/sessions/{cam}/attendance/commit', headers=self.headers, json=dict(grant_ids=['x'])).status_code, (404, 405))
        # Client cannot inject a status/label: /api/recognize accepts only JPEG bytes.
        r = self.client.post('/api/recognize', headers=dict(self.headers, **{'X-Camera-Session': cam}),
                             json=dict(faces=[dict(status='IN_CLASS', student_id=outsider)]))
        self.assertEqual(r.status_code, 415)
        self.client.post(f'/api/attendance/sessions/{sid}/finish', headers=self.headers, json={})
        self.assertEqual(self.client.post(url, headers=self.headers, json=dict(student_id=self.ids['DEMO-SV001'], confirmed=True, recognition_source='MANUAL')).status_code, 409)
        self.assertEqual(self.attendance.confirm_auto(sid, self.ids['DEMO-SV001'], .99, .5, lambda: True)['reason'], 'session_closed')
        # A camera session cannot be created for a closed attendance session.
        self.assertEqual(self.client.post('/api/camera/sessions', headers=self.headers, json=dict(profile=CALIBRATED, attendance_session_id=sid)).status_code, 409)

    def test_7b_auto_cannot_be_enabled_when_locked(self):
        self.engine.verification_policy.calibration_pipeline_signature = 'other-gallery'
        r = self.auto(True)
        self.assertEqual(r.status_code, 409)
        self.assertIn('gallery_changed_since_calibration', r.json['status']['blocking_reasons'])
        self.assertEqual(self.auto(True, profile='retinaface_reuse').status_code, 409)
        self._bind_policy()
        self.assertEqual(self.auto(True).status_code, 200)
        # Policy file changed after enabling (sha differs) -> activation is outdated -> locked.
        self.engine.verification_policy.sha256 = 'changed'
        status = self.client.get('/api/attendance/automation-status?profile=' + CALIBRATED).json
        self.assertEqual(status['state'], 'locked')
        self.assertIn('activation_outdated', status['blocking_reasons'])

    def test_no_session_page_never_writes(self):
        cam = self.client.post('/api/camera/sessions', headers=self.headers, json=dict(profile=CALIBRATED)).json['session_id']
        body = self.frame(cam, [('DEMO-SV001', BOXES[0]), ('STRANGER', BOXES[1])]).json
        self.assertEqual(sorted(f['status'] for f in body['faces']), ['UNKNOWN', 'VERIFIED'])
        self.assertNotIn('automatic_attendance', body)


if __name__ == '__main__':
    unittest.main()
