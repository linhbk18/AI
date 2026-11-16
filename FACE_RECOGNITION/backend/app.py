"""Local-only browser camera API. Run with the project Python: -m backend.app."""
from io import BytesIO
import json
import hashlib
import logging
import secrets
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
from .attendance.attendance_service import AttendanceService
from .attendance.automatic import AutomaticAttendance
from .attendance.continuity import Continuity
from .attendance.stability import observe, REQUIRED, WINDOW
from .media import media_blueprint
from .test_labeling import labeling_blueprint
from .face_recognition.results import normalize_faces
from .attendance.routes import attendance_blueprint
from .database.db import DomainError
from .face_recognition.runtime_service import (
    RecognitionEngine, EngineBusy, EngineUnavailable, SessionStopped,
)
from .face_recognition.camera_pipeline import PROFILES


def create_app(engine=None, start_models=True, overrides=None):
    app = Flask(__name__, template_folder=str(FRONTEND / 'templates'),
                static_folder=str(FRONTEND / 'static'), static_url_path='/static')
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    service = engine if engine is not None else RecognitionEngine()
    app.extensions['face_engine'] = service
    sessions = {}
    sessions_guard = threading.Lock()
    token = secrets.token_urlsafe(32)
    app.extensions['camera_sessions'] = sessions
    store=ManagementStore(app.config.get('DATABASE_PATH',ROOT/'outputs'/'management.sqlite3'),ROOT/'outputs'/'face_embeddings.json')
    app.extensions['management_store']=store
    attendance=AttendanceService(store)
    app.extensions['attendance_service']=attendance
    automatic=AutomaticAttendance(attendance,app.config['AUTO_ATTENDANCE_ENABLED'],app.config['AUTO_ATTENDANCE_EVIDENCE'],app.config['AUTO_ATTENDANCE_ACTIVATION'])
    service.automatic_verifier=automatic
    if hasattr(service,'configure_store'):
        service.configure_store(store)
    app.register_blueprint(management_blueprint(store,service,token))
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
        return jsonify(application='face_recognition_camera_v1',automatic_attendance_requested=automatic.requested,
                       automatic_attendance_reason=automatic.reason,automatic_attendance=status, **service.health())

    def automation_status(profile=None):
        matcher=getattr(service,'matcher',None)
        policy=getattr(matcher,'policy',None)
        if policy is None:
            try:policy=json.loads((ROOT/'outputs/recognition_threshold.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):policy=None
        status=automatic.status(policy,getattr(matcher,'signature',None),profile or request.args.get('profile','retinaface_reuse'))
        candidate=AutomaticAttendance(attendance,True,app.config['AUTO_ATTENDANCE_EVIDENCE'])
        available=candidate.status(policy,getattr(matcher,'signature',None),status['profile'])
        status['can_enable']=available['ready'] and matcher is not None and matcher.policy is not None
        status['enable_blocking_reasons']=available['blocking_reasons']
        status['enable_message']=available['message']
        status['required_consistent_results']=REQUIRED
        status['consistency_window']=WINDOW
        if matcher is not None and getattr(matcher,'policy',None) is None:
            status['ready']=False;status['blocking_reasons'].append('policy_not_loaded_for_runtime_gallery')
            status['message']+=' Policy chưa nạp cho gallery runtime; đánh giá đúng signature và khởi động lại Flask sau publish.'
        return status

    @app.get('/api/attendance/automation-status')
    def automatic_status():return jsonify(automation_status())

    @app.post('/api/attendance/automation-mode')
    def automatic_mode():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or type(data.get('enabled')) is not bool:
            return error('INVALID_MODE','enabled phải là true hoặc false.',400)
        profile=data.get('profile','retinaface_scaled_experimental')
        if not isinstance(profile,str) or profile not in PROFILES:return error('UNKNOWN_PROFILE','Profile không hợp lệ.',400)
        with sessions_guard:
            status=automation_status(profile)
            if data['enabled'] and not status['can_enable']:
                return jsonify(error=dict(code='AUTOMATION_LOCKED',message=status['enable_message']),status=status),409
            matcher=getattr(service,'matcher',None)
            activation=dict(enabled=data['enabled'],profile=profile)
            if data['enabled']:
                activation['policy_sha256']=hashlib.sha256(json.dumps(matcher.policy,sort_keys=True).encode()).hexdigest()
            from .face_recognition.verification_workflow import write
            write(app.config['AUTO_ATTENDANCE_ACTIVATION'],activation)
            for s in sessions.values():s['pending_auto'].clear();s['verified_history'].clear()
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
        try:attendance_context=attendance.context(attendance_id) if attendance_id is not None else None
        except DomainError as exc:return error('ATTENDANCE_SESSION_INVALID',str(exc),exc.status)
        requested_class=options.get('class_id')
        if attendance_context is not None:
            if requested_class is not None and str(requested_class)!=str(attendance_context['class_id']):
                return error('CLASS_SCOPE_MISMATCH','Lớp gửi lên không khớp với buổi điểm danh.',409)
            requested_class=attendance_context['class_id']
        elif requested_class is not None:
            return error('ATTENDANCE_SESSION_REQUIRED','Cần buổi điểm danh hợp lệ để giới hạn lớp.',400)
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
            sessions[session_id] = dict(cancelled=threading.Event(), last_seen=now,profile=profile,attendance_id=attendance_id,class_id=requested_class,
                continuity=Continuity(),sequence=0,pending_auto={},verified_history={})
        return jsonify(session_id=session_id,profile=profile)

    @app.delete('/api/camera/sessions/<session_id>')
    def stop_session(session_id):
        with sessions_guard:
            session = sessions.pop(session_id, None)
        if session:
            session['cancelled'].set()
        return jsonify(stopped=True, note='Không nhận khung mới; tác vụ native đang chạy sẽ kết thúc tại điểm hủy an toàn.')

    @app.post('/api/camera/sessions/<session_id>/attendance/commit')
    def commit_automatic(session_id):
        data=request.get_json(silent=True) or {};ids=data.get('grant_ids') if isinstance(data,dict) else None
        if not isinstance(ids,list) or not 1<=len(ids)<=50 or not all(isinstance(g,str) for g in ids):return error('INVALID_GRANTS','Danh sách xác minh không hợp lệ.',400)
        with sessions_guard:
            session=sessions.get(session_id)
            if not session or session['cancelled'].is_set():return error('SESSION_STOPPED','Camera đã tắt.',409)
            if not automation_status(session['profile'])['ready']:return error('AUTOMATION_LOCKED','Chưa đủ bằng chứng bật tự động.',409)
            grants=[session['pending_auto'].get(g) for g in set(ids)]
            def continuous():
                now=time.monotonic();matcher=getattr(service,'matcher',None)
                return bool(automatic.requested and store.gallery_revision()==getattr(service,'_gallery_revision',None) and grants and all(g and g['sequence']==session['sequence'] and now-g['created_at']<3
                    and g['signature']==getattr(matcher,'signature',None) and g['policy']==json.dumps(matcher.policy,sort_keys=True)
                    and session['continuity'].valid(g['track'],now) for g in grants))
            if not continuous():return error('STALE_TRACK','Khuôn mặt đã đổi hoặc kết quả cũ; không ghi điểm danh.',409)
            result=automatic.apply(session['attendance_id'],[g['face'] for g in grants],session['cancelled'].is_set,
                min(g['track']['captured_at'] for g in grants),continuous)
            for key in ids:session['pending_auto'].pop(key,None)
        return jsonify(result)

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
        except DomainError as exc:return error('ATTENDANCE_SESSION_INVALID',str(exc),exc.status)
        if attendance_context is not None:
            supplied_class=request.headers.get('X-Class-Id')
            if supplied_class!=str(session['class_id']):
                return error('CLASS_SCOPE_MISMATCH','Lớp của khung hình không khớp với phiên camera.',409)
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
                    session['sequence']+=1;sequence=session['sequence'];session['pending_auto'].clear()
                    snapshot=session['continuity'].snapshot(bgr,received_at)
            kwargs=dict(profile=session['profile'])
            inference_started=time.monotonic()
            if attendance_context is not None:
                kwargs['allowed_student_ids']=attendance_context['student_ids']
                kwargs['class_id']=session['class_id']
            result = (service.detect_preview(bgr,session['cancelled'].is_set) if preview else
                      service.analyze(bgr, session['cancelled'].is_set,**kwargs))
            if attendance_context is not None:
                attendance.context(session['attendance_id'])
                result['attendance_session_id']=session['attendance_id']
            if session['cancelled'].is_set():
                raise SessionStopped()
            with sessions_guard:
                if preview:session['continuity'].update(result['faces'],result['frame'],bgr,received_at)
                else:
                    result['request_id']=sequence
                    result['frame_timing']=dict(server_received_at=received_at,inference_started_at=inference_started,result_at=time.monotonic(),
                        client_captured_ms=request.headers.get('X-Client-Captured-Ms'))
                    linked=session['continuity'].bind(result['faces'],result['frame'],snapshot,time.monotonic())
            if not preview and attendance_context is not None:
                with sessions_guard:
                    grants=[];current_history={};pending_consistency=False
                    for face,saved in zip(result['faces'],linked):
                        frame_verified=face.get('identity_confirmed') is True
                        face['identity_confirmed']=False
                        if saved and sequence==session['sequence']:
                            student_id=face.get('top1',{}).get('student',{}).get('id') if frame_verified else None
                            state,consistency=observe(session['verified_history'].get(saved['id'],{}),student_id,sequence)
                            current_history[saved['id']]=state
                            face['verification_consistency']=consistency
                            face['identity_confirmed']=consistency['ready']
                            if not consistency['ready']:
                                pending_consistency=True;continue
                            if face.get('outside_class') or not face.get('eligible_for_confirmation'):continue
                            grant=secrets.token_urlsafe(32)
                            session['pending_auto'][grant]=dict(face=face,track=saved,sequence=sequence,created_at=time.monotonic(),
                                signature=getattr(service.matcher,'signature',None),policy=json.dumps(service.matcher.policy,sort_keys=True))
                            grants.append(dict(grant_id=grant,face_id=face['face_id'],server_track_id=saved['id']))
                    if sequence==session['sequence']:session['verified_history']=current_history
                    result['automatic_attendance']=dict(enabled=automation_status(session['profile'])['ready'],changed=False,grants=grants,
                        pending_consistency=pending_consistency,
                        reason='awaiting_current_track_commit' if grants else 'no_verified_current_face')
                result['attendance_enabled']=result['automatic_attendance']['enabled']
                result['identity_confirmation_enabled']=any(f.get('identity_confirmed') is True for f in result['faces'])
                result['automatic_attendance']['status']=automation_status(session['profile'])
            result.setdefault('timings_ms',{})['decode_ms']=decode_ms
            records=attendance.detail(session['attendance_id'])['records'] if attendance_context else []
            result['recognition_results']=normalize_faces(result['faces'],'live_camera',records)
            for public,face in zip(result['recognition_results'],result['faces']):
                public['auto_attendance_eligible']=bool(face.get('identity_confirmed') and face.get('tracking',{}).get('valid') and face.get('verification_consistency',{}).get('ready') and not face.get('outside_class') and result.get('attendance_enabled'))
                public['presence_verified']=public['auto_attendance_eligible']
                public['presence_verification_method']='server_track_continuity_not_anti_spoofing' if public['presence_verified'] else None
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
