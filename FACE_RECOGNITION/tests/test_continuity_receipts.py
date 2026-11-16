"""Continuity/protocol primitives using real saved detections and original photos; not webcam proof."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import numpy as np
from PIL import Image
from io import BytesIO

from backend.attendance.continuity import Continuity
from backend.attendance.attendance_service import AttendanceService
from backend.database.db import ManagementStore
from backend.face_recognition.verification_workflow import upper95,manifest_rows
from tests.camera_live_api_qa import payload

ROOT=Path(__file__).resolve().parents[1]


class ContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual=json.loads((ROOT/'outputs/real_auto_attendance_trace.json').read_text(encoding='utf-8'))['linh']
        cls.faces=actual['faces'];cls.frame=actual['frame']
        g=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
        labels=sorted({r['student_id'] for r in g})
        cls.images=[]
        for label in labels[:2]:
            data,_=payload(Path(next(r for r in g if r['student_id']==label)['image_path']))
            cls.images.append(np.asarray(Image.open(BytesIO(data)).convert('RGB'))[:,:,::-1].copy())

    def tracker(self):
        t=Continuity();t.update(copy.deepcopy(self.faces),self.frame,self.images[0],0)
        return t,t.snapshot(self.images[0],.1)

    def test_eight_second_inference_with_continuous_preview(self):
        t,s=self.tracker()
        for at in range(1,9):t.update(copy.deepcopy(self.faces),self.frame,self.images[0],at)
        f=copy.deepcopy(self.faces);bound=t.bind(f,self.frame,s,8.1)
        self.assertIsNotNone(bound[0]);self.assertTrue(f[0]['tracking']['valid'])

    def test_leave_reenter_and_delayed_preview_do_not_revive(self):
        t,s=self.tracker();t.update([],self.frame,self.images[0],1)
        t.update(copy.deepcopy(self.faces),self.frame,self.images[0],.5)
        self.assertFalse(t.valid(s[0],1.1))
        t.update(copy.deepcopy(self.faces),self.frame,self.images[0],2)
        self.assertFalse(t.valid(s[0],2.1))

    def test_replacement_gap_and_new_camera(self):
        import cv2
        t,s=self.tracker();other=cv2.resize(self.images[1],(self.images[0].shape[1],self.images[0].shape[0]))
        t.update(copy.deepcopy(self.faces),self.frame,other,1)
        self.assertFalse(t.valid(s[0],1.1))
        t,s=self.tracker();self.assertFalse(t.valid(s[0],3))
        self.assertFalse(Continuity().valid(s[0],1))

    def test_sql_accepts_slow_but_continuous_and_rejects_lost(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.venv',prefix='continuity-sql-') as tmp:
            p=Path(tmp)/'copy.sqlite3'
            with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as src,closing(sqlite3.connect(p)) as dest:src.backup(dest)
            store=ManagementStore(p,ROOT/'outputs/face_embeddings.json');attendance=AttendanceService(store)
            people=store.list_entities('students')[:2];c=store.save('classes',dict(class_code='LEASE-TEST',class_name='Storage primitive only'))
            for person in people:store.membership(c['id'],person['id'])
            sid=attendance.create(dict(class_id=c['id'],session_name='Lease test',request_key='lease-test'))['id']
            t,s=self.tracker()
            for at in range(1,9):t.update(copy.deepcopy(self.faces),self.frame,self.images[0],at)
            check=lambda:t.valid(s[0],8.1)
            r=attendance.confirm_verified(sid,[people[0]['id']],lambda:False,time.monotonic()-8,check)
            self.assertEqual(r['session']['stats']['present'],1)
            self.assertFalse(attendance.confirm_verified(sid,[people[0]['id']],lambda:False,time.monotonic()-8,check)['changed'])
            t.update([],self.frame,self.images[0],9)
            self.assertFalse(attendance.confirm_verified(sid,[people[1]['id']],lambda:False,time.monotonic()-8,lambda:t.valid(s[0],9.1))['changed'])
            self.assertEqual(attendance.detail(sid)['stats']['present'],1)

    def test_confidence_and_empty_real_data_manifest(self):
        self.assertGreater(upper95(0,100),.01)
        self.assertLess(upper95(0,299),.01)
        rows=manifest_rows(ROOT/'outputs/verification_data/manifest.csv')
        self.assertTrue(all(r['split']=='calibration' for r in rows))

    def test_actual_gallery_top_two_are_distinct_identities(self):
        from backend.face_recognition.face_service import FaceMatcher
        from backend.face_recognition.verification_workflow import digest
        gallery=ROOT/'outputs/face_embeddings.json'
        rows=json.loads(gallery.read_text(encoding='utf-8'))
        config=json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8'))
        matcher=FaceMatcher(rows,config,digest(gallery))
        self.assertEqual(len(rows),92);self.assertEqual(len(matcher.students),5)
        for label in matcher.students:
            result=matcher.match(next(r['embedding'] for r in rows if r['student_id']==label))
            self.assertEqual(result['best_candidate'],label)
            self.assertNotEqual(result['best_candidate'],result['second_candidate'])
            self.assertAlmostEqual(result['margin'],result['best_similarity']-result['second_similarity'])


if __name__=='__main__':unittest.main()
