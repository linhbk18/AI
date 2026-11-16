"""Preview is anonymous, bounded, cancellable and independent of ArcFace lock."""
import threading
import unittest

import numpy as np

from backend.face_recognition.runtime_service import RecognitionEngine,EngineBusy,SessionStopped
from tests import test_camera_api as camera_tests
from tests.test_camera_api import jpeg


class PreviewAPI(unittest.TestCase):
    def setUp(self):
        camera_tests.CameraAPIContract.setUp(self)
        self.engine.detect_preview=lambda bgr,cancelled:dict(faces=[dict(face_id=1,box=dict(x=10,y=20,w=40,h=50))],frame=dict(width=bgr.shape[1],height=bgr.shape[0]),preview_only=True,timings_ms=dict(detection_ms=1),identity_confirmation_enabled=False,attendance_enabled=False)
    def post(self,data=None):
        return self.client.post('/api/detect',data=jpeg() if data is None else data,headers=self.headers,content_type='image/jpeg')
    def test_anonymous_preview_has_no_candidate_or_embedding(self):
        r=self.post();self.assertEqual(r.status_code,200);self.assertTrue(r.json['preview_only'])
        self.assertNotIn('top1',r.json['faces'][0]);self.assertNotIn('embedding',r.get_data(as_text=True))
        self.assertIn('server_total_ms',r.headers['Server-Timing'])
        self.assertEqual(self.engine.calls,0)
    def test_limits_session_and_decode(self):
        self.assertEqual(self.post(jpeg((641,480))).status_code,422)
        self.assertEqual(self.post(b'broken').status_code,400)
        self.client.delete('/api/camera/sessions/'+self.sid,headers=self.headers)
        self.assertEqual(self.post().status_code,409)
    def test_busy_preview_does_not_queue(self):
        def busy(*args):raise EngineBusy()
        self.engine.detect_preview=busy
        self.assertEqual(self.post().status_code,429)
    def test_insufficient_memory_is_an_explicit_resource_error(self):
        def failed(*args):raise MemoryError()
        self.engine.detect_preview=failed
        response=self.post();self.assertEqual(response.status_code,503)
        self.assertEqual(response.json['error']['code'],'RESOURCE_LIMIT')


class PreviewEngine(unittest.TestCase):
    def test_independent_guard_cancel_and_no_recognition(self):
        engine=RecognitionEngine();engine.state='ready'
        entered=threading.Event();release=threading.Event()
        class Detector:
            def detect_faces(self,*args,**kwargs):entered.set();release.wait(3);return []
        class Pipeline:yunet=Detector()
        engine.pipeline=Pipeline()
        engine._guard.acquire() # a simulated ArcFace job is already active
        frame=np.zeros((240,320,3),dtype=np.uint8);results=[]
        thread=threading.Thread(target=lambda:results.append(engine.detect_preview(frame)))
        thread.start();self.assertTrue(entered.wait(1))
        with self.assertRaises(EngineBusy):engine.detect_preview(frame)
        release.set();thread.join(3);engine._guard.release()
        self.assertTrue(results[0]['preview_only']);self.assertEqual(engine.inference_count,0)
        with self.assertRaises(SessionStopped):engine.detect_preview(frame,lambda:True)


if __name__=='__main__':unittest.main()
