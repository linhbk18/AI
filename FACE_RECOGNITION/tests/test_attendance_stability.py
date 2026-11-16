"""Policy/protocol tests only; no claim of biometric accuracy."""
import unittest
import hashlib
import json
import tempfile
from pathlib import Path
from backend.attendance.automatic import AutomaticAttendance
from backend.attendance.stability import observe
from backend.face_recognition.results import normalize_faces

class Stability(unittest.TestCase):
    def run_window(self, values):
        state={}
        for sequence, value in enumerate(values,1):state, result=observe(state,value,sequence)
        return state,result

    def test_full_window_required(self):
        self.assertFalse(self.run_window([1]*6)[1]['ready'])
        self.assertTrue(self.run_window([1]*7)[1]['ready'])

    def test_five_of_seven_and_current_frame(self):
        self.assertTrue(self.run_window([1,None,1,None,1,1,1])[1]['ready'])
        self.assertFalse(self.run_window([1,1,1,1,1,1,None])[1]['ready'])
        self.assertFalse(self.run_window([1,None,None,None,1,1,1])[1]['ready'])

    def test_replacement_and_sequence_gap_reset(self):
        self.assertFalse(self.run_window([1]*7+[2])[1]['ready'])
        state,_=self.run_window([1]*7)
        state,result=observe(state,1,9)
        self.assertFalse(result['ready'])
        self.assertEqual(result['collected'],1)

    def test_present_record_does_not_verify_a_candidate(self):
        face=dict(top1=dict(student=dict(id=1,student_code='A',full_name='Person')),box={},identity_confirmed=False)
        row=normalize_faces([face],'live_camera',[dict(student_id=1,attendance_status='PRESENT')])[0]
        self.assertEqual(row['verification_status'],'UNKNOWN')
        self.assertIsNone(row['student_name'])

class FixedEvidenceGate(unittest.TestCase):
    def test_report_cannot_relax_fixed_project_criteria(self):
        # Protocol fixture only; not an evaluation of any person or recognition model.
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'protocol.json'
            keys=('unknown_false_accept_rate','known_false_rejection_rate','known_wrong_identity_rate')
            report=dict(pipeline_signature='protocol',camera_profile='test',independent_holdout=True,
                approved_for_automatic_attendance=True,min_similarity=.8,min_margin=.2,
                known_samples=100,unknown_samples=100,holdout_webcam_frames=200,
                samples=[dict(identity='UNKNOWN:'+str(i)) for i in range(10)],
                temporal_evaluation=dict(required=5,window=7,unknown_attendance_false_accepts=0,
                    known_attendance_wrong_identity=0,known_trials=1,unknown_trials=1),
                acceptance_targets={k:.5 for k in keys},upper95={k:.05 for k in keys},
                **{k:0 for k in keys})
            policy=dict(status='official',enabled=True,pipeline_signature='protocol',min_similarity=.8,min_margin=.2,
                evidence=dict(labelled_unknown_validation_samples=100,labelled_known_validation_samples=100,
                    independent_evaluation_completed=True,test_used_for_threshold_optimization=False))
            auto=AutomaticAttendance(None,True,path)
            def valid(value):
                path.write_text(json.dumps(value),encoding='utf-8')
                policy['evidence']['automatic_evaluation_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                return auto._evidence_valid('protocol','test',policy)
            self.assertTrue(valid(report))
            self.assertFalse(valid(dict(report,unknown_false_accept_rate=.02)))
            self.assertFalse(valid(dict(report,known_wrong_identity_rate=.001)))
            self.assertFalse(valid(dict(report,known_false_rejection_rate=.11)))
            self.assertFalse(valid(dict(report,holdout_webcam_frames=199)))
            self.assertFalse(valid(dict(report,samples=report['samples'][:9])))
            self.assertFalse(valid(dict(report,temporal_evaluation=dict(report['temporal_evaluation'],window=2))))
            self.assertFalse(valid(dict(report,temporal_evaluation=dict(report['temporal_evaluation'],unknown_attendance_false_accepts=1))))

if __name__=='__main__':unittest.main()
