"""Local-only browser camera API. Run with the project Python: -m backend.app."""
from io import BytesIO
import json
import hashlib
import logging
import secrets
import sqlite3
from time import perf_counter
import threading
import time

from flask import Flask, jsonify, render_template, request
from PIL import Image, ImageOps, UnidentifiedImageError
import numpy as np
from werkzeug.exceptions import HTTPException

from .config import Config, FRONTEND, ROOT
from .database.db import ManagementStore
from .management import management_blueprint
from .enrollment_routes import enrollment_blueprint
from .attendance.attendance_service import AttendanceService
from .attendance.automatic import AutomaticAttendance
from .attendance.continuity import Continuity
from .attendance.stability import observe
from .attendance.classification import classify, public_face, IN_CLASS
from .media import media_blueprint
from .test_labeling import labeling_blueprint
from .attendance.routes import attendance_blueprint
from .database.db import DomainError
from .face_recognition.runtime_service import (
    RecognitionEngine, EngineBusy, EngineUnavailable, SessionStopped,
)
from .face_recognition.camera_pipeline import PROFILES


def source_fingerprint():
    """Detect a checkout/reset while an old Flask process still holds imported code."""
    try:
        files=sorted((ROOT/'backend').rglob('*.py'))
        values=[(p.relative_to(ROOT).as_posix(),hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]
        return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    except OSError:return 'source_unavailable'


def create_app(engine=None, start_models=True, overrides=None):
    app = Flask(__name__, template_folder=str(FRONTEND / 'templates'),
                static_folder=str(FRONTEND / 'static'), static_url_path='/static')
    loaded_source_fingerprint=source_fingerprint()
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    service = engine if engine is not None else RecognitionEngine()
    app.extensions['face_engine'] = service
    if getattr(service,'verification_policy',None) is None:
        # Test doubles / engines without a loaded policy: read the same config file
        # (or an override). RecognitionEngine loads and model-checks it itself.
        from .face_recognition.verification_policy import VerificationPolicy, POLICY_PATH
        service.verification_policy=VerificationPolicy.load(app.config.get('ATTENDANCE_POLICY_PATH',POLICY_PATH))
    sessions = {}
    sessions_guard = threading.Lock()
    token = secrets.token_urlsafe(32)
    app.extensions['camera_sessions'] = sessions
    store=ManagementStore(app.config.get('DATABASE_PATH',ROOT/'outputs'/'management.sqlite3'),ROOT/'outputs'/'face_embeddings.json')
    app.extensions['management_store']=store
    attendance=AttendanceService(store)
    app.extensions['attendance_service']=attendance
    automatic=AutomaticAttendance(attendance,app.config['AUTO_ATTENDANCE_ENABLED'],app.config['AUTO_ATTENDANCE_EVIDENCE'],app.config['AUTO_ATTENDANCE_ACTIVATION'])
    if hasattr(service,'configure_store'):
        service.configure_store(store)
    app.register_blueprint(management_blueprint(store,service,token))
    enrollment_bp=enrollment_blueprint(store,service,app.config.get('ENROLLMENT_CONFIG_PATH'),app.config.get('ENROLLMENT_BACKUP_DIR'))
    app.register_blueprint(enrollment_bp);app.extensions['enrollment_sessions']=enrollment_bp.enrollment_sessions
    app.extensions['enrollment_worker']=enrollment_bp.enrollment_worker
    def camera_config():
        return dict(csrfToken=token,maxFrameDimension=app.config['CLIENT_FRAME_DIMENSION'],frameIntervalMs=app.config['FRAME_INTERVAL_MS'],requestTimeoutMs=app.config['REQUEST_TIMEOUT_MS'],profiles=PROFILES)
    app.register_blueprint(attendance_blueprint(attendance,token,camera_config))
    media_bp,media_jobs=media_blueprint(service,attendance,ROOT/'.venv/media_uploads')
    app.register_blueprint(media_bp);app.extensions['media_jobs']=media_jobs
    labeling_bp,labeling=labeling_blueprint(service,store,token)
    app.register_blueprint(labeling_bp);app.extensions['group_labeling']=labeling
    @app.errorhandler(DomainError)
    def domain_error(exc):return jsonify(error=dict(code='DOMAIN_ERROR',message=str(exc))),exc.status

    def error(code, message, status):
        return jsonify(error=dict(code=code, message=message)), status

    @app.before_request
    def local_write_guard():
        if request.path.startswith('/api/') and not (request.path=='/api/media/jobs' and request.method=='POST') and request.content_length and request.content_length>1024*1024:
            return error('FRAME_TOO_LARGE','Yêu cầu vượt giới hạn 1 MB.',413)
        if request.path.startswith('/api/') and request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            origin = request.headers.get('Origin')
            if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
                return error('ORIGIN_REJECTED', 'Yêu cầu phải đến từ website localhost này.', 403)
            if not secrets.compare_digest(request.headers.get('X-Camera-Token', ''), token):
                return error('TOKEN_REQUIRED', 'Phiên trang đã hết hạn. Hãy tải lại trang.', 403)

    @app.url_defaults
    def asset_version(endpoint,values):
        if endpoint=='static' and values.get('filename','').endswith(('.js','.css')):
            try:values.setdefault('v',str((FRONTEND/'static'/values['filename']).stat().st_mtime_ns))
            except OSError:pass

    @app.after_request
    def response_headers(response):
        if request.path.startswith('/api/') or request.path == '/' or request.endpoint in ('management.page','attendance.page','attendance.camera'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Permissions-Policy'] = 'camera=(self), microphone=()'
        return response

    @app.get('/')
    def index():
        return render_template('index.html', client_config=dict(
            csrfToken=token, maxFrameDimension=app.config['CLIENT_FRAME_DIMENSION'],
            frameIntervalMs=app.config['FRAME_INTERVAL_MS'],
            requestTimeoutMs=app.config['REQUEST_TIMEOUT_MS'],profiles=PROFILES))

    @app.get('/api/health')
    def health():
        status=automation_status()
        model=service.health()
        reload_required=source_fingerprint()!=loaded_source_fingerprint
        if reload_required:model.update(ready=False,state='restart_required')
        return jsonify(application='face_recognition_camera_v1',automatic_attendance_requested=automatic.requested,
                       automatic_attendance_reason=automatic.reason,automatic_attendance=status,
                       manual_attendance_enabled=True,code_reload_required=reload_required,
                       loaded_source_fingerprint=loaded_source_fingerprint,**model)

    DEFAULT_PROFILE='retinaface_scaled_experimental'
    def legacy_policy():
        matcher=getattr(service,'matcher',None)
        if getattr(matcher,'policy',None) is not None:return matcher.policy
        try:return json.loads((ROOT/'outputs/recognition_threshold.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return None

    def automation_status(profile=None):
        if profile is None:
            try:profile=request.args.get('profile',DEFAULT_PROFILE)
            except RuntimeError:profile=DEFAULT_PROFILE
        policy=getattr(service,'verification_policy',None)
        matcher=getattr(service,'matcher',None)
        status=automatic.readiness(policy,getattr(matcher,'signature',None),profile,
                                   bool(service.health().get('ready')),legacy_policy())
        if source_fingerprint()!=loaded_source_fingerprint:
            status.update(state='locked',ready=False,can_enable=False)
            status['blocking_reasons'].append('source_reload_required')
            status['message']='Tự động điểm danh: KHÓA – mã nguồn đã đổi, cần khởi động lại Flask.'
        status['policy']=policy.public() if policy is not None else None
        return status

    @app.get('/api/attendance/automation-status')
    def automatic_status():return jsonify(automation_status())

    @app.post('/api/attendance/automation-mode')
    def automatic_mode():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or type(data.get('enabled')) is not bool:
            return error('INVALID_MODE','enabled phải là true hoặc false.',400)
        profile=data.get('profile',DEFAULT_PROFILE)
        if not isinstance(profile,str) or profile not in PROFILES:return error('UNKNOWN_PROFILE','Profile không hợp lệ.',400)
        with sessions_guard:
            status=automation_status(profile)
            if data['enabled'] and not status['can_enable']:
                return jsonify(error=dict(code='AUTOMATION_LOCKED',message=status['message']),status=status),409
            activation=dict(enabled=data['enabled'],profile=profile,changed_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
            if data['enabled']:activation['policy_sha256']=service.verification_policy.sha256
            target=app.config['AUTO_ATTENDANCE_ACTIVATION']
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(activation,indent=2),encoding='utf-8');temporary.replace(target)
            for s in sessions.values():s['verified_history'].clear()
        return jsonify(automation_status(profile))

    @app.post('/api/camera/sessions')
    def start_session():
        if not service.health()['ready']:
            return error('MODEL_NOT_READY', 'Mô hình đang tải hoặc chưa sẵn sàng. Kiểm tra trạng thái backend.', 503)
        options=request.get_json(silent=True) or {}
        if not isinstance(options,dict):
            return error('INVALID_OPTIONS','Cấu hình phiên phải là đối tượng JSON.',400)
        profile=options.get('profile','retinaface_reuse')
        attendance_id=options.get('attendance_session_id')
        if attendance_id is not None:attendance.context(attendance_id)
        if not isinstance(profile,str) or (profile not in PROFILES and profile!='retinaface_original'):
            return error('UNKNOWN_PROFILE','Cấu hình camera không được hỗ trợ.',400)
        if profile in PROFILES and PROFILES[profile].get('available') is False:
            return error('PROFILE_DISABLED','Cấu hình thử nghiệm này bị vô hiệu do không đạt đánh giá validation.',400)
        with sessions_guard:
            now = time.monotonic()
            for key in list(sessions):
                if now - sessions[key]['last_seen'] > 300:
                    sessions[key]['cancelled'].set()
                    del sessions[key]
            if sum(not s['cancelled'].is_set() for s in sessions.values()) >= 4:
                return error('SESSION_LIMIT', 'Đang có quá nhiều phiên camera. Hãy tắt phiên cũ.', 429)
            session_id = secrets.token_urlsafe(24)
            sessions[session_id] = dict(cancelled=threading.Event(), last_seen=now,profile=profile,attendance_id=attendance_id,
                continuity=Continuity(),sequence=0,verified_history={})
        return jsonify(session_id=session_id,profile=profile)

    @app.delete('/api/camera/sessions/<session_id>')
    def stop_session(session_id):
        with sessions_guard:
            session = sessions.pop(session_id, None)
        if session:
            session['cancelled'].set()
        return jsonify(stopped=True, note='Không nhận khung mới; tác vụ native đang chạy sẽ kết thúc tại điểm hủy an toàn.')

    def recognition_response(session,result,sequence,snapshot,received_at,inference_started,result_at):
        """Classify every face on the BACKEND and auto-write PRESENT when allowed.

        Pipeline: ArcFace global top-1/top-2 -> similarity+margin policy -> numeric
        student_id -> enrollment of the open session's class (fresh SQLite read
        AFTER inference) -> current-track binding -> 5-of-7 stability -> write.
        The raw internal face records never leave this function.
        """
        policy=service.verification_policy
        max_age=policy.result_max_age_s if policy.valid else 0
        max_gap=policy.track_max_gap_s if policy.valid else 0
        attendance_id=session.get('attendance_id')
        context=attendance.context(attendance_id) if attendance_id is not None else None
        roster=set(context['student_ids']) if context else None
        auto=automation_status(session['profile']) if context else None
        raw=result['faces']
        with sessions_guard:
            linked=session['continuity'].bind(raw,result['frame'],snapshot,time.monotonic())
            for face,saved in zip(raw,linked):
                if saved and not session['continuity'].valid(saved,time.monotonic(),max_gap):
                    face['tracking']=dict(valid=False,server_track_id=None)
            current=sequence==session['sequence']
            history={};plans=[]
            for face,saved in zip(raw,linked):
                track_valid=bool(saved and current and face['tracking'].get('valid'))
                status=classify(face,roster,context is not None and context['status']=='IN_PROGRESS',track_valid,context is not None)
                consistency=None
                if context is not None and track_valid:
                    student_id=face['candidate_student_id'] if status==IN_CLASS else None
                    state,consistency=observe(session['verified_history'].get(saved['id'],{}),student_id,sequence,policy.required or 5,policy.window or 7)
                    history[saved['id']]=state
                plans.append((face,saved,status,consistency))
            if current and context is not None:session['verified_history']=history
        public=[];changed=False;errors=[]
        for face,saved,status,consistency in plans:
            entry=dict(verification_consistency=consistency) if context is not None else {}
            if status==IN_CLASS and auto and auto['ready'] and consistency and consistency['ready']:
                def still_valid(saved=saved):
                    with sessions_guard:
                        return bool(not session['cancelled'].is_set() and sequence==session['sequence']
                            and time.monotonic()-result_at<=max_age
                            and session['continuity'].valid(saved,time.monotonic(),max_gap))
                try:
                    outcome=attendance.confirm_auto(attendance_id,face['candidate_student_id'],
                        float(face['top1']['similarity']),float(face['margin']),still_valid)
                except sqlite3.Error:
                    app.logger.exception('Automatic attendance write failed')
                    outcome=dict(changed=False,saved=False,reason='database_write_failed')
                    errors.append('database_write_failed')
                changed=changed or outcome['changed']
                entry['auto_write']=outcome
            public.append((face,status,entry))
        detail=None
        if context is not None:
            try:detail=attendance.detail(attendance_id)
            except sqlite3.Error:errors.append('database_read_failed')
        records={r['student_id']:r['attendance_status'] for r in detail['records']} if detail else {}
        faces=[]
        for face,status,entry in public:
            if context is not None and status==IN_CLASS:
                saved_status=records.get(face['candidate_student_id']) if detail else None
                entry['attendance']=dict(status=saved_status or 'PENDING',saved=saved_status=='PRESENT',
                    label='Đã điểm danh' if saved_status=='PRESENT' else 'Chờ xác nhận')
            faces.append(public_face(face,status,**entry))
        response=dict(faces=faces,detected_faces=result.get('detected_faces',len(faces)),face_errors=result.get('face_errors',0),
            frame=result['frame'],processing_ms=result.get('processing_ms'),profile=result.get('profile',session['profile']),
            experimental=result.get('experimental'),timings_ms=result.get('timings_ms',{}),request_id=sequence,
            attendance_session_id=attendance_id,class_id=context['class_id'] if context else None,
            result_meta=dict(request_id=sequence,attendance_session_id=attendance_id,class_id=context['class_id'] if context else None,
                server_result_epoch_ms=time.time()*1000,max_result_age_s=max_age),
            frame_timing=dict(server_received_at=received_at,inference_started_at=inference_started,result_at=result_at,
                client_captured_ms=request.headers.get('X-Client-Captured-Ms')))
        if context is not None:
            response['automatic_attendance']=dict(status=auto,enabled=bool(auto and auto['ready']),changed=changed,
                errors=errors,pending_consistency=any(f['status']==IN_CLASS and not (f.get('verification_consistency') or {}).get('ready') for f in faces))
            if detail is not None:response['attendance_session']=dict(id=detail['id'],status=detail['status'],stats=detail['stats'])
            if changed and detail is not None:response['automatic_attendance']['session']=detail
        return response

    @app.post('/api/recognize')
    @app.post('/api/detect')
    def recognize():
        preview=request.path=='/api/detect'
        request_start=perf_counter()
        received_at=time.monotonic()
        session_id = request.headers.get('X-Camera-Session', '')
        with sessions_guard:
            session = sessions.get(session_id)
            if session:
                session['last_seen'] = time.monotonic()
        if not session or session['cancelled'].is_set():
            return error('SESSION_STOPPED', 'Camera đã tắt hoặc phiên đã hết hạn.', 409)
        try:attendance_context=attendance.context(session['attendance_id']) if session.get('attendance_id') is not None else None
        except sqlite3.Error:
            app.logger.exception('SQLite unavailable before recognition')
            return error('DATABASE_UNAVAILABLE','Không đọc được SQLite. Không ghi điểm danh; kiểm tra file dữ liệu rồi thử lại.',503)
        if request.mimetype != 'image/jpeg':
            return error('JPEG_REQUIRED', 'Khung hình phải có định dạng JPEG.', 415)
        raw = request.stream.read(1024*1024+1)
        if len(raw)>1024*1024:return error('FRAME_TOO_LARGE','Khung hình vượt giới hạn 1 MB.',413)
        if not raw:
            return error('EMPTY_FRAME', 'Khung hình trống.', 400)
        try:
            decode_start=perf_counter()
            with Image.open(BytesIO(raw)) as image:
                width, height = image.size
                if image.format != 'JPEG':
                    return error('JPEG_REQUIRED', 'Nội dung khung hình không phải JPEG.', 415)
                if max(width, height) > (640 if preview else app.config['MAX_FRAME_DIMENSION']):
                    return error('FRAME_TOO_LARGE', 'Kích thước khung hình vượt giới hạn.', 422)
                rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
            bgr = np.ascontiguousarray(rgb[:, :, ::-1])
            decode_ms=(perf_counter()-decode_start)*1000
        except (UnidentifiedImageError, OSError, ValueError):
            return error('INVALID_IMAGE', 'Không thể đọc khung hình.', 400)
        try:
            with sessions_guard:
                if not preview:
                    session['sequence']+=1;sequence=session['sequence']
                    snapshot=session['continuity'].snapshot(bgr,received_at)
            kwargs=dict(profile=session['profile'])
            inference_started=time.monotonic()
            if attendance_context is not None:kwargs['allowed_student_ids']=attendance_context['student_ids']
            result = (service.detect_preview(bgr,session['cancelled'].is_set) if preview else
                      service.analyze(bgr, session['cancelled'].is_set,**kwargs))
            result_at=time.monotonic()
            if session['cancelled'].is_set():
                raise SessionStopped()
            if preview:
                with sessions_guard:session['continuity'].update(result['faces'],result['frame'],bgr,received_at)
            else:
                result=recognition_response(session,result,sequence,snapshot,received_at,inference_started,result_at)
            result.setdefault('timings_ms',{})['decode_ms']=decode_ms
            serialize_start=perf_counter()
            response=jsonify(result)
            serialization_ms=(perf_counter()-serialize_start)*1000
            timings=dict(result['timings_ms'],serialization_ms=serialization_ms,
                         server_total_ms=(perf_counter()-request_start)*1000)
            response.headers['Server-Timing']=', '.join(f'{name};dur={value:.3f}' for name,value in timings.items())
            return response
        except EngineBusy:
            response, status = error('ENGINE_BUSY', 'Backend đang xử lý khung trước. Không xếp hàng khung mới.', 429)
            response.headers['Retry-After'] = '2'
            return response, status
        except DomainError:
            raise
        except sqlite3.Error:
            app.logger.exception('SQLite unavailable during recognition')
            return error('DATABASE_UNAVAILABLE','Không đọc/ghi được SQLite. Chưa ghi điểm danh; kết quả này bị bỏ.',503)
        except EngineUnavailable:
            return error('MODEL_NOT_READY', 'Mô hình chưa sẵn sàng.', 503)
        except SessionStopped:
            return error('SESSION_STOPPED', 'Đã hủy kết quả vì camera đã tắt.', 409)
        except Exception as exc:
            app.logger.exception('Frame inference failed; frame is not persisted')
            if isinstance(exc,MemoryError) or any(word in str(exc) for word in ('Insufficient memory','OutOfMemoryError','Failed to allocate')):
                return error('RESOURCE_LIMIT','Máy đang thiếu RAM để xử lý AI. Khung này không tạo dữ liệu điểm danh. Có thể tiếp tục xác nhận thủ công; kiểm tra ứng dụng đang dùng bộ nhớ.',503)
            return error('INFERENCE_FAILED', 'Backend không xử lý được khung hình. Xem Terminal để biết lỗi.', 500)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if request.path.startswith('/api/'):
            message = 'Khung hình vượt giới hạn dung lượng.' if exc.code == 413 else 'Yêu cầu không hợp lệ.'
            return error('HTTP_' + str(exc.code), message, exc.code)
        return exc

    if start_models:
        service.start_loading()
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    create_app().run(host=Config.HOST, port=Config.PORT, debug=False,
                     use_reloader=False, threaded=True)
