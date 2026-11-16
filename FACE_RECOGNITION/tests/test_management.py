"""Management API tests use disposable SQLite and an explicit AI test double."""
import json
from pathlib import Path
import base64
import re
import sqlite3
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.database.db import DomainError
from tests.test_camera_api import FakeEngine,jpeg

ROOT=Path(__file__).resolve().parents[1]


class EnrollmentFake(FakeEngine):
    def __init__(self):
        super().__init__();self.failure=None;self.refreshed=0;self.refresh_result=True;self.state='ready';self.enrollment_frames=0
    def enroll(self,bgr):
        if self.failure:raise self.failure
        return [1.]*512,dict(model_name='ArcFace',detector_backend='retinaface',align=True,normalization='base')
    def refresh_gallery(self):self.refreshed+=1;return self.refresh_result
    def enrollment_frame(self,bgr,posture):
        index=self.enrollment_frames%512;self.enrollment_frames+=1
        vector=[0.]*512;vector[index]=1.
        return dict(accepted=True,embedding=vector,embedding_ms=2.5)


class ManagementAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='management-test-',dir=ROOT/'.venv')
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'management.sqlite3'
        self.engine=EnrollmentFake()
        self.app=create_app(self.engine,start_models=False,overrides={'TESTING':True,'DATABASE_PATH':self.db})
        self.client=self.app.test_client()
        html=self.client.get('/overview').get_data(as_text=True)
        token=json.loads(re.search(r'<script id="management-config" type="application/json">(.*?)</script>',html).group(1))['csrfToken']
        self.headers={'X-Camera-Token':token}

    def post(self,url,data):return self.client.post(url,json=data,headers=self.headers)
    def student(self,code='SV-TEST'):return self.post('/api/students',dict(student_code=code,full_name='Sinh viên kiểm thử')).json
    def classroom(self,code='LOP-TEST'):return self.post('/api/classes',dict(class_code=code,class_name='Lớp kiểm thử',description='Mô tả')).json
    def test_empty_realistic_overview_pages_and_no_seed(self):
        self.assertEqual(self.client.get('/api/overview').json,dict(classes=0,students=0,registered_students=0))
        for page in ('/overview','/classes','/students','/students/1','/classes/1'):
            self.assertEqual(self.client.get(page).status_code,200)
        for endpoint in ('/api/attendance','/api/attendance_sessions','/outputs/management.sqlite3'):
            self.assertEqual(self.client.get(endpoint).status_code,404)

    def test_classes_crud_unique_search_and_soft_delete(self):
        c=self.classroom();self.assertIn('id',c)
        self.assertEqual(self.post('/api/classes',dict(class_code='lop-test',class_name='Trùng')).status_code,409)
        result=self.client.put(f"/api/classes/{c['id']}",json=dict(class_code='UPDATED',class_name='Lớp mới'),headers=self.headers)
        self.assertEqual(result.status_code,200)
        self.assertEqual(len(self.client.get('/api/classes?q=LỚP MỚI').json['items']),1)
        self.assertEqual(self.client.delete(f"/api/classes/{c['id']}",headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/classes').json['items'],[])
        with sqlite3.connect(self.db) as db:self.assertIsNotNone(db.execute('SELECT deleted_at FROM classes').fetchone()[0])

    def test_student_crud_search_duplicate_and_xss_payload_roundtrip(self):
        s=self.student()
        self.assertEqual(self.post('/api/students',dict(student_code='sv-test',full_name='Trùng')).status_code,409)
        r=self.client.put(f"/api/students/{s['id']}",json=dict(student_code='NEW',full_name='<img src=x onerror=alert(1)>'),headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.assertEqual(len(self.client.get('/api/students?q=new').json['items']),1)
        self.assertEqual(self.client.delete(f"/api/students/{s['id']}",headers=self.headers).status_code,200)

    def test_membership_many_to_many_duplicate_remove_preserves_student(self):
        s=self.student();c=self.classroom();c2=self.classroom('SECOND')
        url=f"/api/classes/{c['id']}/students"
        self.assertEqual(self.post(url,dict(student_id=s['id'])).status_code,201)
        self.assertEqual(self.post(url,dict(student_id=s['id'])).status_code,409)
        self.assertEqual(self.post(f"/api/classes/{c2['id']}/students",dict(student_id=s['id'])).status_code,201)
        self.assertEqual(len(self.client.get(f"/api/students/{s['id']}").json['classes']),2)
        self.assertEqual(self.client.delete(f"/api/classes/{c['id']}",headers=self.headers).status_code,409)
        self.assertEqual(self.client.delete(f"/api/students/{s['id']}",headers=self.headers).status_code,409)
        self.assertEqual(self.client.delete(url+f"/{s['id']}",headers=self.headers).status_code,200)
        self.assertEqual(self.client.get(f"/api/students/{s['id']}").status_code,200)

    def test_required_fields_bad_ids_and_write_guard(self):
        for data in ({},dict(student_code='',full_name='A'),dict(student_code='X',full_name=''),[]):
            self.assertEqual(self.post('/api/students',data).status_code,400)
        self.assertEqual(self.client.put('/api/students/1',json={}).status_code,403)
        self.assertEqual(self.client.delete('/api/students/1').status_code,403)
        self.assertEqual(self.client.get('/api/students/999').status_code,404)
        c=self.classroom()
        self.assertEqual(self.post(f"/api/classes/{c['id']}/students",dict(student_id=True)).status_code,400)
        self.assertEqual(self.post(f"/api/classes/{c['id']}/students",dict(student_id=999)).status_code,404)

    def test_enrollment_persists_vectors_no_public_vector_and_protects_student(self):
        c=self.classroom();store=self.app.extensions['management_store']
        samples=[dict(posture=pose,embedding=[1.]+[0.]*511) for pose in ('thẳng','quay_trái','quay_phải','ngẩng_nhẹ','cúi_nhẹ')]
        sid=store.create_student_with_embeddings(dict(student_code='SV-CAMERA',full_name='Sinh viên webcam'),c['id'],samples)
        s=self.client.get(f'/api/students/{sid}').json
        self.assertEqual(s['face_count'],5)
        self.assertEqual(self.client.get('/api/overview').json['registered_students'],1)
        self.assertEqual(self.client.delete(f"/api/students/{s['id']}",headers=self.headers).status_code,409)
        # A fresh app/connection sees the same persisted data; no in-memory DB.
        restarted=create_app(EnrollmentFake(),start_models=False,overrides={'DATABASE_PATH':self.db}).test_client()
        self.assertEqual(restarted.get(f"/api/students/{s['id']}").json['face_count'],5)
        with sqlite3.connect(self.db) as db:
            vector=json.loads(db.execute('SELECT embedding_json FROM face_embeddings LIMIT 1').fetchone()[0]);self.assertEqual(len(vector),512)

    def test_persistence_in_fresh_python_process(self):
        c=self.classroom();store=self.app.extensions['management_store']
        sid=store.create_student_with_embeddings(dict(student_code='SV-PERSIST',full_name='Persisted'),c['id'],[dict(posture='thẳng',embedding=[1.]+[0.]*511)])
        # Reopen Flask and SQLite in an entirely fresh interpreter, not just RAM.
        code="""import sys,json
from backend.app import create_app
from tests.test_management import EnrollmentFake
app=create_app(EnrollmentFake(),start_models=False,overrides={'DATABASE_PATH':sys.argv[1]})
with app.test_client() as c:
 print(json.dumps(c.get('/api/students/'+sys.argv[2]).json))
"""
        result=subprocess.run([sys.executable,'-c',code,str(self.db),str(sid)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        restored=json.loads(result.stdout)
        self.assertEqual(restored['student_code'],'SV-PERSIST')
        self.assertEqual(restored['face_count'],1)
        self.assertEqual(len(restored['classes']),1)

    def test_enrollment_validation_errors_are_explicit_and_not_saved(self):
        s=self.student()
        self.assertEqual(self.client.post(f"/api/students/{s['id']}/faces",headers=self.headers).status_code,404)
        self.assertFalse(self.client.get(f"/api/students/{s['id']}").json['face_count'])
        self.assertTrue(self.post('/api/students/check-code',dict(student_code='SV-TEST')).json['exists'])
        self.assertFalse(self.post('/api/students/check-code',dict(student_code='SV-NEW')).json['exists'])
        started=self.post('/api/enrollment/start',{});self.assertEqual(started.status_code,201)
        self.assertEqual(self.client.get('/api/overview').json['students'],1)
        self.post('/api/enrollment/cancel',dict(session=started.json['session']))
        c=self.classroom()
        with self.assertRaises(DomainError):
            self.app.extensions['management_store'].create_student_with_embeddings(dict(student_code='SV-TEST',full_name='Duplicate'),c['id'],[dict(posture='thẳng',embedding=[1.]+[0.]*511)])
        self.assertEqual(self.client.get(f"/api/students/{s['id']}").json['face_count'],0)

    def test_reenrollment_restores_old_vectors_if_publish_is_rejected(self):
        c=self.classroom();store=self.app.extensions['management_store']
        sid=store.create_student_with_embeddings(dict(student_code='SV-REPLACE',full_name='Replace me'),c['id'],[dict(posture='thẳng',embedding=[1.]+[0.]*511)])
        with store.connection() as db:before=dict(db.execute('SELECT * FROM face_embeddings WHERE student_id=?',(sid,)).fetchone())
        previous=store.replace_student_embeddings(sid,[dict(posture='quay_trái',embedding=[0.,1.]+[0.]*510)])
        self.assertEqual(len(previous),1)
        store.restore_student_embeddings(sid,previous)
        with store.connection() as db:after=dict(db.execute('SELECT * FROM face_embeddings WHERE student_id=?',(sid,)).fetchone())
        self.assertEqual(after,before)

    def test_camera_enrollment_session_publishes_only_after_final_confirmation(self):
        c=self.classroom()
        with patch('backend.management.MIN_SAMPLE_INTERVAL_SECONDS',0):
            started=self.post('/api/enrollment/start',{})
            self.assertEqual(started.status_code,201)
            session=started.json['session']
            self.assertEqual(self.client.get('/api/overview').json['students'],0)
            frame=base64.b64encode(jpeg()).decode('ascii')
            for _ in range(30):
                response=self.post('/api/enrollment/frame',dict(session=session,frame=frame))
                self.assertEqual(response.status_code,200,response.json)
                self.assertTrue(response.json['accepted'])
            self.assertTrue(response.json['complete'])
            self.assertEqual(self.client.get('/api/overview').json['students'],0)
            self.assertEqual(self.post('/api/enrollment/duplicate-check',dict(session=session)).status_code,200)
            saved=self.post('/api/enrollment/finish',dict(session=session,student_code='SV-CAM',full_name='Camera student',class_id=c['id'],identity_confirmed=True,gallery_warning_acknowledged=True))
        self.assertEqual(saved.status_code,201,saved.json)
        self.assertTrue(saved.json['gallery_updated'])
        self.assertEqual(saved.json['embedding_count'],30)
        self.assertEqual(self.engine.refreshed,1)
        student=self.client.get(f"/api/students/{saved.json['student_id']}").json
        self.assertEqual(student['face_count'],30)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM face_embeddings WHERE image_sha256 IS NOT NULL').fetchone()[0],0)

    def test_gallery_publish_failure_rolls_back_new_student_and_vectors(self):
        c=self.classroom()
        with patch('backend.management.MIN_SAMPLE_INTERVAL_SECONDS',0):
            session=self.post('/api/enrollment/start',{}).json['session'];frame=base64.b64encode(jpeg()).decode('ascii')
            for _ in range(30):self.assertEqual(self.post('/api/enrollment/frame',dict(session=session,frame=frame)).status_code,200)
            self.post('/api/enrollment/duplicate-check',dict(session=session))
            self.engine.refresh_result=False
            response=self.post('/api/enrollment/finish',dict(session=session,student_code='SV-ROLLBACK',full_name='No partial row',class_id=c['id'],identity_confirmed=True,gallery_warning_acknowledged=True))
        self.assertEqual(response.status_code,503,response.json)
        self.assertEqual(self.client.get('/api/overview').json['students'],0)
        store=self.app.extensions['management_store'];gallery,_=store.gallery_snapshot()
        self.assertEqual(len(gallery),92)

    def test_legacy_mapping_manual_unique_originals_preserved(self):
        s=self.student();other=self.student('OTHER')
        before=(ROOT/'outputs/face_embeddings.json').read_bytes()
        option=self.client.get('/api/legacy-gallery').json['items'][0]
        url=f"/api/students/{s['id']}/legacy-mapping"
        data=dict(dataset_label=option['dataset_label'],confirmed=False)
        self.assertEqual(self.post(url,data).status_code,400)
        data['confirmed']=True
        self.assertEqual(self.post(url,data).status_code,201)
        self.assertEqual(self.post(f"/api/students/{other['id']}/legacy-mapping",data).status_code,409)
        store=self.app.extensions['management_store'];gallery,revision=store.gallery_snapshot()
        self.assertEqual(len(gallery),92)
        self.assertEqual(sum(r['student_id'].startswith('SV-TEST') for r in gallery),option['embedding_count'])
        self.assertEqual(self.client.get(f"/api/students/{s['id']}").json['face_count'],option['embedding_count'])
        self.assertEqual((ROOT/'outputs/face_embeddings.json').read_bytes(),before)
        self.assertGreater(revision,0)

    def test_foreign_keys_enforced_and_nonfinite_vectors_rejected(self):
        store=self.app.extensions['management_store']
        with store.connection() as db:self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
        s=self.student()
        for vector in ([0.]*512,[float('nan')]*512,[1.]*3):
            with self.assertRaises(DomainError):
                store.add_embedding(s['id'],vector,{},'bad')


if __name__=='__main__':unittest.main()
