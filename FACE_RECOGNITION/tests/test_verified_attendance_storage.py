"""SQL transaction tests only; no fake model, embedding or recognition result."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from backend.database.db import ManagementStore,DomainError
from backend.attendance.attendance_service import AttendanceService
from backend.attendance.automatic import AutomaticAttendance

ROOT=Path(__file__).resolve().parents[1]


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.venv',prefix='verified-storage-');self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'test.sqlite3'
        with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as src,closing(sqlite3.connect(path)) as dest:src.backup(dest)
        self.store=ManagementStore(path,ROOT/'outputs/face_embeddings.json');self.service=AttendanceService(self.store)
        self.people=self.store.list_entities('students')[:2]
        c=self.store.save('classes',dict(class_code='VERIFIED-SQL-TEST',class_name='Database transaction test'))
        self.store.membership(c['id'],self.people[0]['id'])
        self.sid=self.service.create(dict(class_id=c['id'],session_name='Storage only',request_key='verified-sql-test'))['id']

    def test_internal_sql_source_unique_and_finish(self):
        r=self.service.confirm_verified(self.sid,[self.people[0]['id']]*2,lambda:False,time.monotonic(),lambda:True)
        self.assertEqual(r['session']['stats']['present'],1)
        self.assertEqual(r['session']['records'][0]['recognition_source'],'AI_VERIFIED')
        self.assertFalse(self.service.confirm_verified(self.sid,[self.people[0]['id']],lambda:False,time.monotonic(),lambda:True)['changed'])
        self.service.finish(self.sid)
        with self.assertRaises(DomainError):self.service.confirm_verified(self.sid,[self.people[0]['id']],lambda:False,time.monotonic())

    def test_stale_and_cancelled_do_not_write(self):
        for cancelled,stamp in ((lambda:True,time.monotonic()),(lambda:False,time.monotonic()-3)):
            self.assertFalse(self.service.confirm_verified(self.sid,[self.people[0]['id']],cancelled,stamp)['changed'])
        self.assertEqual(self.service.detail(self.sid)['stats']['present'],0)

    def test_outsider_batch_rolls_back(self):
        with self.assertRaises(DomainError):self.service.confirm_verified(self.sid,[p['id'] for p in self.people],lambda:False,time.monotonic(),lambda:True)
        self.assertEqual(self.service.detail(self.sid)['stats']['present'],0)

    def test_real_uncalibrated_policy_cannot_enable_automation(self):
        policy=json.loads((ROOT/'outputs/recognition_threshold.json').read_text(encoding='utf-8'))
        auto=AutomaticAttendance(self.service,True)
        self.assertFalse(auto.verify({},policy['pipeline_signature'],'retinaface_reuse',policy))
        status=auto.status(policy,policy['pipeline_signature'],'retinaface_reuse')
        self.assertFalse(status['ready'])
        self.assertIn('labelled_unknown_samples_missing',status['blocking_reasons'])

    def test_two_student_sql_transition_and_restart(self):
        # Storage primitive only: this is NOT an assertion of biometric verification.
        self.service.finish(self.sid)
        c=self.service.detail(self.sid)['class_id']
        self.store.membership(c,self.people[1]['id'])
        sid=self.service.create(dict(class_id=c,session_name='Two-person SQL test',request_key='two-student-sql'))['id']
        self.assertEqual(self.service.detail(sid)['stats']['total'],2)
        r=self.service.confirm_verified(sid,[self.people[0]['id']],lambda:False,time.monotonic(),lambda:True)
        self.assertEqual(r['session']['stats'],dict(total=2,present=1,pending=1,absent=0))
        reopened=AttendanceService(ManagementStore(self.store.path,ROOT/'outputs/face_embeddings.json'))
        self.assertEqual(reopened.detail(sid)['stats']['present'],1)

    def test_automatic_service_contract_with_explicit_protocol_fixture(self):
        # Protocol fixture only, NOT evidence of ArcFace identity verification.
        # No production policy or attendance database is changed.
        automatic=AutomaticAttendance(self.service,True)
        face=dict(identity_confirmed=True,outside_class=False,eligible_for_confirmation=True,
            top1={'student':{'id':self.people[0]['id']}})
        stale=automatic.apply(self.sid,[face],lambda:False,time.monotonic()-8,lambda:False)
        self.assertFalse(stale['changed'])
        self.assertEqual(self.service.detail(self.sid)['stats']['present'],0)
        for key in ('identity_confirmed','eligible_for_confirmation'):
            rejected=automatic.apply(self.sid,[dict(face,**{key:False})],lambda:False,time.monotonic(),lambda:True)
            self.assertFalse(rejected['changed'])
        self.assertFalse(automatic.apply(self.sid,[dict(face,outside_class=True)],lambda:False,time.monotonic(),lambda:True)['changed'])
        committed=automatic.apply(self.sid,[face],lambda:False,time.monotonic()-8,lambda:True)
        self.assertTrue(committed['changed'])
        self.assertEqual(committed['session']['records'][0]['recognition_source'],'AI_VERIFIED')
        self.assertFalse(automatic.apply(self.sid,[face],lambda:False,time.monotonic(),lambda:True)['changed'])

    def test_reviewed_static_batch_is_manual_atomic_and_open_only(self):
        with self.assertRaises(DomainError):self.service.confirm_reviewed(self.sid,[p['id'] for p in self.people])
        self.assertEqual(self.service.detail(self.sid)['stats']['present'],0)
        result=self.service.confirm_reviewed(self.sid,[self.people[0]['id']]*2)
        self.assertEqual(result['changed'],1)
        self.assertEqual(result['session']['records'][0]['recognition_source'],'MANUAL')
        self.assertEqual(self.service.confirm_reviewed(self.sid,[self.people[0]['id']])['changed'],0)
        self.service.finish(self.sid)
        with self.assertRaises(DomainError):self.service.confirm_reviewed(self.sid,[self.people[0]['id']])


if __name__=='__main__':unittest.main()
