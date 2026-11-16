"""Contract/concurrency tests without opening a device or loading TensorFlow."""
import json
from io import BytesIO
import re
import threading
import tempfile
from pathlib import Path
import unittest

from PIL import Image

from backend.app import create_app
from backend.face_recognition.runtime_service import EngineBusy, SessionStopped


def jpeg(size=(320, 180)):
    stream = BytesIO()
    Image.new('RGB', size, '#253147').save(stream, format='JPEG')
    return stream.getvalue()


class FakeEngine:
    def __init__(self):
        self.ready = True
        self.result = dict(faces=[], frame=dict(width=320, height=180), processing_ms=1.,
                          detected_faces=0, face_errors=0, identity_confirmation_enabled=False,
                          attendance_enabled=False, threshold_enabled=False)
        self.calls = 0
        self.failure = None
        self.last_kwargs={}
    def health(self):
        return dict(ready=self.ready, state='ready' if self.ready else 'loading',
                    model_name='ArcFace', detector_backend='retinaface', registered_embeddings=92,
                    registered_students=5, identity_confirmation_enabled=False, attendance_enabled=False)
    def analyze(self, bgr, cancelled,profile='retinaface_reuse',**kwargs):
        self.calls += 1
        self.last_kwargs=kwargs
        if self.failure:
            raise self.failure
        if cancelled():
            raise SessionStopped()
        return self.result


class CameraAPIContract(unittest.TestCase):
    def setUp(self):
        self.database_dir=tempfile.TemporaryDirectory(prefix='camera-api-',dir=Path(__file__).resolve().parents[1]/'.venv')
        self.addCleanup(self.database_dir.cleanup)
        self.engine = FakeEngine()
        self.app = create_app(self.engine, start_models=False, overrides={'TESTING': True,'DATABASE_PATH':Path(self.database_dir.name)/'test.sqlite3'})
        self.client = self.app.test_client()
        html = self.client.get('/').get_data(as_text=True)
        cfg = json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>', html).group(1))
        self.headers = {'X-Camera-Token': cfg['csrfToken']}
        self.sid = self.client.post('/api/camera/sessions', headers=self.headers).json['session_id']
        self.headers['X-Camera-Session'] = self.sid
    def post(self, payload=None, **kwargs):
        return self.client.post('/api/recognize', data=jpeg() if payload is None else payload,
                                content_type='image/jpeg', headers=self.headers, **kwargs)
    def test_automatic_activation_cannot_bypass_missing_policy(self):
        response=self.client.post('/api/attendance/automation-mode',headers=self.headers,
            json=dict(enabled=True,profile='retinaface_scaled_experimental'))
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json['error']['code'],'AUTOMATION_LOCKED')
        self.assertFalse(response.json['status']['can_enable'])
        for body in ({'enabled':'true'},{'enabled':True,'profile':[]}):
            self.assertEqual(self.client.post('/api/attendance/automation-mode',headers=self.headers,json=body).status_code,400)
    def test_page_assets_and_no_biometric_file_routes(self):
        for path in ('/', '/static/css/style.css', '/static/js/camera.js'):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
        for path in ('/outputs/face_embeddings.json', '/DATASET/Test/3.jpg', '/api/attendance'):
            self.assertEqual(self.client.get(path).status_code, 404)
    def test_no_face_clears_identity_contract(self):
        result = self.post()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['faces'], [])
        self.assertFalse(result.json['attendance_enabled'])
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.assertIn('serialization_ms;dur=',result.headers['Server-Timing'])
        self.assertIn('decode_ms',result.json['timings_ms'])
    def test_explicit_profile_configuration(self):
        response=self.client.post('/api/camera/sessions',headers=self.headers,json={'profile':'yunet_recall_experimental'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['profile'],'yunet_recall_experimental')
        self.assertEqual(self.client.post('/api/camera/sessions',headers=self.headers,json={'profile':'yunet_experimental'}).status_code,400)
        self.assertEqual(self.client.post('/api/camera/sessions',headers=self.headers,json={'profile':'unknown'}).status_code,400)
        self.assertEqual(self.client.post('/api/camera/sessions',headers=self.headers,json=[1]).status_code,400)
    def test_attendance_camera_requires_matching_class_scope(self):
        from backend.attendance.attendance_service import AttendanceService
        store=self.app.extensions['management_store']
        classroom=store.save('classes',dict(class_code='SCOPE',class_name='Scope test'))
        student=store.save('students',dict(student_code='SCOPE-1',full_name='Test Student'))
        store.membership(classroom['id'],student['id'])
        attendance=self.app.extensions['attendance_service'].create(dict(class_id=classroom['id'],session_name='Scope',session_date='2026-11-16',request_key='scope-test'))
        wrong=self.client.post('/api/camera/sessions',headers=self.headers,json={'attendance_session_id':attendance['id'],'class_id':classroom['id']+1})
        self.assertEqual(wrong.status_code,409)
        camera=self.client.post('/api/camera/sessions',headers=self.headers,json={'attendance_session_id':attendance['id'],'class_id':classroom['id']}).json['session_id']
        headers=dict(self.headers,**{'X-Camera-Session':camera})
        self.assertEqual(self.client.post('/api/recognize',data=jpeg(),content_type='image/jpeg',headers=headers).status_code,409)
        headers['X-Class-Id']=str(classroom['id']+1)
        self.assertEqual(self.client.post('/api/recognize',data=jpeg(),content_type='image/jpeg',headers=headers).status_code,409)
        headers['X-Class-Id']=str(classroom['id'])
        self.assertEqual(self.client.post('/api/recognize',data=jpeg(),content_type='image/jpeg',headers=headers).status_code,200)
        standalone=self.client.post('/api/camera/sessions',headers=self.headers,json={'class_id':classroom['id']})
        self.assertEqual(standalone.status_code,200)
        wrong_header=dict(self.headers,**{'X-Camera-Session':standalone.json['session_id'],'X-Class-Id':str(classroom['id']+1)})
        self.assertEqual(self.client.post('/api/recognize',data=jpeg(),content_type='image/jpeg',headers=wrong_header).status_code,409)
        no_header=dict(self.headers,**{'X-Camera-Session':standalone.json['session_id']})
        self.assertEqual(self.client.post('/api/recognize?class_id='+str(classroom['id']),data=jpeg(),content_type='image/jpeg',headers=no_header).status_code,200)
        self.assertEqual(self.engine.last_kwargs['class_id'],classroom['id'])
        self.assertEqual(self.engine.last_kwargs['allowed_student_ids'],(student['id'],))
    def test_class_membership_cache_tracks_roster_updates(self):
        store=self.app.extensions['management_store']
        classroom=store.save('classes',dict(class_code='CACHE-SCOPE',class_name='Cache scope'))
        first=store.save('students',dict(student_code='CACHE-1',full_name='First'))
        second=store.save('students',dict(student_code='CACHE-2',full_name='Second'))
        store.membership(classroom['id'],first['id'])
        self.assertEqual(store.class_student_ids(classroom['id']),(first['id'],))
        store.membership(classroom['id'],second['id'])
        self.assertEqual(store.class_student_ids(classroom['id']),(first['id'],second['id']))
        store.membership(classroom['id'],first['id'],True)
        self.assertEqual(store.class_student_ids(classroom['id']),(second['id'],))
    def test_multiple_faces_no_identity_confirmation(self):
        faces = [dict(face_id=i, box=dict(x=i*20,y=20,w=50,h=60),
                      top1=dict(candidate=name, similarity=.6), top2=dict(candidate='Other',similarity=.4),
                      margin=.2, identity_confirmed=False, label='Ứng viên chưa xác nhận')
                 for i,name in enumerate(('A','B'),1)]
        self.engine.result.update(faces=faces, detected_faces=2)
        result = self.post().json
        self.assertEqual(len(result['faces']), 2)
        self.assertTrue(all(not f['identity_confirmed'] for f in result['faces']))
        self.assertNotIn('embedding', json.dumps(result))
    def test_rejects_missing_token_cross_origin_and_stopped_session(self):
        self.assertEqual(self.client.post('/api/camera/sessions').status_code, 403)
        headers = dict(self.headers, Origin='http://other.example')
        self.assertEqual(self.client.post('/api/camera/sessions',headers=headers).status_code, 403)
        self.client.delete('/api/camera/sessions/'+self.sid, headers=self.headers)
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.engine.calls, 0)
    def test_input_limits_and_malformed_jpeg(self):
        self.assertEqual(self.post(b'bad bytes').status_code, 400)
        self.assertEqual(self.post(b'x'*(1024*1024+1)).status_code, 413)
        self.assertEqual(self.post(jpeg((961,100))).status_code, 422)
        self.assertEqual(self.client.post('/api/recognize',data=b'{}',content_type='application/json',headers=self.headers).status_code,415)
        self.assertEqual(self.engine.calls,0)
    def test_busy_and_loading_and_inference_error_contracts(self):
        self.engine.failure=EngineBusy()
        response=self.post()
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers['Retry-After'],'2')
        self.engine.ready=False
        self.assertEqual(self.client.post('/api/camera/sessions',headers=self.headers).status_code,503)
        self.engine.failure=ValueError('private error; not returned')
        response=self.post()
        self.assertEqual(response.status_code,500)
        self.assertNotIn('private error',response.get_data(as_text=True))
    def test_stop_during_inflight_suppresses_late_result(self):
        entered,release=threading.Event(),threading.Event()
        def blocking(bgr,cancelled,profile='retinaface_reuse'):
            entered.set()
            self.assertTrue(release.wait(5))
            if cancelled(): raise SessionStopped()
            return self.engine.result
        self.engine.analyze=blocking
        responses=[]
        def send():
            with self.app.test_client() as client:
                responses.append(client.post('/api/recognize',data=jpeg(),content_type='image/jpeg',headers=self.headers))
        worker=threading.Thread(target=send)
        worker.start()
        self.assertTrue(entered.wait(5))
        self.assertEqual(self.client.delete('/api/camera/sessions/'+self.sid,headers=self.headers).status_code,200)
        release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(responses[0].status_code,409)
        self.assertNotIn('faces',responses[0].json)


if __name__ == '__main__':
    unittest.main()
