"""Attendance tests use a disposable DB; no demo attendance in real SQLite."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import subprocess
import sys
import unittest

from backend.attendance.attendance_service import AttendanceService
from backend.database.db import DomainError
from backend.database.import_legacy_students import import_students
from backend.face_recognition.runtime_service import RecognitionEngine
from tests import test_management as management_tests
from tests.test_management import ROOT


class AttendanceAPI(unittest.TestCase):
    def setUp(self):
        management_tests.ManagementAPI.setUp(self)
        self.service=self.app.extensions['attendance_service'];self.store=self.app.extensions['management_store']
        self.c=self.store.save('classes',dict(class_code='QA-C',class_name='Lớp tạm'))
        self.s=[self.store.save('students',dict(student_code='QA-'+str(i),full_name='Sinh viên '+str(i))) for i in range(3)]
        for s in self.s[:2]:self.store.membership(self.c['id'],s['id'])
    def post(self,url,data=None):return self.client.post(url,json={} if data is None else data,headers=self.headers)
    def begin(self,key='test-key'):
        return self.post('/api/attendance/sessions',dict(class_id=self.c['id'],session_name='Buổi QA',session_date='2026-11-14',request_key=key))
    def confirm(self,sid,student):return self.post(f'/api/attendance/sessions/{sid}/confirm',dict(student_id=student,confirmed=True,recognition_source='MANUAL'))

    def test_begin_snapshot_idempotency_empty_class_and_missing_class(self):
        response=self.begin();self.assertEqual(response.status_code,201,response.json);sid=response.json['id']
        self.assertEqual(response.json['stats'],dict(total=2,present=0,absent=0,pending=2))
        self.assertEqual(self.begin().json['id'],sid)
        self.assertEqual(self.begin('other').status_code,409)
        self.assertEqual(self.post('/api/attendance/sessions',dict(session_name='X')).status_code,400)
        empty=self.store.save('classes',dict(class_code='EMPTY',class_name='Empty'))
        self.assertEqual(self.post('/api/attendance/sessions',dict(class_id=empty['id'],session_name='X',request_key='empty')).status_code,422)

    def test_confirmation_outside_requires_human_and_duplicate_is_noop(self):
        sid=self.begin().json['id']
        self.assertEqual(self.confirm(sid,self.s[2]['id']).status_code,422)
        self.assertEqual(self.post(f'/api/attendance/sessions/{sid}/confirm',dict(student_id=self.s[0]['id'],confirmed=False)).status_code,400)
        self.assertEqual(self.post(f'/api/attendance/sessions/{sid}/confirm',dict(student_id=self.s[0]['id'],confirmed=True,recognition_source='AI_VERIFIED')).status_code,400)
        self.assertTrue(self.confirm(sid,self.s[0]['id']).json['changed'])
        self.assertFalse(self.confirm(sid,self.s[0]['id']).json['changed'])
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM attendance_records WHERE session_id=?',(sid,)).fetchone()[0],2)

    def test_manual_confirmation_does_not_require_ai_verification_or_policy(self):
        # Human confirmation remains available even when biometric automation is locked.
        sid=self.begin().json['id']
        self.assertFalse(self.client.get('/api/attendance/automation-status').json['ready'])
        result=self.confirm(sid,self.s[0]['id'])
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['session']['stats']['present'],1)
        self.assertEqual(result.json['session']['stats']['total'],2)
        record=next(r for r in result.json['session']['records'] if r['student_id']==self.s[0]['id'])
        self.assertEqual(record['source'],'manual')
        self.assertEqual(record['recognition_source'],'MANUAL')
        self.assertFalse(self.confirm(sid,self.s[0]['id']).json['changed'])

    def test_finish_absence_and_no_further_camera_writes(self):
        sid=self.begin().json['id'];self.confirm(sid,self.s[0]['id'])
        r=self.post(f'/api/attendance/sessions/{sid}/finish').json
        self.assertEqual(r['status'],'COMPLETED');self.assertEqual(r['stats'],dict(total=2,present=1,absent=1,pending=0))
        again=self.post(f'/api/attendance/sessions/{sid}/finish').json;self.assertEqual(again['ended_at'],r['ended_at'])
        self.assertEqual(self.confirm(sid,self.s[1]['id']).status_code,409)
        self.assertEqual(self.post('/api/camera/sessions',dict(attendance_session_id=sid)).status_code,409)

    def test_history_edits_are_audited_and_isolated_between_sessions(self):
        one=self.begin().json['id'];self.post(f'/api/attendance/sessions/{one}/finish')
        two=self.begin('second').json['id'];self.post(f'/api/attendance/sessions/{two}/finish')
        path=f'/api/attendance/sessions/{one}/records/{self.s[0]["id"]}'
        data=dict(attendance_status='PRESENT',reason='Đối chiếu lại hồ sơ',confirmed=True)
        result=self.client.put(path,json=data,headers=self.headers)
        self.assertEqual(result.status_code,200);self.assertEqual(len(result.json['edits']),1)
        self.assertEqual(self.service.detail(two)['stats']['present'],0)
        self.assertEqual(self.service.detail(one)['records'][0]['recognition_source'],'MANUAL')
        data['reason']='';self.assertEqual(self.client.put(path,json=data,headers=self.headers).status_code,400)

    def test_snapshot_survives_class_name_student_name_and_membership_changes(self):
        sid=self.begin().json['id'];self.store.membership(self.c['id'],self.s[0]['id'],True)
        self.store.save('students',dict(student_code='NEW',full_name='Tên mới'),self.s[0]['id'])
        self.store.save('classes',dict(class_code='NEWCLASS',class_name='Tên lớp mới'),self.c['id'])
        self.assertEqual(self.confirm(sid,self.s[0]['id']).status_code,200)
        detail=self.service.detail(sid);self.assertEqual(detail['class_code_snapshot'],'QA-C')
        self.assertEqual(detail['records'][0]['student_code_snapshot'],'QA-0')
        self.assertEqual(detail['stats']['total'],2)

    def test_concurrent_confirmations_and_finish_are_atomic(self):
        sid=self.begin().json['id'];student=self.s[0]['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.service.confirm(sid,student,dict(confirmed=True)),range(2)))
        self.assertEqual(sum(r['changed'] for r in results),1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.service.finish,sid),pool.submit(self.service.confirm,sid,self.s[1]['id'],dict(confirmed=True))]
            for future in futures:
                try:future.result()
                except DomainError as exc:self.assertEqual(exc.status,409)
        detail=self.service.detail(sid);self.assertEqual(detail['stats']['pending'],0);self.assertEqual(len(detail['records']),2)

    def test_reports_count_completed_only_and_csv(self):
        sid=self.begin().json['id'];self.confirm(sid,self.s[0]['id'])
        self.assertEqual(self.client.get('/api/attendance/reports').json['completed_sessions'],0)
        self.post(f'/api/attendance/sessions/{sid}/finish')
        report=self.client.get('/api/attendance/reports').json
        self.assertEqual((report['completed_sessions'],report['present'],report['absent']),(1,1,1))
        self.assertEqual(report['by_class'][0]['attendance_rate'],.5)
        self.assertEqual(self.client.get('/api/attendance/reports.csv').status_code,200)
        self.assertEqual(len(self.client.get('/api/attendance/sessions?class_id='+str(self.c['id'])+'&date=2026-11-14').json['items']),1)

    def test_invalid_filters_are_not_silently_treated_as_all_classes(self):
        for query in ('class_id=wrong','class_id=-1','class_id=0'):
            self.assertEqual(self.client.get('/api/attendance/reports?'+query).status_code,400)
        self.assertEqual(self.client.get('/api/attendance/sessions?date=not-a-date').status_code,400)

    def test_archiving_current_profiles_does_not_remove_completed_history(self):
        sid=self.begin().json['id'];self.post(f'/api/attendance/sessions/{sid}/finish')
        for student in self.s[:2]:
            self.store.membership(self.c['id'],student['id'],True);self.store.delete('students',student['id'])
        self.store.delete('classes',self.c['id'])
        self.assertEqual(self.service.detail(sid)['stats']['absent'],2)
        self.assertEqual(self.service.reports(self.c['id'])['completed_sessions'],1)

    def test_restart_in_fresh_process_preserves_open_session(self):
        sid=self.begin().json['id'];self.confirm(sid,self.s[0]['id'])
        code="from backend.app import create_app;from tests.test_management import EnrollmentFake;import sys,json;a=create_app(EnrollmentFake(),False,{'DATABASE_PATH':sys.argv[1]});print(json.dumps(a.test_client().get('/api/attendance/sessions/'+sys.argv[2]).json))"
        r=subprocess.run([sys.executable,'-c',code,str(self.db),str(sid)],cwd=ROOT,capture_output=True,text=True,timeout=40)
        self.assertEqual(r.returncode,0,r.stderr);detail=json.loads(r.stdout);self.assertEqual(detail['stats']['present'],1);self.assertEqual(detail['status'],'IN_PROGRESS')

    def test_camera_context_rechecked_after_inference_and_never_marks_attendance(self):
        sid=self.begin().json['id'];camera=self.post('/api/camera/sessions',dict(attendance_session_id=sid)).json['session_id']
        seen={}
        def infer(image,cancelled,profile='retinaface_reuse',allowed_student_ids=None,class_id=None):
            seen['ids']=allowed_student_ids;self.service.finish(sid)
            return dict(faces=[],frame=dict(width=320,height=180))
        self.engine.analyze=infer
        from tests.test_camera_api import jpeg
        r=self.client.post('/api/recognize',data=jpeg(),headers=dict(self.headers,**{'X-Camera-Session':camera,'X-Class-Id':str(self.c['id'])}),content_type='image/jpeg')
        self.assertEqual(r.status_code,409);self.assertEqual(set(seen['ids']),{s['id'] for s in self.s[:2]})
        self.assertEqual(self.service.detail(sid)['stats']['present'],0)


class ClassGallery(unittest.TestCase):
    def test_demo_student_outside_class_is_removed_before_cosine(self):
        import tempfile
        from pathlib import Path
        import numpy as np
        from backend.database.db import ManagementStore
        with tempfile.TemporaryDirectory(dir=ROOT/'.venv') as folder:
            store=ManagementStore(Path(folder)/'scope-demo.sqlite3',ROOT/'outputs/face_embeddings.json')
            config=json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8'))
            imported=import_students(store,config)['students']
            demo=next(row for row in imported if row['student_code']=='DEMO-SV005')
            classmates=[row for row in imported if row['student_code'] in ('DEMO-SV001','DEMO-SV003')]
            classroom=store.save('classes',dict(class_code='MIS-AI-QA',class_name='MIS-AI QA'))
            for row in classmates:store.membership(classroom['id'],row['student_id'])
            engine=RecognitionEngine();engine.configure_store(store);engine._gallery_config=config;engine._base_gallery_hash='test';engine._base_policy=None;engine.state='ready'
            demo_query=next(record['embedding'] for record in store.legacy if record['student_id']=='Thu Thảo')
            class Pipeline:
                def run(self,*args):return [dict(embedding=demo_query,facial_area=dict(x=1,y=1,w=40,h=40))],{}
            engine.pipeline=Pipeline()
            face=engine.analyze(np.zeros((100,100,3),dtype=np.uint8),allowed_student_ids=[r['student_id'] for r in classmates],class_id=classroom['id'])['faces'][0]
            scoped=engine._class_matchers[(engine._gallery_revision,classroom['id'],tuple(sorted(r['student_id'] for r in classmates)))]
            self.assertTrue(scoped.records)
            self.assertNotIn('DEMO-SV005 · Thu Thảo',{record['student_id'] for record in scoped.records})
            self.assertTrue(face['unknown'],face);self.assertEqual(face['label'],'Không xác định');self.assertIsNone(face['top1']['student'])

    def test_scoped_one_identity_none_and_outside_class(self):
        import tempfile
        from pathlib import Path
        from backend.database.db import ManagementStore
        with tempfile.TemporaryDirectory(dir=ROOT/'.venv') as folder:
            store=ManagementStore(Path(folder)/'test.sqlite3',ROOT/'outputs/face_embeddings.json')
            config=json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8'));rows=import_students(store,config)['students']
            engine=RecognitionEngine();engine.configure_store(store);engine._gallery_config=config;engine._base_gallery_hash='test';engine._base_policy=None;engine.state='ready'
            query=store.legacy[0]['embedding']
            class Pipeline:
                def run(self,*args):return [dict(embedding=query,facial_area=dict(x=1,y=1,w=40,h=40))],{}
            engine.pipeline=Pipeline()
            import numpy as np
            frame=np.zeros((100,100,3),dtype=np.uint8)
            calibrated='retinaface_scaled_experimental'
            one=engine.analyze(frame,profile=calibrated,allowed_student_ids=[rows[0]['student_id']])['faces'][0]
            self.assertIsNotNone(one['top2']['candidate']);self.assertNotEqual(one['top1']['candidate'],one['top2']['candidate'])
            # A registered vector matches itself: passes similarity+margin on the calibrated profile.
            self.assertTrue(one['verified']);self.assertTrue(one['eligible_for_confirmation']);self.assertEqual(one['candidate_student_id'],rows[0]['student_id'])
            # Same face on an uncalibrated profile is never verified.
            self.assertFalse(engine.analyze(frame,allowed_student_ids=[rows[0]['student_id']])['faces'][0]['verified'])
            outside=engine.analyze(frame,profile=calibrated,allowed_student_ids=[rows[1]['student_id']])['faces'][0]
            self.assertTrue(outside['outside_class']);self.assertFalse(outside['eligible_for_confirmation'])
            empty=engine.analyze(frame,profile=calibrated,allowed_student_ids=[])['faces'][0]
            self.assertEqual(empty['top1'],one['top1']);self.assertTrue(empty['outside_class']);self.assertFalse(empty['eligible_for_confirmation'])


if __name__=='__main__':unittest.main()
