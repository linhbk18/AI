"""Additive management routes; existing camera contracts remain in app.py."""
from io import BytesIO
import hashlib
import warnings

from flask import Blueprint, current_app, jsonify, render_template, request
from PIL import Image, ImageOps, UnidentifiedImageError
import numpy as np

from .database.db import DomainError
from .face_recognition.runtime_service import EngineBusy, EngineUnavailable


def management_blueprint(store,engine,token):
    bp=Blueprint('management',__name__)

    @bp.errorhandler(DomainError)
    def domain_error(exc):
        return jsonify(error=dict(code='MANAGEMENT_ERROR',message=str(exc))),exc.status

    @bp.before_request
    def upload_limit():
        if request.path.endswith('/faces') and request.method=='POST':
            request.max_content_length=5*1024*1024

    @bp.get('/overview')
    @bp.get('/classes')
    @bp.get('/classes/<int:identity>')
    @bp.get('/students')
    @bp.get('/students/<int:identity>')
    @bp.get('/students/new')
    @bp.get('/students/<int:identity>/face')
    def page(identity=None):
        return render_template('management.html',csrf_token=token)

    @bp.get('/api/overview')
    def overview(): return jsonify(store.overview())

    @bp.get('/api/legacy-gallery')
    def legacy(): return jsonify(items=store.legacy_options())

    def refresh():
        return bool(hasattr(engine,'refresh_gallery') and engine.refresh_gallery())

    # Explicit route registration keeps the table selector private and fixed.
    def register(kind):
        def collection():
            if request.method=='GET': return jsonify(items=store.list_entities(kind,request.args.get('q','')))
            return jsonify(store.save(kind,request.get_json(silent=True))),201
        def detail(identity):
            if request.method=='GET': return jsonify(store.detail(kind,identity))
            if request.method=='DELETE':
                store.delete(kind,identity);return jsonify(deleted=True,archived=True)
            result=store.save(kind,request.get_json(silent=True),identity)
            if kind=='students': refresh()
            return jsonify(result)
        bp.add_url_rule('/api/'+kind,kind+'_list',collection,methods=['GET','POST'])
        bp.add_url_rule('/api/'+kind+'/<int:identity>',kind+'_detail',detail,methods=['GET','PUT','DELETE'])
    register('classes');register('students')

    @bp.post('/api/classes/<int:class_id>/students')
    def add_member(class_id):
        data=request.get_json(silent=True)
        if not isinstance(data,dict): raise DomainError('Nội dung phải là JSON object.')
        store.membership(class_id,data.get('student_id'))
        return jsonify(added=True),201

    @bp.delete('/api/classes/<int:class_id>/students/<int:student_id>')
    def remove_member(class_id,student_id):
        store.membership(class_id,student_id,True)
        return jsonify(removed=True)

    @bp.post('/api/students/<int:student_id>/legacy-mapping')
    def map_legacy(student_id):
        data=request.get_json(silent=True)
        if not isinstance(data,dict): raise DomainError('Nội dung phải là JSON object.')
        store.map_legacy(student_id,data.get('dataset_label'),data.get('confirmed'))
        return jsonify(mapped=True,gallery_updated=refresh()),201

    @bp.post('/api/students/<int:student_id>/faces')
    def register_face(student_id):
        if not current_app.config.get('LEGACY_PHOTO_UPLOAD_ENABLED'):
            raise DomainError('Đăng ký bằng ảnh tải lên/chụp thủ công đã ngừng. Dùng "Đăng ký khuôn mặt bằng AI" qua camera trực tiếp.',410)
        store.detail('students',student_id)
        if request.form.get('confirmed')!='true':
            raise DomainError('Hãy xác nhận ảnh thuộc đúng sinh viên đã chọn. AI không tự xác minh điều này.')
        upload=request.files.get('image')
        if upload is None: raise DomainError('Chưa có ảnh tải lên.')
        raw=upload.read(4*1024*1024+1)
        if not raw or len(raw)>4*1024*1024: raise DomainError('Ảnh phải lớn hơn 0 và không quá 4 MB.',413)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(BytesIO(raw)) as image:
                    if image.format not in ('JPEG','PNG','WEBP') or getattr(image,'n_frames',1)!=1:
                        raise DomainError('Chỉ nhận ảnh tĩnh JPEG, PNG hoặc WebP.',415)
                    if max(image.size)>4096 or min(image.size)<32:
                        raise DomainError('Kích thước ảnh phải từ 32 đến 4096 pixel mỗi cạnh.',422)
                    rgb=np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
            bgr=np.ascontiguousarray(rgb[:,:,::-1])
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:
            raise DomainError('Ảnh không đọc được hoặc vượt giới hạn an toàn.',422) from exc
        try:
            vector,pipeline=engine.enroll(bgr)
        except EngineBusy:
            raise DomainError('AI đang xử lý camera hoặc ảnh khác. Tắt camera rồi thử lại.',429)
        except EngineUnavailable:
            raise DomainError('Mô hình đang tải hoặc chưa sẵn sàng. Vui lòng thử lại sau.',503)
        except ValueError as exc:
            if str(exc)=='NO_FACE': raise DomainError('Không phát hiện khuôn mặt. Hãy chọn ảnh rõ mặt.',422) from exc
            if str(exc)=='MULTIPLE_FACES': raise DomainError('Ảnh có nhiều khuôn mặt. Chỉ chọn ảnh một sinh viên.',422) from exc
            current_app.logger.exception('Enrollment AI error; uploaded image not saved')
            raise DomainError('AI không tạo được embedding hợp lệ. Kiểm tra Terminal; dữ liệu chưa được lưu.',422) from exc
        except Exception as exc:
            current_app.logger.exception('Enrollment failed; uploaded image not saved')
            raise DomainError('Lỗi xử lý AI. Kiểm tra Terminal và thử lại; dữ liệu chưa được lưu.',500) from exc
        identity=store.add_embedding(student_id,vector,pipeline,hashlib.sha256(raw).hexdigest())
        return jsonify(id=identity,saved=True,gallery_updated=refresh(),
            message='Đã lưu embedding theo hồ sơ được xác nhận thủ công. Ảnh không được lưu; đây không phải xác minh danh tính tự động.'),201

    return bp
