"""Fail-closed automatic attendance. Only server-produced, evaluated matches write SQL."""
import hashlib
import json
import math
import time
from pathlib import Path


class AutomaticAttendance:
    def __init__(self,attendance,enabled=False,evidence_path=None,activation_path=None):
        self.attendance=attendance
        self._requested=enabled is True
        self.activation_path=Path(activation_path) if activation_path else None
        self.evidence_path=Path(evidence_path) if evidence_path else None
        self.reason='automatic_mode_disabled_pending_independent_known_and_unknown_evaluation'

    @property
    def requested(self):
        if self.activation_path and self.activation_path.exists():
            try:return json.loads(self.activation_path.read_text(encoding='utf-8')).get('enabled') is True
            except (OSError,ValueError,AttributeError):return False
        return self._requested

    def status(self,policy=None,signature=None,profile=None):
        policy=policy or {};evidence=policy.get('evidence') or {};reasons=[]
        if not self.requested:reasons.append('automatic_mode_disabled')
        if policy.get('status')!='official' or policy.get('enabled') is not True:reasons.append('official_policy_missing')
        if policy.get('min_similarity') is None or policy.get('min_margin') is None:reasons.append('calibrated_thresholds_missing')
        if evidence.get('labelled_unknown_validation_samples',0)==0:reasons.append('labelled_unknown_samples_missing')
        if evidence.get('independent_evaluation_completed') is not True:reasons.append('independent_evaluation_missing')
        if not self.evidence_path or not self.evidence_path.is_file():reasons.append('automatic_evaluation_report_missing')
        if not signature or signature!=policy.get('pipeline_signature'):reasons.append('pipeline_signature_unavailable_or_mismatched')
        if not reasons and not self._evidence_valid(signature,profile,policy):reasons.append('evaluation_hash_metrics_or_camera_profile_mismatch')
        labels={'automatic_mode_disabled':'cấu hình tự động đang tắt',
            'official_policy_missing':'chưa có policy chính thức','calibrated_thresholds_missing':'chưa có ngưỡng đã kiểm thử',
            'labelled_unknown_samples_missing':'policy chưa có mẫu người lạ đã được đánh giá độc lập','independent_evaluation_missing':'thiếu đánh giá độc lập',
            'automatic_evaluation_report_missing':'thiếu báo cáo xác minh tự động',
            'pipeline_signature_unavailable_or_mismatched':'cấu hình mô hình/gallery chưa khớp hoặc chưa tải',
            'evaluation_hash_metrics_or_camera_profile_mismatch':'báo cáo đánh giá chưa hợp lệ cho camera hiện tại'}
        return dict(requested=self.requested,ready=not reasons,blocking_reasons=reasons,
            known_samples=evidence.get('labelled_known_validation_samples',0),unknown_samples=evidence.get('labelled_unknown_validation_samples',0),
            profile=profile,calibration_evidence=policy.get('calibration_evidence',{}),freshness_mode='continuous_server_track_then_single_use_commit',max_preview_gap_seconds=2.5,max_inference_seconds=30,
            message='Tự động đang khóa: '+ '; '.join(labels[r] for r in reasons)+'. Hiện dùng xác nhận thủ công.' if reasons else 'Đã đủ cấu hình; mỗi khuôn mặt vẫn phải vượt xác minh và kiểm tra tuổi ảnh trước khi ghi Có mặt.')

    def verify(self,match,signature,profile,policy):
        """Global top-two scores, not a roster-restricted nearest candidate."""
        return bool(match.get('decision')=='candidate_passes_gates_manual_review_required'
            and match.get('policy_applied') is True and self._evidence_valid(signature,profile,policy))

    def _evidence_valid(self,signature,profile,policy):
        if not self.requested:return False
        try:
            if not policy or policy.get('status')!='official' or policy.get('enabled') is not True:return False
            if policy.get('pipeline_signature')!=signature:return False
            if self.activation_path and self.activation_path.exists():
                activation=json.loads(self.activation_path.read_text(encoding='utf-8'))
                digest=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()
                if activation.get('policy_sha256')!=digest or activation.get('profile')!=profile:return False
            evidence=policy['evidence']
            if (evidence.get('labelled_unknown_validation_samples',0)<=0 or evidence.get('labelled_known_validation_samples',0)<=0 or
                evidence.get('independent_evaluation_completed') is not True or
                evidence.get('test_used_for_threshold_optimization') is not False):return False
            raw=self.evidence_path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=evidence.get('automatic_evaluation_sha256'):return False
            report=json.loads(raw)
            for artifact in report.get('external_label_artifacts',[]):
                if hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest()!=artifact['sha256']:return False
            if (report['pipeline_signature']!=signature or report['camera_profile']!=profile or
                report['independent_holdout'] is not True or report['approved_for_automatic_attendance'] is not True or
                report['min_similarity']!=policy['min_similarity'] or report['min_margin']!=policy['min_margin']):return False
            # Acceptance targets must come from the reviewed evaluation, never invented here.
            for key in ('unknown_false_accept_rate','known_false_rejection_rate','known_wrong_identity_rate'):
                value=report[key];target=report['acceptance_targets'][key]
                bound=report['upper95'][key]
                if (isinstance(value,bool) or isinstance(target,bool) or not math.isfinite(value) or
                    not math.isfinite(target) or isinstance(bound,bool) or not math.isfinite(bound)
                    or not 0<=value<=bound<=target<=1):return False
            if report['known_samples']<=0 or report['unknown_samples']<=0:return False
            # Fixed project criteria cannot be relaxed by report-supplied targets.
            if report['known_wrong_identity_rate'] != 0:return False
            if report['unknown_false_accept_rate'] > .01 or report['known_false_rejection_rate'] > .10:return False
            if report['known_samples']+report['unknown_samples'] < 200:return False
            samples=report.get('samples',[])
            unknown_people={r['identity'] for r in samples if r.get('identity','').startswith('UNKNOWN:')}
            if len(unknown_people) < 10 or report.get('holdout_webcam_frames',0) < 200:return False
            temporal=report.get('temporal_evaluation',{})
            if (temporal.get('required') != 5 or temporal.get('window') != 7 or
                temporal.get('unknown_attendance_false_accepts') != 0 or
                temporal.get('known_attendance_wrong_identity') != 0 or
                temporal.get('known_trials',0) <= 0 or temporal.get('unknown_trials',0) <= 0):return False
            return True
        except (OSError,TypeError,KeyError,ValueError,AttributeError):return False

    def apply(self,session_id,faces,cancelled,received_at,continuity_check=None):
        # The camera route supplies server-validated continuity at commit time.
        # Calls without current-track proof cannot write, regardless of frame age.
        if not self.requested:return dict(enabled=False,reason=self.reason,changed=False)
        eligible=[f['top1']['student']['id'] for f in faces if f.get('identity_confirmed') is True
            and not f.get('outside_class') and f.get('eligible_for_confirmation') and f.get('top1',{}).get('student')]
        if not eligible:return dict(enabled=False,reason='no_verified_fresh_identity_or_missing_evaluation',changed=False)
        evidence={f['top1']['student']['id']:dict(similarity=f['top1'].get('similarity'),margin=f.get('margin')) for f in faces if f.get('top1',{}).get('student') and f['top1']['student']['id'] in eligible}
        if any(type(v) not in (int,float) or not math.isfinite(v) for scores in evidence.values() for v in scores.values()):
            return dict(enabled=False,changed=False,reason='verification_scores_missing_or_invalid')
        return self.attendance.confirm_verified(session_id,eligible,cancelled,received_at,continuity_check,evidence)
