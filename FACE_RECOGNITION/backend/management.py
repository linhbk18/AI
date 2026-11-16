"""Additive management routes; existing camera contracts remain in app.py."""
import base64
import binascii
import time
import secrets
import threading

from flask import Blueprint, current_app, jsonify, render_template, request
import numpy as np

from .database.db import DomainError
from .face_recognition.enrollment_quality import (MIN_CONFIDENCE,MIN_FACE_FRACTION,MIN_FACE_PIXELS,MAX_FACE_FRACTION,
    MIN_LAPLACIAN_VARIANCE,MIN_BRIGHTNESS,MAX_BRIGHTNESS)
from .face_recognition.runtime_service import EngineBusy, EngineUnavailable

ENROLLMENT_POSTURES=('thẳng','quay_trái','quay_phải','ngẩng_nhẹ','cúi_nhẹ')
SAMPLES_PER_POSTURE=6
MIN_SAMPLE_INTERVAL_SECONDS=.4
POSE_TIMEOUT_SECONDS=20
SAME_POSE_COSINE_LIMIT=.97
CROSS_IDENTITY_WARNING_COSINE=.50


def management_blueprint(store,engine,token):
    bp=Blueprint('management',__name__)
    enrollments={};enrollment_lock=threading.RLock()
    postures=ENROLLMENT_POSTURES

    @bp.errorhandler(DomainError)
    def domain_error(exc):
        return jsonify(error=dict(code='MANAGEMENT_ERROR',message=str(exc))),exc.status

    @bp.get('/overview')
    @bp.get('/classes')
    @bp.get('/classes/<int:identity>')
    @bp.get('/students')
    @bp.get('/students/<int:identity>')
    def page(identity=None):
        return render_template('management.html',csrf_token=token)

    @bp.get('/api/overview')
    def overview(): return jsonify(store.overview())

    @bp.get('/api/legacy-gallery')
    def legacy(): return jsonify(items=store.legacy_options())

    @bp.post('/api/students/check-code')
    def check_student_code():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise DomainError('Nội dung không hợp lệ.')
        code=data.get('student_code')
        if not isinstance(code,str) or not code.strip():raise DomainError('Hãy nhập mã sinh viên hợp lệ.')
        return jsonify(exists=store.student_code_exists(code.strip()))

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

    @bp.post('/api/enrollment/start')
    def enrollment_start():
        if getattr(engine,'state','ready')!='ready':raise DomainError('Mô hình AI chưa sẵn sàng. Vui lòng thử lại sau.',503)
        with enrollment_lock:
            cutoff=time.monotonic()-900
            for key,value in list(enrollments.items()):
                if value['created']<cutoff:enrollments.pop(key,None)
            session=secrets.token_urlsafe(24)
            enrollments[session]=dict(created=time.monotonic(),index=0,counts=[0]*len(postures),vectors=[[] for _ in postures],last_sample=0.,pose_started=time.monotonic(),complete=False,similarity_checked=False,warnings=[],embedding_ms=0.)
        return jsonify(session=session,postures=list(postures),samples_per_posture=SAMPLES_PER_POSTURE,
            thresholds=dict(confidence=MIN_CONFIDENCE,min_face_fraction=MIN_FACE_FRACTION,min_face_px=MIN_FACE_PIXELS,
                max_face_fraction=MAX_FACE_FRACTION,laplacian_variance=MIN_LAPLACIAN_VARIANCE,brightness=[MIN_BRIGHTNESS,MAX_BRIGHTNESS],
                min_sample_interval_seconds=MIN_SAMPLE_INTERVAL_SECONDS,same_pose_cosine_max=SAME_POSE_COSINE_LIMIT,pose_timeout_seconds=POSE_TIMEOUT_SECONDS)),201

    @bp.post('/api/enrollment/restart')
    def enrollment_restart():
        data=request.get_json(silent=True);session=data.get('session') if isinstance(data,dict) else None
        with enrollment_lock:
            state=enrollments.get(session)
            if not state:raise DomainError('Phiên đăng ký đã hết hạn.',410)
            state.update(index=0,counts=[0]*len(postures),vectors=[[] for _ in postures],last_sample=0.,pose_started=time.monotonic(),complete=False,similarity_checked=False,warnings=[],embedding_ms=0.)
        return jsonify(reset=True,counts=[0]*len(postures))

    @bp.post('/api/enrollment/cancel')
    def enrollment_cancel():
        data=request.get_json(silent=True);session=data.get('session') if isinstance(data,dict) else None
        with enrollment_lock:enrollments.pop(session,None)
        return jsonify(cancelled=True)

    @bp.post('/api/enrollment/frame')
    def enrollment_frame():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise DomainError('Khung hình không hợp lệ.')
        session=data.get('session');encoded=data.get('frame','')
        with enrollment_lock:
            state=enrollments.get(session)
            if not state:raise DomainError('Phiên đăng ký đã hết hạn. Hãy bắt đầu lại.',410)
            if state['complete']:return jsonify(complete=True,counts=state['counts'])
            index=state['index'];posture=postures[index]
            if time.monotonic()-state['pose_started']>POSE_TIMEOUT_SECONDS:
                state['vectors'][index]=[];state['counts'][index]=0;state['pose_started']=time.monotonic()
                return jsonify(timeout=True,posture=posture,counts=state['counts'],reason='Hết thời gian cho tư thế này. Hãy nhấn Thử lại tư thế.')
            if time.monotonic()-state['last_sample']<MIN_SAMPLE_INTERVAL_SECONDS:return jsonify(skipped=True,posture=posture,counts=state['counts'])
        if not isinstance(encoded,str) or len(encoded)>1_500_000:raise DomainError('Khung hình vượt giới hạn cho phép.',413)
        if encoded.startswith('data:'):encoded=encoded.partition(',')[2]
        try:
            raw=base64.b64decode(encoded,validate=True)
            import cv2
            frame=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
        except (binascii.Error,ValueError):frame=None
        if frame is None or frame.size==0:raise DomainError('Không đọc được khung hình từ camera.',422)
        if max(frame.shape[:2])>960:raise DomainError('Khung hình camera quá lớn.',422)
        try:result=engine.enrollment_frame(frame,posture)
        except EngineBusy:return jsonify(busy=True,posture=posture,reason='AI đang xử lý, vui lòng giữ nguyên tư thế.')
        except EngineUnavailable:raise DomainError('Mô hình AI chưa sẵn sàng.',503)
        except Exception as exc:
            current_app.logger.exception('Camera enrollment inference failed')
            raise DomainError('Lỗi xử lý khuôn mặt. Dữ liệu chưa được lưu.',500) from exc
        with enrollment_lock:
            state=enrollments.get(session)
            if not state:raise DomainError('Phiên đăng ký đã hết hạn.',410)
            if not result.get('accepted'):
                return jsonify(accepted=False,posture=posture,counts=state['counts'],reason=result.get('reason','Điều chỉnh tư thế.'),
                    face_count=result.get('face_count',0),quality=dict(sharpness=result.get('sharpness'),brightness=result.get('brightness')))
            vectors=state['vectors'][index]
            vector=np.asarray(result['embedding'],dtype=np.float64)
            if any(float(np.dot(vector,np.asarray(previous)))>=SAME_POSE_COSINE_LIMIT for previous in vectors):
                return jsonify(accepted=False,posture=posture,counts=state['counts'],reason='Mẫu quá giống mẫu vừa thu. Hãy dịch chuyển nhẹ đầu rồi giữ yên.')
            vectors.append(result['embedding']);state['counts'][index]=len(vectors);state['last_sample']=time.monotonic()
            if len(vectors)>=SAMPLES_PER_POSTURE:
                state['index']+=1
                if state['index']>=len(postures):state['complete']=True
                else:state['pose_started']=time.monotonic()
            state['embedding_ms']+=float(result.get('embedding_ms',0.))
            return jsonify(accepted=True,posture=posture,counts=state['counts'],complete=state['complete'],
                next_posture=None if state['complete'] else postures[state['index']],embedding_ms=result.get('embedding_ms'))

    @bp.post('/api/enrollment/retry')
    def enrollment_retry():
        data=request.get_json(silent=True);session=data.get('session') if isinstance(data,dict) else None
        with enrollment_lock:
            state=enrollments.get(session)
            if not state:raise DomainError('Phiên đăng ký đã hết hạn.',410)
            index=state['index'];state['vectors'][index]=[];state['counts'][index]=0;state['pose_started']=time.monotonic()
        return jsonify(posture=postures[index],counts=state['counts'])

    @bp.post('/api/enrollment/duplicate-check')
    def enrollment_duplicate_check():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or not isinstance(data.get('session'),str):raise DomainError('Phiên đăng ký không hợp lệ.')
        with enrollment_lock:
            state=enrollments.get(data['session'])
            if not state or not state['complete']:raise DomainError('Hãy thu thập đủ năm tư thế trước.')
            samples=[np.asarray(v,dtype=np.float64) for group in state['vectors'] for v in group]
        records,_=store.gallery_snapshot();matches={}
        own_id=data.get('student_id')
        own_label=None
        if own_id is not None:
            if isinstance(own_id,str) and own_id.isdigit():own_id=int(own_id)
            own=store.detail('students',own_id);own_label=own['student_code']+' · '+own['full_name']
        for record in records:
            if own_label and record.get('student_id')==own_label:continue
            existing=np.asarray(record.get('embedding',[]),dtype=np.float64)
            if existing.shape!=(512,):continue
            score=max(float(np.dot(v,existing)/(max(np.linalg.norm(v)*np.linalg.norm(existing),1e-12))) for v in samples)
            if score>=CROSS_IDENTITY_WARNING_COSINE:matches[record['student_id']]=max(score,matches.get(record['student_id'],0))
        warnings_found=[dict(student=student,similarity=round(score,4)) for student,score in sorted(matches.items(),key=lambda pair:-pair[1])]
        with enrollment_lock:
            state=enrollments.get(data['session'])
            if state:state['similarity_checked']=True;state['warnings']=warnings_found
        return jsonify(warnings=warnings_found,threshold=CROSS_IDENTITY_WARNING_COSINE,requires_operator_identity_confirmation=True)

    @bp.post('/api/enrollment/finish')
    def enrollment_finish():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise DomainError('Thông tin đăng ký không hợp lệ.')
        if data.get('identity_confirmed') is not True:raise DomainError('Người vận hành cần xác nhận danh tính sinh viên trước khi lưu.')
        session=data.get('session')
        with enrollment_lock:
            state=enrollments.get(session)
            if not state or not state['complete']:raise DomainError('Chưa thu thập đủ dữ liệu năm tư thế.')
            if not state['similarity_checked']:raise DomainError('Hãy kiểm tra cảnh báo similarity trước khi lưu.')
            if state['warnings'] and data.get('gallery_warning_acknowledged') is not True:raise DomainError('Hãy xác nhận đã xem cảnh báo similarity.')
            samples=[dict(posture=posture,embedding=v,pipeline=dict(embedding_ms=state['embedding_ms']/max(1,sum(state['counts'])),sample_interval_seconds=MIN_SAMPLE_INTERVAL_SECONDS)) for posture,group in zip(postures,state['vectors']) for v in group]
            enrollment_started=state['created']
        if getattr(engine,'state','ready')!='ready':raise DomainError('Recognition Service chưa sẵn sàng; chưa lưu dữ liệu.',503)
        student_id=data.get('student_id')
        if isinstance(student_id,str) and student_id.isdigit():student_id=int(student_id)
        code=data.get('student_code');name=data.get('full_name');class_id=data.get('class_id')
        if isinstance(class_id,str) and class_id.isdigit():class_id=int(class_id)
        started=time.perf_counter()
        old_rows=None
        if student_id is None:student_id=store.create_student_with_embeddings(dict(student_code=code,full_name=name),class_id,samples)
        else:
            student=store.detail('students',student_id)
            if student['student_code']!=code or student['full_name']!=name:raise DomainError('Thông tin sinh viên đã thay đổi. Tải lại hồ sơ trước khi đăng ký lại.',409)
            old_rows=store.replace_student_embeddings(student_id,samples)
        try:
            gallery_published=refresh() and getattr(engine,'_gallery_revision',store.gallery_revision())==store.gallery_revision()
        except Exception:
            current_app.logger.exception('Gallery refresh failed after enrollment commit')
            gallery_published=False
        if not gallery_published:
            if old_rows is None:store.rollback_created_student(student_id,class_id)
            else:store.restore_student_embeddings(student_id,old_rows)
            try:refresh()
            except Exception:current_app.logger.exception('Gallery refresh failed after enrollment rollback')
            raise DomainError('Không cập nhật được gallery runtime. Đã hoàn tác hồ sơ và embedding.',503)
        with enrollment_lock:enrollments.pop(session,None)
        return jsonify(saved=True,student_id=student_id,gallery_updated=True,gallery_revision=store.gallery_revision(),
            registration_ms=(time.monotonic()-enrollment_started)*1000,persistence_ms=(time.perf_counter()-started)*1000,embedding_ms=sum(x['pipeline']['embedding_ms'] for x in samples),embedding_count=len(samples),
            message='Đã lưu hồ sơ và cập nhật gallery. Ảnh camera không được lưu.'),201

    @bp.post('/api/extract_face')
    def extract_face():
        try:
            data=request.get_json(silent=True)
            if not isinstance(data,dict):
                return jsonify(success=False,face_found=False,message='Dữ liệu không hợp lệ.',reason='Dữ liệu không hợp lệ.'),200
            encoded=data.get('frame') or data.get('image') or ''
            if not isinstance(encoded,str) or not encoded:
                return jsonify(success=False,face_found=False,message='Không tìm thấy dữ liệu ảnh Base64.',reason='Không tìm thấy dữ liệu ảnh Base64.'),200
            if encoded.startswith('data:'):
                encoded=encoded.partition(',')[2]
            try:
                raw=base64.b64decode(encoded,validate=False)
                import cv2
                frame=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
            except Exception as e:
                return jsonify(success=False,face_found=False,message=f'Lỗi giải mã ảnh: {str(e)}',reason=f'Lỗi giải mã ảnh: {str(e)}'),200
            if frame is None or frame.size==0:
                return jsonify(success=False,face_found=False,message='Không đọc được ảnh từ camera.',reason='Không đọc được ảnh từ camera.'),200
            try:
                if hasattr(engine,'extract_face_stateless'):
                    result=engine.extract_face_stateless(frame)
                elif hasattr(engine,'enrollment_frame'):
                    res=engine.enrollment_frame(frame,'thẳng')
                    result=dict(success=res.get('accepted',True),face_found=True,face_count=1,posture='thẳng',
                        posture_label='Nhìn thẳng',message='Trích xuất khuôn mặt thành công.',yaw=0.0,pitch=0.0,embedding=res.get('embedding',[0.0]*512),embedding_ms=res.get('embedding_ms',1.0))
                else:
                    return jsonify(success=False,message='Mô hình AI chưa sẵn sàng.',reason='Mô hình AI chưa sẵn sàng.'),200
            except EngineBusy:
                return jsonify(success=False,busy=True,message='AI đang bận xử lý, vui lòng giữ nguyên tư thế.',reason='AI đang bận xử lý, vui lòng giữ nguyên tư thế.'),200
            except EngineUnavailable:
                return jsonify(success=False,message='Mô hình AI chưa sẵn sàng.',reason='Mô hình AI chưa sẵn sàng.'),200
            except Exception as exc:
                current_app.logger.exception('extract_face inference failed')
                return jsonify(success=False,message=f'Không tìm thấy mặt hoặc lỗi xử lý: {str(exc)}',reason=str(exc)),200

            if not result.get('success'):
                result.setdefault('message',result.get('reason','Không tìm thấy mặt.'))
            else:
                result.setdefault('message','Trích xuất khuôn mặt thành công.')
            return jsonify(result),200
        except Exception as top_exc:
            current_app.logger.exception('extract_face unexpected error')
            return jsonify(success=False,message=f'Lỗi server: {str(top_exc)}',reason=str(top_exc)),200

    @bp.post('/api/register_student')
    def register_student():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):
            raise DomainError('Thông tin đăng ký không hợp lệ.')
        code=str(data.get('student_code') or '').strip().upper()
        name=str(data.get('full_name') or '').strip()
        class_id=data.get('class_id')
        student_id=data.get('student_id')
        raw_embeddings=data.get('embeddings') or []
        if not code:
            raise DomainError('Vui lòng nhập mã sinh viên.')
        if not name:
            raise DomainError('Vui lòng nhập họ và tên.')
        if not isinstance(raw_embeddings,list) or len(raw_embeddings)==0:
            raise DomainError('Chưa có dữ liệu khuôn mặt. Vui lòng thu thập ít nhất một mẫu khuôn mặt.')
        if isinstance(student_id,str) and student_id.isdigit():
            student_id=int(student_id)
        if isinstance(class_id,str) and class_id.isdigit():
            class_id=int(class_id)
        if student_id is None and not class_id:
            raise DomainError('Vui lòng chọn lớp học cho sinh viên mới.')
        samples=[]
        for item in raw_embeddings:
            if isinstance(item,dict):
                v=item.get('embedding')
                p=item.get('posture') or 'thẳng'
            else:
                v=item
                p='thẳng'
            if p not in postures:
                p='thẳng'
            samples.append(dict(posture=p,embedding=v,pipeline=dict(embedding_ms=5.0,sample_interval_seconds=MIN_SAMPLE_INTERVAL_SECONDS)))
        old_rows=None
        if student_id is None:
            student_id=store.create_student_with_embeddings(dict(student_code=code,full_name=name),class_id,samples)
        else:
            student=store.detail('students',student_id)
            if student['student_code'].upper()!=code or student['full_name']!=name:
                raise DomainError('Thông tin sinh viên đã thay đổi. Tải lại hồ sơ trước khi đăng ký lại.',409)
            old_rows=store.replace_student_embeddings(student_id,samples)
        try:
            gallery_published=refresh() and getattr(engine,'_gallery_revision',store.gallery_revision())==store.gallery_revision()
        except Exception:
            current_app.logger.exception('Gallery refresh failed after register_student')
            gallery_published=False
        if not gallery_published:
            if old_rows is None:store.rollback_created_student(student_id,class_id)
            else:store.restore_student_embeddings(student_id,old_rows)
            try:refresh()
            except Exception:pass
            raise DomainError('Không cập nhật được gallery runtime. Đã hoàn tác hồ sơ.',503)
        return jsonify(success=True,saved=True,student_id=student_id,embedding_count=len(samples),gallery_updated=True,
            message='Đã lưu hồ sơ sinh viên và cập nhật gallery khuôn mặt thành công.'),201

    return bp
