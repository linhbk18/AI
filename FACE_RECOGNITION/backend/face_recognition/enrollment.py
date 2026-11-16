"""Camera face enrollment logic: quality gates, head pose, diverse sampling.

Pure Python/NumPy (OpenCV only for sharpness), no TensorFlow, so every rule is
unit-testable. Frames and face crops exist only in RAM; only L2-normalised
ArcFace vectors with a pose label are ever handed to persistence.

Pose sign convention (raw, unmirrored camera image):
  yaw  > 0  the student turned to THEIR OWN LEFT (nose moves to image right)
  pitch > 0  the student raised the chin (nose tip moves towards the eye line)
"""
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import secrets
import threading
import time

import numpy as np

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'enrollment_config.json'
POSE_KEYS = ('front', 'left', 'right', 'up', 'down')


def load_config(path=None):
    cfg = json.loads(Path(path or CONFIG_PATH).read_text(encoding='utf-8'))
    keys = [p['key'] for p in cfg['poses']]
    if keys != list(POSE_KEYS):
        raise ValueError('enrollment_config.json phải khai báo đúng 5 tư thế theo thứ tự front,left,right,up,down')
    for pose in cfg['poses']:
        if not isinstance(pose['samples'], int) or not 1 <= pose['samples'] <= 20:
            raise ValueError('Số mẫu mỗi tư thế phải từ 1 đến 20')
    for key in POSE_KEYS:
        r = cfg['pose_ranges_deg'][key]
        if not (r['yaw'][0] < r['yaw'][1] and r['pitch'][0] < r['pitch'][1]):
            raise ValueError(f'Khoảng góc không hợp lệ cho tư thế {key}')
    return cfg


def l2_normalize(vector):
    v = np.asarray(vector, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(v))
    if v.shape != (512,) or not np.isfinite(v).all() or norm == 0:
        raise ValueError('INVALID_EMBEDDING')
    return v / norm


# --------------------------------------------------------------------------- pose
def estimate_pose(face, model):
    """Yaw/pitch/roll in degrees from 5 YuNet landmarks on a generic 3D head.

    face: dict with right_eye, left_eye, nose, mouth_right, mouth_left (x, y)
    in image pixels. right_eye is the subject's right eye (image left side).
    """
    re_, le_ = np.asarray(face['right_eye'], float), np.asarray(face['left_eye'], float)
    nose = np.asarray(face['nose'], float)
    mouth = (np.asarray(face['mouth_right'], float) + np.asarray(face['mouth_left'], float)) / 2
    eye_mid = (re_ + le_) / 2
    roll = math.atan2(le_[1] - re_[1], le_[0] - re_[0])
    c, s = math.cos(-roll), math.sin(-roll)
    rot = np.array([[c, -s], [s, c]])
    eye_vec = rot @ (le_ - re_)
    nose_r, mouth_r = rot @ (nose - eye_mid), rot @ (mouth - eye_mid)
    iod = float(eye_vec[0])
    span = float(mouth_r[1])
    if iod <= 1 or span <= 1:
        raise ValueError('DEGENERATE_LANDMARKS')
    yaw_ratio = float(nose_r[0]) / iod
    nose_ratio = float(nose_r[1]) / span
    # Pitch first (vertical landmark ratios do not change with yaw):
    #   ratio(phi) = (E - d_e t) / (E + M - (d_e - d_m) t), t = tan(phi)
    # with E the nose->eye height share; solved for t around the neutral ratio.
    big = model['eye_mouth_span']
    d_e, d_m = model['nose_depth_eye'], model['nose_depth_mouth']
    r0 = model['neutral_nose_ratio']
    t = (r0 - nose_ratio) * big / (d_e - (d_e - d_m) * nose_ratio)
    phi = math.atan(t)
    pitch = math.degrees(phi)
    # Yaw: nose-to-eye depth seen by the camera grows when the chin is raised
    # (eyes sit above the nose tip), so correct the depth ratio by pitch.
    depth = max(0.3, math.cos(phi) + (r0 * big / d_e) * math.sin(phi))
    yaw = math.degrees(math.atan(yaw_ratio / (model['yaw_depth_ratio'] * depth)))
    return dict(yaw=yaw, pitch=pitch, roll=math.degrees(roll), yaw_ratio=yaw_ratio,
                nose_ratio=nose_ratio, eye_distance=iod)


def pose_guidance(pose_key, yaw, pitch, ranges):
    """Return (matches, Vietnamese hint) for the requested pose only."""
    r = ranges[pose_key]
    (y0, y1), (p0, p1) = r['yaw'], r['pitch']
    if pose_key == 'front':
        if yaw > y1: return False, 'Quay mặt sang phải một chút để nhìn thẳng vào camera.'
        if yaw < y0: return False, 'Quay mặt sang trái một chút để nhìn thẳng vào camera.'
        if pitch > p1: return False, 'Hạ cằm xuống một chút, nhìn thẳng vào camera.'
        if pitch < p0: return False, 'Nâng cằm lên một chút, nhìn thẳng vào camera.'
        return True, 'Tốt lắm, giữ nguyên tư thế nhìn thẳng.'
    if pose_key in ('left', 'right'):
        word = 'trái' if pose_key == 'left' else 'phải'
        sign = 1 if pose_key == 'left' else -1
        toward = sign * yaw
        low, high = (y0, y1) if pose_key == 'left' else (-y1, -y0)
        if toward < low: return False, f'Quay đầu thêm sang bên {word} của bạn.'
        if toward > high: return False, f'Quay quá nhiều rồi, quay lại về giữa một chút.'
        if pitch > p1: return False, 'Hạ cằm xuống một chút.'
        if pitch < p0: return False, 'Nâng cằm lên một chút.'
        return True, f'Tốt lắm, giữ nguyên tư thế quay {word}.'
    if yaw > y1 or yaw < y0:
        return False, 'Giữ mặt hướng thẳng về camera, chỉ ' + ('ngẩng' if pose_key == 'up' else 'cúi') + ' đầu.'
    if pose_key == 'up':
        if pitch < p0: return False, 'Ngẩng mặt lên thêm một chút.'
        if pitch > p1: return False, 'Ngẩng quá cao, hạ xuống một chút.'
        return True, 'Tốt lắm, giữ nguyên tư thế ngẩng nhẹ.'
    if pitch > p1: return False, 'Cúi mặt xuống thêm một chút.'
    if pitch < p0: return False, 'Cúi quá thấp, nâng lên một chút.'
    return True, 'Tốt lắm, giữ nguyên tư thế cúi nhẹ.'


# ------------------------------------------------------------------------ quality
def face_crop(image, box):
    h, w = image.shape[:2]
    x, y = max(0, int(box['x'])), max(0, int(box['y']))
    r, b = min(w, int(box['x'] + box['w'])), min(h, int(box['y'] + box['h']))
    if r <= x or b <= y:
        return None
    return image[y:b, x:r]


def sharpness_and_brightness(bgr, box, q):
    import cv2
    crop = face_crop(bgr, box)
    if crop is None or crop.size == 0:
        return 0.0, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    brightness = float(gray.mean())
    target = int(q['sharpness_face_width_px'])
    if gray.shape[1] != target:
        scale = target / gray.shape[1]
        gray = cv2.resize(gray, (target, max(1, round(gray.shape[0] * scale))),
                          interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var()), brightness


def assess_frame(bgr, faces, cfg, measure=sharpness_and_brightness):
    """Quality gate for one frame. faces: YuNet detections with landmarks.

    Returns dict(ok, code, message, face, metrics). Every failure carries a
    Vietnamese, student-facing instruction. Hard gates are never relaxed.
    """
    q = cfg['quality']
    height, width = bgr.shape[:2]
    faces = [f for f in faces if f['score'] >= q['detector_score_threshold']]
    out = dict(ok=False, face=None, metrics={}, face_count=len(faces))
    if not faces:
        return dict(out, code='NO_FACE', message='Không thấy khuôn mặt. Hãy đưa mặt vào giữa khung hình.')
    if len(faces) > 1:
        return dict(out, code='MULTIPLE_FACES', message='Có nhiều khuôn mặt trong khung. Chỉ một sinh viên đứng trước camera.')
    face = faces[0]
    box = face['box']
    out['face'] = face
    metrics = out['metrics']
    metrics['detection_confidence'] = round(float(face['score']), 3)
    metrics['face_width_px'] = int(box['w'])
    metrics['face_width_ratio'] = round(box['w'] / width, 3)
    if face['score'] < q['min_detection_confidence']:
        return dict(out, code='LOW_CONFIDENCE', message='Khuôn mặt chưa rõ. Nhìn về phía camera và giữ yên.')
    margin = q['edge_margin_ratio'] * width
    inside = (box['x'] >= margin and box['y'] >= margin and box['x'] + box['w'] <= width - margin
              and box['y'] + box['h'] <= height - margin)
    if box['w'] > q['max_face_width_ratio'] * width:
        return dict(out, code='TOO_CLOSE', message='Lùi ra xa camera một chút.')
    if not inside:
        return dict(out, code='NOT_INSIDE', message='Khuôn mặt bị cắt ở mép. Di chuyển vào giữa khung hình.')
    if box['w'] < max(q['min_face_width_ratio'] * width, q['min_face_width_px']):
        return dict(out, code='TOO_SMALL', message='Lại gần camera hơn.')
    try:
        pose = estimate_pose(face, cfg['pose_model'])
    except ValueError:
        return dict(out, code='NO_LANDMARKS', message='Không xác định được mắt, mũi, miệng. Nhìn về phía camera.')
    if pose['eye_distance'] < q['min_eye_distance_px']:
        return dict(out, code='TOO_SMALL', message='Lại gần camera hơn.')
    metrics.update(yaw=round(pose['yaw'], 1), pitch=round(pose['pitch'], 1), roll=round(pose['roll'], 1))
    sharp, bright = measure(bgr, box, q)
    metrics['sharpness'] = round(sharp, 1)
    metrics['brightness'] = round(bright, 1)
    if bright < q['min_brightness']:
        return dict(out, code='TOO_DARK', message='Ánh sáng quá tối. Bật thêm đèn hoặc quay mặt về phía nguồn sáng.', pose=pose)
    if bright > q['max_brightness']:
        return dict(out, code='TOO_BRIGHT', message='Khuôn mặt bị chói sáng. Tránh đèn chiếu thẳng hoặc ngược sáng.', pose=pose)
    if sharp < q['min_laplacian_variance']:
        return dict(out, code='BLURRY', message='Hình bị mờ. Giữ yên đầu trong giây lát.', pose=pose)
    return dict(out, ok=True, code='OK', message='Chất lượng tốt.', pose=pose)


def quality_score(metrics, q):
    """0..1 summary used only to rank/record accepted samples, never to relax gates."""
    sharp = min(1.0, metrics.get('sharpness', 0) / (q['min_laplacian_variance'] * 4))
    centre = (q['min_brightness'] + q['max_brightness']) / 2
    half = (q['max_brightness'] - q['min_brightness']) / 2
    light = max(0.0, 1 - abs(metrics.get('brightness', centre) - centre) / half)
    size = min(1.0, metrics.get('face_width_ratio', 0) / 0.35)
    return round((sharp + light + size + metrics.get('detection_confidence', 0)) / 4, 3)


# ------------------------------------------------------------------------ session
@dataclass
class Sample:
    vector: np.ndarray
    pose: str
    yaw: float
    pitch: float
    quality: float
    captured_at: float
    embedding_ms: float


@dataclass
class EnrollmentSession:
    cfg: dict
    mode: str  # 'new' | 'reenroll'
    subject: dict
    created_at: float = field(default_factory=time.monotonic)
    session_id: str = field(default_factory=lambda: secrets.token_urlsafe(24))

    def __post_init__(self):
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.reset(self.created_at)

    # -- lifecycle ----------------------------------------------------------
    def reset(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self.generation = getattr(self, 'generation', 0) + 1
            self.samples = {k: [] for k in POSE_KEYS}
            self.index = 0
            self.state = 'collecting'
            self.pending = False
            self.last_attempt = -1e9
            self.last_seen = now
            self.started_at = now
            self.completed_at = None
            self.baseline = dict(yaw=0.0, pitch=0.0)
            self.rejections = {}
            self.embedding_ms = []
            self.frames = 0
            self.verification = None
            self._start_pose(now)

    def _start_pose(self, now):
        self.pose_active_s = 0.0
        self.pose_last_frame = None

    @property
    def pose_key(self):
        return POSE_KEYS[self.index] if self.index < len(POSE_KEYS) else None

    def required(self, key):
        return next(p['samples'] for p in self.cfg['poses'] if p['key'] == key)

    def retry_pose(self, now=None):
        with self.lock:
            if self.state == 'pose_timeout':
                self.state = 'collecting'
                self._start_pose(time.monotonic() if now is None else now)

    def touch(self, now):
        self.last_seen = now

    # -- per-frame ----------------------------------------------------------
    def relative_pose(self, pose):
        return pose['yaw'] - self.baseline['yaw'], pose['pitch'] - self.baseline['pitch']

    def observe(self, assessment, now):
        """Apply one assessed frame. Returns public guidance + whether to embed.

        At most one embedding job is in flight (bounded queue of size 1); a new
        sample needs the minimum interval since the previous attempt.
        """
        c = self.cfg['collection']
        with self.lock:
            self.touch(now)
            self.frames += 1
            result = dict(request_embedding=False, pose_match=False, guidance=assessment['message'],
                          code=assessment['code'], generation=self.generation)
            if self.state != 'collecting':
                return result
            face_present = assessment['code'] not in ('NO_FACE',)
            if face_present and self.pose_last_frame is not None:
                self.pose_active_s += min(1.0, max(0.0, now - self.pose_last_frame))
            self.pose_last_frame = now if face_present else None
            if self.pose_active_s >= c['pose_timeout_s']:
                self.state = 'pose_timeout'
                result['guidance'] = 'Hết thời gian cho tư thế này. Nhấn "Thử lại tư thế" để tiếp tục.'
                return result
            if not assessment['ok']:
                self.rejections[assessment['code']] = self.rejections.get(assessment['code'], 0) + 1
                return result
            yaw, pitch = self.relative_pose(assessment['pose'])
            match, hint = pose_guidance(self.pose_key, yaw, pitch, self.cfg['pose_ranges_deg'])
            result.update(pose_match=match, guidance=hint, relative_yaw=round(yaw, 1), relative_pitch=round(pitch, 1))
            if not match:
                self.rejections['WRONG_POSE'] = self.rejections.get('WRONG_POSE', 0) + 1
                return result
            if self.pending:
                result['guidance'] = 'Đang xử lý mẫu, giữ nguyên tư thế…'
                return result
            if now - self.last_attempt < c['min_sample_interval_s']:
                return result
            self.pending = True
            self.last_attempt = now
            result.update(request_embedding=True, pose=self.pose_key, yaw=yaw, pitch=pitch,
                          quality=quality_score(assessment['metrics'], self.cfg['quality']))
            return result

    def embedding_failed(self, generation, code):
        with self.lock:
            if generation == self.generation:
                self.pending = False
                self.rejections[code] = self.rejections.get(code, 0) + 1

    def add_embedding(self, generation, pose, vector, yaw, pitch, quality, now, embedding_ms):
        """Diversity gate: cosine + pose delta + time, same pose only."""
        c = self.cfg['collection']
        with self.lock:
            if generation != self.generation:
                return 'STALE'
            self.pending = False
            if self.state != 'collecting' or pose != self.pose_key:
                return 'STALE'
            v = l2_normalize(vector)
            self.embedding_ms.append(embedding_ms)
            same = self.samples[pose]
            if same:
                sims = [float(v @ s.vector) for s in same]
                best = int(np.argmax(sims))
                nearest = same[best]
                if sims[best] >= c['duplicate_cosine']:
                    self.rejections['DUPLICATE'] = self.rejections.get('DUPLICATE', 0) + 1
                    return 'DUPLICATE'
                delta = math.hypot(yaw - nearest.yaw, pitch - nearest.pitch)
                if (sims[best] >= c['near_duplicate_cosine'] and delta < c['near_duplicate_min_pose_delta_deg']
                        and now - nearest.captured_at < c['near_duplicate_min_interval_s']):
                    self.rejections['NEAR_DUPLICATE'] = self.rejections.get('NEAR_DUPLICATE', 0) + 1
                    return 'NEAR_DUPLICATE'
            same.append(Sample(v, pose, yaw, pitch, quality, now, embedding_ms))
            if len(same) >= self.required(pose):
                if pose == 'front':
                    lim = self.cfg['pose_model']['personal_baseline_limit_deg']
                    self.baseline = dict(yaw=float(np.clip(np.median([s.yaw for s in same]), -lim, lim)),
                                         pitch=float(np.clip(np.median([s.pitch for s in same]), -lim, lim)))
                self.index += 1
                self._start_pose(now)
                if self.index >= len(POSE_KEYS):
                    self.state = 'complete'
                    self.completed_at = now
            return 'ACCEPTED'

    # -- reporting ----------------------------------------------------------
    def vectors(self):
        with self.lock:
            return [(s.pose, s.vector) for k in POSE_KEYS for s in self.samples[k]]

    def progress(self):
        with self.lock:
            poses = []
            for p in self.cfg['poses']:
                have, need = len(self.samples[p['key']]), p['samples']
                poses.append(dict(key=p['key'], label=p['label'], instruction=p['instruction'],
                                  collected=have, required=need, done=have >= need,
                                  current=p['key'] == self.pose_key and self.state != 'complete'))
            total = sum(p['samples'] for p in self.cfg['poses'])
            got = sum(min(p['collected'], p['required']) for p in poses)
            timeout = self.cfg['collection']['pose_timeout_s']
            return dict(state=self.state, current_pose=self.pose_key, poses=poses, collected=got, required=total,
                        percent=round(100 * got / total), pending=self.pending,
                        pose_time_left_s=None if self.state == 'complete' else max(0, round(timeout - self.pose_active_s, 1)),
                        complete=self.state == 'complete',
                        elapsed_s=round(((self.completed_at or self.last_seen) - self.started_at), 1),
                        mean_embedding_ms=round(float(np.mean(self.embedding_ms)), 1) if self.embedding_ms else None)


# ------------------------------------------------------------------- verification
def compare_with_gallery(vectors, records, own_label=None, cfg=None):
    """Robust per-student similarity of the newly collected vectors to the gallery.

    For each existing student: median over new vectors of (max cosine to that
    student's gallery vectors). The median avoids one odd frame triggering or
    hiding a warning. Returns other-student risks and own-identity consistency.
    """
    cfg = cfg or load_config()
    v = cfg['verification']
    new = np.asarray([l2_normalize(x) for x in vectors])
    by_student = {}
    for r in records:
        by_student.setdefault(r['student_id'], []).append(r['embedding'])
    others, own = [], None
    for student, vecs in by_student.items():
        mat = np.asarray(vecs, dtype=np.float64)
        mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        best = (new @ mat.T).max(axis=1)
        entry = dict(student=student, median_similarity=round(float(np.median(best)), 4),
                     max_similarity=round(float(best.max()), 4))
        if own_label is not None and student == own_label:
            own = entry
        else:
            others.append(entry)
    others.sort(key=lambda e: -e['median_similarity'])
    risks = [e for e in others if e['median_similarity'] >= v['other_student_warning_cosine']]
    warnings = []
    if risks:
        warnings.append(dict(code='SIMILAR_TO_OTHER_STUDENT',
            message='Khuôn mặt vừa thu rất giống sinh viên đã có: ' + ', '.join(r['student'] for r in risks[:3])
            + '. Hãy kiểm tra giấy tờ trước khi lưu.'))
    if own_label is not None and own is not None and own['median_similarity'] < v['same_student_min_cosine']:
        warnings.append(dict(code='DIFFERENT_FROM_EXISTING',
            message='Khuôn mặt vừa thu khác đáng kể so với dữ liệu cũ của chính sinh viên này. Có thể không phải cùng một người.'))
    return dict(top_other=others[:3], risks=risks, own=own, warnings=warnings,
                thresholds=dict(other_student_warning_cosine=v['other_student_warning_cosine'],
                                same_student_min_cosine=v['same_student_min_cosine']),
                note='Ngưỡng cảnh báo là giá trị khởi đầu, chưa hiệu chỉnh bằng dữ liệu độc lập; chỉ dùng để nhắc người vận hành.')
