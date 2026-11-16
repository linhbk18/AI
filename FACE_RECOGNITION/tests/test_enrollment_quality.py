"""Fast synthetic checks for the camera enrollment quality and landmark gates."""
import unittest

from backend.face_recognition.enrollment_quality import (
    face_box_feedback,frame_quality_feedback,estimate_head_pose,posture_matches,
)


class EnrollmentQualityTests(unittest.TestCase):
    def test_face_box_confidence_size_and_edges(self):
        self.assertIsNone(face_box_feedback(640,480,180,80,150,180,.8))
        self.assertIn('Lại gần',face_box_feedback(640,480,180,80,110,150,.8))
        self.assertIn('Lùi ra xa',face_box_feedback(640,480,20,80,500,300,.8))
        self.assertIn('nằm trọn',face_box_feedback(640,480,0,80,150,180,.8))
        self.assertIsNotNone(face_box_feedback(640,480,180,80,150,180,.59))

    def test_quality_threshold_feedback(self):
        self.assertIsNone(frame_quality_feedback(75,130))
        self.assertIn('Giữ yên',frame_quality_feedback(59.9,130))
        self.assertIn('ánh sáng',frame_quality_feedback(75,69))
        self.assertIn('ánh sáng',frame_quality_feedback(75,201))

    def test_landmark_pose_gates_and_signs(self):
        frontal=estimate_head_pose((60,40),(40,40),(50,55),(62,70),(38,70))
        self.assertTrue(posture_matches('thẳng',*frontal))
        left=estimate_head_pose((60,40),(40,40),(56,55),(62,70),(38,70))
        right=estimate_head_pose((60,40),(40,40),(44,55),(62,70),(38,70))
        self.assertTrue(posture_matches('quay_trái',*left))
        self.assertTrue(posture_matches('quay_phải',*right))
        up=estimate_head_pose((60,40),(40,40),(50,51),(62,70),(38,70))
        down=estimate_head_pose((60,40),(40,40),(50,60),(62,70),(38,70))
        self.assertTrue(posture_matches('ngẩng_nhẹ',*up))
        self.assertTrue(posture_matches('cúi_nhẹ',*down))
        self.assertFalse(posture_matches('quay_trái',*right))


if __name__=='__main__':
    unittest.main()
