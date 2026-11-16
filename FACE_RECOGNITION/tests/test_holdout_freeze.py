"""Workflow isolation checks only; these fixtures are not biometric evidence."""
import tempfile
from pathlib import Path
import unittest

from backend.face_recognition.verification_workflow import reserve_holdout


class HoldoutFreezeTests(unittest.TestCase):
    def test_freeze_before_exposure_and_reject_retuning_or_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'registry.json'
            rows=[dict(sha256='a',identity='UNKNOWN:U01'),dict(sha256='b',identity='STUDENT')]
            trial=dict(profile='fixed',min_similarity=.6,min_margin=.2,targets={'far':.01})
            key,_=reserve_holdout(path,rows,trial)
            self.assertTrue(path.exists())
            self.assertEqual(reserve_holdout(path,rows,trial)[0],key)
            for samples,config in ((rows,dict(trial,min_margin=.1)),(rows[:1],trial),([dict(rows[0],identity='UNKNOWN:renamed')],trial)):
                before=path.read_bytes()
                with self.assertRaisesRegex(ValueError,'already exposed'):reserve_holdout(path,samples,config)
                self.assertEqual(path.read_bytes(),before)
            reserve_holdout(path,[dict(sha256='new',identity='UNKNOWN:NEW')],dict(trial,min_margin=.3))


if __name__=='__main__':unittest.main()
