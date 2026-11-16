"""Management API tests use disposable SQLite and an explicit AI test double."""
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import subprocess
import sys
import unittest

from backend.app import create_app
from backend.database.db import DomainError
from backend.face_recognition.runtime_service import EngineBusy, EngineUnavailable
from tests.test_camera_api import FakeEngine, jpeg

ROOT=Path(__file__).resolve().parents[1]


class EnrollmentFake(FakeEngine):
    def __init__(self):
        super().__init__();self.failure=None;self.refreshed=0
    def enroll(self,bgr):
        if self.failure:raise self.failure
        return [1.]*512,dict(model_name='ArcFace',detector_backend='retinaface',align=True,normalization='base')
    def refresh_gallery(self):self.refreshed+=1;return True


class ManagementAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='management-test-',dir=ROOT/'.venv')
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'management.sqlite3'
        self.engine=EnrollmentFake()
        self.app=create_app(self.engine,start_models=False,overrides={'TESTING':True,'DATABASE_PATH':self.db,'LEGACY_PHOTO_UPLOAD_ENABLED':True,'ENROLLMENT_BACKUP_DIR':Path(self.temp.name)/'backups'})
        self.client=self.app.test_client()
        html=self.client.get('/overview').get_data(as_text=True)
        token=json.loads(re.search(r'<script id="management-config" type="application/json">(.*?)</script>',html).group(1))['csrfToken']
        self.headers={'X-Camera-Token':token}

    def post(self,url,data):return self.client.post(url,json=data,headers=self.headers)
    def student(self,code='SV-TEST'):return self.post('/api/students',dict(student_code=code,full_name='Sinh viên kiểm thử')).json
    def classroom(self,code='LOP-TEST'):return self.post('/api/classes',dict(class_code=code,class_name='Lớp kiểm thử',description='Mô tả')).json
    def upload(self,sid,data=None,confirmed='true'):
        return self.client.post(f'/api/students/{sid}/faces',headers=self.headers,data=dict(image=(BytesIO(jpeg() if data is None else data),'test.jpg'),confirmed=confirmed),content_type='multipart/form-data')

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
        s=self.student();r=self.upload(s['id'])
        self.assertEqual(r.status_code,201,r.json)
        self.assertNotIn('embedding',r.json)
        self.assertEqual(self.client.get('/api/overview').json['registered_students'],1)
        self.assertEqual(self.engine.refreshed,1)
        self.assertEqual(self.upload(s['id']).status_code,409)
        self.assertEqual(self.upload(s['id'],jpeg((321,180))).status_code,201)
        self.assertEqual(self.client.delete(f"/api/students/{s['id']}",headers=self.headers).status_code,409)
        # A fresh app/connection sees the same persisted data; no in-memory DB.
        restarted=create_app(EnrollmentFake(),start_models=False,overrides={'DATABASE_PATH':self.db}).test_client()
        self.assertEqual(restarted.get(f"/api/students/{s['id']}").json['face_count'],2)
        with sqlite3.connect(self.db) as db:
            vector=json.loads(db.execute('SELECT embedding_json FROM face_embeddings').fetchone()[0]);self.assertEqual(len(vector),512)

    def test_persistence_in_fresh_python_process(self):
        s=self.student();c=self.classroom()
        self.post(f"/api/classes/{c['id']}/students",dict(student_id=s['id']))
        self.assertEqual(self.upload(s['id']).status_code,201)
        # Reopen Flask and SQLite in an entirely fresh interpreter, not just RAM.
        code="""import sys,json
from backend.app import create_app
from tests.test_management import EnrollmentFake
app=create_app(EnrollmentFake(),start_models=False,overrides={'DATABASE_PATH':sys.argv[1]})
with app.test_client() as c:
 print(json.dumps(c.get('/api/students/'+sys.argv[2]).json))
"""
        result=subprocess.run([sys.executable,'-c',code,str(self.db),str(s['id'])],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        restored=json.loads(result.stdout)
        self.assertEqual(restored['student_code'],'SV-TEST')
        self.assertEqual(restored['face_count'],1)
        self.assertEqual(len(restored['classes']),1)

    def test_enrollment_validation_errors_are_explicit_and_not_saved(self):
        s=self.student()
        self.assertEqual(self.upload(s['id'],confirmed='false').status_code,400)
        self.assertEqual(self.upload(s['id'],b'not an image').status_code,422)
        for exc,status in ((ValueError('NO_FACE'),422),(ValueError('MULTIPLE_FACES'),422),(EngineBusy(),429),(EngineUnavailable(),503)):
            self.engine.failure=exc;response=self.upload(s['id']);self.assertEqual(response.status_code,status,response.json)
        self.assertEqual(self.client.get(f"/api/students/{s['id']}").json['face_count'],0)
        self.assertEqual(self.upload(s['id'],b'x'*(4*1024*1024+1)).status_code,413)

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
