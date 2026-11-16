"""Camera-only face enrollment API ("Thêm sinh viên" and "Đăng ký lại khuôn mặt").

Frames arrive as JPEG, are decoded in RAM and dropped after the response. Only
L2-normalised ArcFace vectors (+ pose label) are kept, in the session's RAM,
until the operator confirms; then student + vectors are written in one SQLite
transaction and the runtime gallery is reloaded. Nothing is written before.
"""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import logging
import threading
import time
import uuid

from flask import Blueprint, current_app, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError
import numpy as np

from .config import ROOT
from .database.db import DomainError
from .face_recognition.enrollment import EnrollmentSession, assess_frame, compare_with_gallery, load_config
from .face_recognition.runtime_service import EngineBusy, EngineUnavailable, SessionStopped

logger = logging.getLogger(__name__)
PIPELINE = dict(model_name='ArcFace', detector_backend='retinaface', align=True, normalization='base',
                embedding_dimension=512, enforce_detection=True, expand_percentage=0, anti_spoofing=False,
                source='camera_enrollment', camera_path='retinaface_reuse_on_face_crop', l2_normalized=True,
                attribution='operator_confirmed_identity_not_biometrically_verified')


def enrollment_blueprint(store, engine, config_path=None, backup_dir=None):
    bp = Blueprint('enrollment', __name__)
    cfg = load_config(config_path)
    sessions = {}
    guard = threading.Lock()
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='enrollment-embed')
    backup_dir = backup_dir or ROOT / 'outputs' / 'backups' / 'face_enrollment'

    def fail(message, status=400, code='ENROLLMENT_ERROR', **extra):
        return jsonify(error=dict(code=code, message=message, **extra)), status

    @bp.errorhandler(DomainError)
    def domain_error(exc):
        return fail(str(exc), exc.status, 'ENROLLMENT_ERROR')

    def expire(now):
        for key in list(sessions):
            s = sessions[key]
            if now - s.last_seen > cfg['session']['ttl_s'] or s.cancelled.is_set():
                s.cancelled.set()
                del sessions[key]

    def get(session_id):
        with guard:
            expire(time.monotonic())
            s = sessions.get(session_id)
        if s is None:
            raise DomainError('Phiên đăng ký đã kết thúc hoặc hết hạn. Hãy bắt đầu lại.', 404)
        return s

    def ready():
        try:
            return bool(engine.health().get('ready'))
        except Exception:
            return False

    def public(s, extra=None):
        data = dict(session_id=s.session_id, mode=s.mode, progress=s.progress(),
                    last_sample=getattr(s, 'last_outcome', None), verification=s.verification,
                    message='Đã thu thập đủ dữ liệu khuôn mặt' if s.state == 'complete' else None)
        if extra:
            data.update(extra)
        return data

    @bp.get('/api/enrollment/config')
    def public_config():
        return jsonify(poses=cfg['poses'], pose_timeout_s=cfg['collection']['pose_timeout_s'],
                       frame_interval_ms=150, max_frame_dimension=cfg['collection']['max_frame_dimension'],
                       total_required=sum(p['samples'] for p in cfg['poses']))

    @bp.get('/api/enrollment/code-status')
    def code_status():
        return jsonify(store.code_status(request.args.get('code', '')))

    @bp.post('/api/enrollment/sessions')
    def start():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise DomainError('Nội dung phải là JSON object.')
        mode = data.get('mode', 'new')
        if mode == 'new':
            class_id = data.get('class_id')
            fields = store.validate_new_student(data, class_id)
            subject = dict(fields, class_id=class_id)
        elif mode == 'reenroll':
            sid = data.get('student_id')
            if isinstance(sid, bool) or not isinstance(sid, int) or sid <= 0:
                raise DomainError('Mã hồ sơ sinh viên không hợp lệ.')
            student = store.detail('students', sid)
            subject = dict(student_id=sid, student_code=student['student_code'], full_name=student['full_name'],
                           existing_faces=student['face_count'])
        else:
            raise DomainError('Chế độ đăng ký không hợp lệ.')
        if not ready():
            raise DomainError('Mô hình AI đang tải hoặc chưa sẵn sàng. Vui lòng đợi rồi thử lại.', 503)
        with guard:
            expire(time.monotonic())
            if sum(not s.cancelled.is_set() for s in sessions.values()) >= cfg['session']['max_active_sessions']:
                raise DomainError('Đang có phiên đăng ký khác. Hãy hoàn tất hoặc hủy phiên đó trước.', 429)
            s = EnrollmentSession(cfg, mode, subject)
            sessions[s.session_id] = s
        return jsonify(public(s, dict(subject={k: v for k, v in subject.items() if k != 'class_id'}))), 201

    def decode(raw):
        if request.mimetype != 'image/jpeg':
            raise DomainError('Khung hình phải là JPEG.', 415)
        if not raw:
            raise DomainError('Khung hình trống.')
        try:
            with Image.open(BytesIO(raw)) as image:
                if image.format != 'JPEG':
                    raise DomainError('Khung hình phải là JPEG.', 415)
                if max(image.size) > cfg['collection']['max_frame_dimension'] or min(image.size) < 64:
                    raise DomainError('Kích thước khung hình không hợp lệ.', 422)
                rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise DomainError('Không đọc được khung hình.') from exc
        return np.ascontiguousarray(rgb[:, :, ::-1])

    def embed_job(s, bgr, box, plan):
        """Runs on the single enrollment worker; bounded to one job per session."""
        try:
            vector, timings = engine.enrollment_embedding(bgr, box, cfg['collection'], s.cancelled.is_set)
            outcome = s.add_embedding(plan['generation'], plan['pose'], vector, plan['yaw'], plan['pitch'],
                                      plan['quality'], time.monotonic(), timings.get('total_ms', 0.0))
            s.last_outcome = dict(result=outcome, pose=plan['pose'], embedding_ms=round(timings.get('total_ms', 0.0), 1))
            if s.state == 'complete' and s.verification is None:
                verify(s)
        except EngineBusy:
            s.embedding_failed(plan['generation'], 'AI_BUSY')
            s.last_outcome = dict(result='AI_BUSY', pose=plan['pose'])
        except (SessionStopped, EngineUnavailable):
            s.embedding_failed(plan['generation'], 'STOPPED')
        except ValueError as exc:
            code = str(exc) if str(exc) in ('NO_FACE', 'MULTIPLE_FACES', 'INVALID_EMBEDDING') else 'EMBEDDING_FAILED'
            s.embedding_failed(plan['generation'], code)
            s.last_outcome = dict(result=code, pose=plan['pose'])
        except Exception:
            logger.exception('Enrollment embedding failed; frame not persisted')
            s.embedding_failed(plan['generation'], 'EMBEDDING_FAILED')
            s.last_outcome = dict(result='EMBEDDING_FAILED', pose=plan['pose'])

    def verify(s):
        """Compare collected vectors with everyone currently in the gallery."""
        if hasattr(engine, 'refresh_gallery'):
            engine.refresh_gallery(wait=5)
        records = engine.gallery_records() if hasattr(engine, 'gallery_records') else []
        own = store.gallery_label(s.subject['student_id']) if s.mode == 'reenroll' else None
        vectors = [v for _, v in s.vectors()]
        if records and vectors:
            s.verification = compare_with_gallery(vectors, records, own, cfg)
        else:
            s.verification = dict(top_other=[], risks=[], own=None, warnings=[], note='Gallery trống; không có gì để so sánh.')

    @bp.post('/api/enrollment/sessions/<session_id>/frames')
    def frame(session_id):
        s = get(session_id)
        raw = request.stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise DomainError('Khung hình vượt 1 MB.', 413)
        bgr = decode(raw)
        now = time.monotonic()
        if s.state == 'complete':
            s.touch(now)
            return jsonify(public(s))
        start = time.perf_counter()
        try:
            faces = engine.enrollment_detect(bgr, cfg['quality']['detector_score_threshold'])
        except EngineUnavailable:
            raise DomainError('Mô hình AI chưa sẵn sàng.', 503)
        detect_ms = (time.perf_counter() - start) * 1000
        assessment = assess_frame(bgr, faces, cfg)
        plan = s.observe(assessment, now)
        if plan['request_embedding']:
            box = assessment['face']['box']
            worker.submit(embed_job, s, bgr, box, plan)
        face = assessment.get('face')
        return jsonify(public(s, dict(
            frame=dict(width=bgr.shape[1], height=bgr.shape[0]),
            face=dict(box={k: round(v, 1) for k, v in face['box'].items()}) if face else None,
            face_count=assessment['face_count'], quality=dict(ok=assessment['ok'], code=assessment['code'],
            message=assessment['message'], metrics=assessment['metrics']),
            guidance=plan['guidance'], pose_match=plan['pose_match'], sampling=plan['request_embedding'],
            relative_pose=dict(yaw=plan.get('relative_yaw'), pitch=plan.get('relative_pitch')),
            timings_ms=dict(detection_quality=round(detect_ms + 0.0, 1),
                            server_total=round((time.perf_counter() - start) * 1000, 1)))))

    @bp.get('/api/enrollment/sessions/<session_id>')
    def status(session_id):
        s = get(session_id)
        s.touch(time.monotonic())
        return jsonify(public(s))

    @bp.post('/api/enrollment/sessions/<session_id>/retry-pose')
    def retry(session_id):
        s = get(session_id)
        s.retry_pose()
        return jsonify(public(s))

    @bp.post('/api/enrollment/sessions/<session_id>/reset')
    def reset(session_id):
        s = get(session_id)
        s.reset()
        s.last_outcome = None
        return jsonify(public(s))

    @bp.delete('/api/enrollment/sessions/<session_id>')
    def cancel(session_id):
        with guard:
            s = sessions.pop(session_id, None)
        if s:
            s.cancelled.set()
            s.reset()  # drop collected vectors from RAM
        return jsonify(cancelled=True, saved=False)

    @bp.post('/api/enrollment/sessions/<session_id>/complete')
    def complete(session_id):
        s = get(session_id)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise DomainError('Nội dung phải là JSON object.')
        if s.state != 'complete':
            raise DomainError('Chưa thu thập đủ dữ liệu khuôn mặt.', 409)
        if data.get('identity_confirmed') is not True:
            raise DomainError('Người vận hành cần xác nhận đã đối chiếu danh tính sinh viên trước khi lưu.')
        if s.verification is None:
            verify(s)
        if s.verification['warnings'] and data.get('acknowledge_warnings') is not True:
            raise DomainError('Có cảnh báo nhận dạng. Hãy kiểm tra và xác nhận cảnh báo trước khi lưu.', 409)
        if s.mode == 'reenroll' and data.get('confirm_replace') is not True:
            raise DomainError('Cần xác nhận thay thế dữ liệu khuôn mặt cũ.', 409)
        samples = [dict(pose=s_.pose, embedding=s_.vector.tolist(), quality=s_.quality)
                   for key in s.samples for s_ in s.samples[key]]
        enrollment_id = 'enr-' + uuid.uuid4().hex
        backup = None
        try:
            backup = store.backup(backup_dir, cfg['backups']['keep_last'])
        except Exception:
            logger.exception('Pre-enrollment backup failed; nothing written')
            raise DomainError('Không tạo được bản sao lưu CSDL; chưa lưu gì.', 500)
        validate = getattr(engine, 'validate_gallery', None)
        try:
            if s.mode == 'new':
                receipt = store.enroll_new_student(dict(student_code=s.subject['student_code'], full_name=s.subject['full_name']),
                                                   s.subject.get('class_id'), samples, PIPELINE, enrollment_id, validate)
            else:
                receipt = store.replace_camera_embeddings(s.subject['student_id'], samples, PIPELINE, enrollment_id, validate)
        except DomainError:
            raise
        except Exception:
            logger.exception('Enrollment transaction failed and was rolled back')
            raise DomainError('Lưu thất bại và đã hoàn tác; CSDL và gallery giữ nguyên.', 500)
        # Runtime gallery reload; on error undo the commit so both stay consistent.
        try:
            loaded = engine.refresh_gallery(wait=30) if hasattr(engine, 'refresh_gallery') else False
        except Exception:
            logger.exception('Runtime gallery reload failed; reverting enrollment')
            store.revert_enrollment(receipt)
            try:
                engine.refresh_gallery(wait=30)
            except Exception:
                logger.exception('Gallery reload after revert failed')
            raise DomainError('AI không nạp được gallery mới nên đã hoàn tác; CSDL và gallery giữ nguyên.', 500)
        engine_revision = engine.loaded_gallery_revision() if hasattr(engine, 'loaded_gallery_revision') else None
        matcher = getattr(engine, 'matcher', None)
        with guard:
            sessions.pop(session_id, None)
        s.cancelled.set()
        counts = {}
        for item in samples:
            counts[item['pose']] = counts.get(item['pose'], 0) + 1
        snapshot = s.progress()
        timing = dict(collection_s=snapshot['elapsed_s'], mean_embedding_ms=snapshot['mean_embedding_ms'],
                      frames_processed=s.frames, rejected=dict(s.rejections))
        logger.info('Enrollment saved: %s vectors, timing %s', len(samples), timing)
        s.reset()
        loaded = bool(loaded and engine_revision is not None and engine_revision >= receipt['revision'])
        return jsonify(saved=True, student_id=receipt['student_id'], mode=receipt['mode'],
                       embeddings_saved=len(receipt['embedding_ids']), per_pose=counts,
                       replaced_embeddings=len(receipt['replaced']), gallery_revision=receipt['revision'],
                       engine_gallery_revision=engine_revision, gallery_loaded=loaded,
                       backup_file=backup.name if backup else None, timing=timing,
                       automatic_attendance_policy_applied=bool(matcher is not None and getattr(matcher, 'policy', None)),
                       message=('Đã lưu sinh viên và dữ liệu khuôn mặt. Gallery nhận diện đã cập nhật.' if loaded else
                                'Đã lưu vào CSDL. AI đang bận nên gallery sẽ được nạp trước khung nhận diện kế tiếp.')
                               + ' Điểm danh tự động không tự bật; ngưỡng cần được đánh giá lại với gallery mới.'), 201

    bp.enrollment_sessions = sessions
    bp.enrollment_worker = worker
    return bp
