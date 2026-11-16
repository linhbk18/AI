"""Disposable SQLite import tests. No AI inference and no production mutations."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.database.db import ManagementStore,DomainError
from backend.database.import_legacy_students import import_students,audit_links,ROSTER
from backend.face_recognition.face_service import FaceMatcher

ROOT=Path(__file__).resolve().parents[1]


class StudentImport(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='student-import-',dir=ROOT/'.venv');self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'test.sqlite3'
        self.store=ManagementStore(self.path,ROOT/'outputs/face_embeddings.json')
        self.config=json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8'))
        self.label=ROSTER[0]['dataset_label']

    def run_import(self,**kwargs):return import_students(self.store,self.config,**kwargs)
    def counts(self):
        with self.store.connection() as db:
            return tuple(db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('students','legacy_mappings','face_embeddings'))

    def test_exact_five_92_and_repeat_is_noop(self):
        result=self.run_import();self.assertEqual(result['created_students'],5);self.assertEqual(self.counts(),(5,5,92))
        revision=self.store.gallery_revision()
        again=self.run_import();self.assertEqual(again['created_students'],0);self.assertEqual(again['reference_or_mapping_changes'],0)
        self.assertEqual(self.counts(),(5,5,92));self.assertEqual(self.store.gallery_revision(),revision)
        self.assertTrue(all(s['code_is_temporary'] for s in result['students']))

    def test_existing_mapping_code_and_display_name_are_preserved(self):
        s=self.store.save('students',dict(student_code='REAL-EXISTING',full_name='Tên hiển thị đã chỉnh'))
        self.store.map_legacy(s['id'],self.label,True)
        result=self.run_import();row=result['students'][0]
        self.assertEqual(result['created_students'],4)
        self.assertEqual(row['student_code'],'REAL-EXISTING');self.assertEqual(row['full_name'],'Tên hiển thị đã chỉnh')
        self.assertFalse(row['code_is_temporary']);self.assertEqual(self.counts(),(5,5,92))

    def test_unmapped_same_name_never_merges_automatically(self):
        self.store.save('students',dict(student_code='SV1',full_name=self.label.upper()))
        with self.assertRaisesRegex(DomainError,'--link'):self.run_import()
        self.assertEqual(self.counts(),(1,0,0))

    def test_explicit_link_preserves_existing_upload_class_and_student(self):
        s=self.store.save('students',dict(student_code='SV1',full_name=self.label.upper()))
        c=self.store.save('classes',dict(class_code='EXISTING',class_name='Existing'))
        self.store.membership(c['id'],s['id'])
        self.store.add_embedding(s['id'],[1.]*512,{},'previous-upload')
        result=self.run_import(explicit_links={self.label:s['id']})
        self.assertEqual(result['created_students'],4);self.assertEqual(self.counts(),(5,5,93))
        self.assertEqual(result['students'][0]['additional_embedding_count'],1)
        self.assertEqual(len(self.store.detail('classes',c['id'])['students']),1)

    def test_explicit_keep_separate_does_not_merge_names(self):
        self.store.save('students',dict(student_code='SV1',full_name=self.label))
        self.run_import(new_labels=[self.label]);self.assertEqual(self.counts(),(6,5,92))

    def test_edit_code_and_name_preserves_mapping_and_vectors(self):
        result=self.run_import();student=result['students'][0]
        self.store.save('students',dict(student_code='OFFICIAL-123',full_name='Tên hiển thị mới'),student['student_id'])
        again=self.run_import();row=again['students'][0]
        self.assertEqual(row['student_code'],'OFFICIAL-123');self.assertFalse(row['code_is_temporary'])
        self.assertEqual(row['legacy_embedding_count'],20);self.assertEqual(row['dataset_label'],self.label)
        records,_=self.store.gallery_snapshot()
        self.assertEqual(sum(r['student_id']=='OFFICIAL-123 · Tên hiển thị mới' for r in records),20)

    def test_code_collision_rolls_back_entire_roster(self):
        self.store.save('students',dict(student_code=ROSTER[3]['temporary_code'],full_name='Unrelated student'))
        with self.assertRaises(DomainError):self.run_import()
        self.assertEqual(self.counts(),(1,0,0))

    def test_dry_run_has_no_profile_or_reference_mutation(self):
        result=self.run_import(dry_run=True);self.assertEqual(result['status'],'dry_run');self.assertEqual(self.counts(),(0,0,0))
        self.assertEqual(self.store.gallery_revision(),0)

    def test_missing_nonfinite_wrong_label_rejected_before_import(self):
        baseline=copy.deepcopy(self.store.legacy)
        for kind in ('missing','nan','source'):
            self.store.legacy=copy.deepcopy(baseline)
            if kind=='missing':self.store.legacy.pop()
            elif kind=='nan':self.store.legacy[0]['embedding'][0]=float('nan')
            else:self.store.legacy[0]['source']='DATASET/validation/wrong.jpg'
            with self.assertRaises(DomainError):self.run_import()
            self.assertEqual(self.counts(),(0,0,0))

    def test_reordered_records_link_by_label_not_order(self):
        self.store.legacy.reverse();self.run_import()
        with self.store.connection() as db:self.assertEqual(sum(r['legacy_embedding_count'] for r in audit_links(self.store,db)),92)
        records,_=self.store.gallery_snapshot();matcher=FaceMatcher(records,self.config,'testing',None)
        for item in ROSTER:
            query=next(r['embedding'] for r in self.store.legacy if r['student_id']==item['dataset_label'])
            self.assertEqual(matcher.match(query)['best_candidate'],item['temporary_code']+' · '+item['display_name'])

    def test_all_five_can_join_one_class_and_survive_reopen(self):
        self.run_import();c=self.store.save('classes',dict(class_code='QA-ONLY',class_name='Temporary test class'))
        students=self.store.list_entities('students')
        for s in students:self.store.membership(c['id'],s['id'])
        reopened=ManagementStore(self.path,ROOT/'outputs/face_embeddings.json')
        self.assertEqual(len(reopened.detail('classes',c['id'])['students']),5)
        with reopened.connection() as db:self.assertEqual(sum(r['legacy_embedding_count'] for r in audit_links(reopened,db)),92)

    def test_wrong_existing_reference_fails_without_overwrite(self):
        self.run_import()
        with self.store.connection() as db:
            ids=[r[0] for r in db.execute('SELECT id FROM students ORDER BY id')]
            db.execute("UPDATE face_embeddings SET student_id=? WHERE embedding_reference='legacy:0'",(ids[1],))
        with self.assertRaises(DomainError):self.run_import()
        self.assertEqual(self.counts(),(5,5,92))


if __name__=='__main__':unittest.main()
