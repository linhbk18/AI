"""Label persistence tests on disposable copies. Test choices are NOT dataset ground truth."""
from contextlib import closing
import csv
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from backend.face_recognition.verification_workflow import write

from backend.database.db import ManagementStore,DomainError
from backend.test_labeling import GroupLabeling
from backend.face_recognition.verification_workflow import group_calibration

ROOT=Path(__file__).resolve().parents[1]


class Labels(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.venv',prefix='label-unit-');self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)/'labels';shutil.copytree(ROOT/'outputs/test_labeling',base)
        path=Path(self.tmp.name)/'copy.sqlite3'
        with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as src,closing(sqlite3.connect(path)) as dst:src.backup(dst)
        self.manager=GroupLabeling(None,ManagementStore(path,ROOT/'outputs/face_embeddings.json'),base)
        self.original=json.loads((ROOT/'outputs/test_labeling/labels.json').read_text(encoding='utf-8'))
        manifest=self.manager.read('manifest.json',{})
        self.expected=sum(len(im['faces']) for im in manifest['images'])
        # Reset ONLY the disposable test copy. Never overwrite the user's real labels.
        fixture=dict(revision=0,faces={f['face_id']:dict(label='UNLABELED',unknown_person_id='',note='',label_source='human_required') for im in manifest['images'] for f in im['faces']})
        write(base/'labels.json',fixture);self.manager.export_csv(manifest,fixture)

    def test_all_defaults_are_unlabelled_without_predictions(self):
        state=self.manager.state();self.assertEqual(state['counts']['UNLABELED'],self.expected)
        self.assertEqual(state['counts']['KNOWN']+state['counts']['UNKNOWN'],0)
        self.assertTrue(all('prediction' not in f and 'similarity' not in f for im in state['manifest']['images'] for f in im['faces']))

    def test_save_ignore_roundtrip_csv_revision_and_invalid_batch(self):
        state=self.manager.state();fid=next(iter(state['labels']['faces']))
        result=self.manager.save_labels(dict(revision=0,faces={fid:dict(label='IGNORE',note='Persistence test on temporary copy only')}))
        self.assertEqual(result['faces'][fid]['label'],'IGNORE')
        with (self.manager.base/'labels.csv').open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
        self.assertEqual(next(r for r in rows if r['face_id']==fid)['label'],'IGNORE')
        with self.assertRaises(DomainError):self.manager.save_labels(dict(revision=0,faces={fid:dict(label='UNKNOWN')}))
        with self.assertRaises(DomainError):self.manager.save_labels(dict(revision=1,faces={fid:dict(label='IGNORE'),'missing-face':dict(label='DEMO-SV001')}))
        self.assertEqual(self.manager.state()['labels']['revision'],1)
        self.assertEqual(json.loads((ROOT/'outputs/test_labeling/labels.json').read_text(encoding='utf-8')),self.original)

    def test_manual_box_updates_numbered_copy_and_preserves_original(self):
        state=self.manager.state();image=state['manifest']['images'][0];asset=self.manager.base/'assets'/image['numbered_asset'];before=asset.read_bytes()
        out=self.manager.add_box(dict(revision=0,image_id=image['image_id'],box=dict(x=10,y=10,w=40,h=50)))
        self.assertEqual(len(out['manifest']['images'][0]['faces']),len(image['faces'])+1)
        self.assertNotEqual(before,asset.read_bytes())
        self.assertEqual(out['counts']['UNLABELED'],self.expected+1)

    def test_group_scores_cannot_be_imported_before_human_scoring(self):
        (self.manager.base/'evaluation.json').unlink(missing_ok=True)
        with self.assertRaises(FileNotFoundError):group_calibration(self.manager.base/'evaluation.json')

    def test_anonymous_person_reuse_and_invalid_codes_are_atomic(self):
        ids=list(self.manager.state()['labels']['faces'])[:2]
        saved=self.manager.save_labels(dict(revision=0,faces={fid:dict(label='UNKNOWN',unknown_person_id='U01') for fid in ids}))
        self.assertEqual({saved['faces'][fid]['unknown_person_id'] for fid in ids},{'U01'})
        before=(self.manager.base/'labels.json').read_bytes()
        for label,code in [('UNKNOWN','../invalid'),('UNKNOWN','x'*65),('IGNORE','U01')]:
            with self.assertRaises(DomainError):self.manager.save_labels(dict(revision=1,faces={ids[0]:dict(label=label,unknown_person_id=code)}))
            self.assertEqual((self.manager.base/'labels.json').read_bytes(),before)


if __name__=='__main__':unittest.main()
