"""Per-face identity verification policy (similarity + margin) loaded from backend/attendance_policy.json.

Fail closed: any missing/invalid value means NO face can be verified.
Verification is computed against the GLOBAL gallery (all registered students),
never against a class-restricted subset, so an outsider can never be "pulled"
onto the nearest classmate.
"""
import hashlib
import json
import math
from pathlib import Path

from .face_service import pipeline_signature

POLICY_PATH = Path(__file__).resolve().parents[1] / 'attendance_policy.json'


def _number(value, low, high):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and low <= value <= high)


class VerificationPolicy:
    def __init__(self, data, face_config=None, source_sha256=None):
        self.data = data if isinstance(data, dict) else {}
        self.sha256 = source_sha256
        v = self.data.get('verification') or {}
        s = self.data.get('stability') or {}
        f = self.data.get('freshness') or {}
        a = self.data.get('automatic') or {}
        self.min_similarity = v.get('min_similarity')
        self.min_margin = v.get('min_margin')
        self.calibrated_profile = v.get('calibrated_profile')
        self.calibration_pipeline_signature = v.get('calibration_pipeline_signature')
        self.required = s.get('required_consistent_results')
        self.window = s.get('window')
        self.result_max_age_s = f.get('result_max_age_s')
        self.track_max_gap_s = f.get('track_max_gap_s')
        self.require_independent_evaluation = a.get('require_independent_evaluation') is not False
        self.require_calibrated_gallery = a.get('require_calibrated_gallery') is not False
        reasons = []
        if not _number(self.min_similarity, -1, 1) or not _number(self.min_margin, 0, 2):
            reasons.append('verification_thresholds_missing_or_invalid')
        if not isinstance(self.calibrated_profile, str) or not self.calibrated_profile:
            reasons.append('calibrated_profile_missing')
        if (isinstance(self.required, bool) or isinstance(self.window, bool) or not isinstance(self.required, int)
                or not isinstance(self.window, int) or not 1 <= self.required <= self.window <= 50):
            reasons.append('stability_policy_invalid')
        if not _number(self.result_max_age_s, 0.1, 30) or not _number(self.track_max_gap_s, 0.1, 30):
            reasons.append('freshness_policy_invalid')
        if face_config is not None:
            try:
                if pipeline_signature(face_config, 'model-only') != v.get('model_signature'):
                    reasons.append('model_pipeline_mismatch')
            except (KeyError, TypeError):
                reasons.append('model_pipeline_mismatch')
        self.reasons = reasons
        self.valid = not reasons

    @classmethod
    def load(cls, path=POLICY_PATH, face_config=None):
        try:
            raw = Path(path).read_bytes()
            return cls(json.loads(raw), face_config, hashlib.sha256(raw).hexdigest())
        except (OSError, ValueError):
            policy = cls({}, None, None)
            policy.reasons = ['verification_policy_file_missing_or_unreadable']
            policy.valid = False
            return policy

    def profile_reasons(self, profile):
        return [] if profile == self.calibrated_profile else ['camera_profile_not_calibrated']

    def verify(self, best_similarity, margin, profile):
        """Return (verified, reasons). Inclusive thresholds, exactly as calibrated."""
        reasons = list(self.reasons) + self.profile_reasons(profile)
        if not _number(best_similarity, -1, 1):
            reasons.append('similarity_missing')
        elif self.valid and best_similarity < self.min_similarity:
            reasons.append('similarity_below_threshold')
        if not _number(margin, 0, 2):
            reasons.append('margin_missing')
        elif self.valid and margin < self.min_margin:
            reasons.append('margin_below_threshold')
        return (not reasons), reasons

    def public(self):
        return dict(valid=self.valid, reasons=list(self.reasons), min_similarity=self.min_similarity,
                    min_margin=self.min_margin, calibrated_profile=self.calibrated_profile,
                    required_consistent_results=self.required, consistency_window=self.window,
                    result_max_age_s=self.result_max_age_s, track_max_gap_s=self.track_max_gap_s,
                    require_independent_evaluation=self.require_independent_evaluation,
                    require_calibrated_gallery=self.require_calibrated_gallery, sha256=self.sha256)
