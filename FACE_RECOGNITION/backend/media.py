"""Bounded RAM, one background media job; uploaded media NEVER auto-marks attendance."""
import copy
import math
from pathlib import Path
import secrets
import tempfile
import threading
import time

import cv2
import numpy as np
from PIL import Image,ImageOps,UnidentifiedImageError
from flask import Blueprint,jsonify,request
from werkzeug.utils import secure_filename

from .database.db import DomainError
from .face_recognition.runtime_service import EngineBusy,SessionStopped
from .face_recognition.camera_pipeline import PROFILES
from .face_recognition.results import normalize_faces


class MediaJobs:
    def __init__(self,engine,attendance,temp_dir):
        self.engine=engine;self.attendance=attendance;self.jobs={};self.lock=threading.RLock()
        self.temp_dir=Path(temp_dir);self.temp_dir.mkdir(parents=True,exist_ok=True)
        for p in self.temp_dir.glob('media-*.upload'):
            if p.is_file() and time.time()-p.stat().st_mtime>86400:p.unlink(missing_ok=True)

    def view(self,job):
        return copy.deepcopy({k:v for k,v in job.items() if not k.startswith('_')})

    def get(self,identity):
        with self.lock:
            job=self.jobs.get(identity)
            if not job:raise DomainError('Không tìm thấy tác vụ hoặc tác vụ đã hết hạn.',404)
            return self.view(job)

    def create(self,upload,source,session_id,profile):
        self.attendance.context(session_id)
        if not self.engine.health()['ready']:raise DomainError('Mô hình chưa sẵn sàng.',503)
        if profile not in PROFILES or PROFILES[profile].get('available') is False:raise DomainError('Cấu hình AI không được hỗ trợ.')
        if source not in ('snapshot','image','video'):raise DomainError('Nguồn đầu vào không hợp lệ.')
        suffix=Path(upload.filename or '').suffix.lower()
        if suffix not in (('.mp4','.mov','.webm','.avi') if source=='video' else ('.jpg','.jpeg','.png','.webp')):raise DomainError('Định dạng tệp không được hỗ trợ.',415)
        with self.lock:
            if any(j['state'] in ('queued','running','cancelling') for j in self.jobs.values()):raise DomainError('Đang xử lý một tệp. Hãy chờ hoặc hủy tác vụ trước.',429)
            for key in list(self.jobs):
                if time.monotonic()-self.jobs[key]['_created']>600:self.jobs.pop(key)
            while len(self.jobs)>=10:self.jobs.pop(next(iter(self.jobs)))
            maximum=(64 if source=='video' else 8)*1024*1024
            temp=tempfile.NamedTemporaryFile(prefix='media-',suffix='.upload',dir=self.temp_dir,delete=False);path=Path(temp.name)
            try:
                size=0
                with temp:
                    while True:
                        chunk=upload.stream.read(256*1024)
                        if not chunk:break
                        size+=len(chunk)
                        if size>maximum:raise DomainError('Tệp vượt giới hạn: ảnh 8 MB, video 64 MB.',413)
                        temp.write(chunk)
                if not size:raise DomainError('Tệp trống.')
                if source=='video':
                    with path.open('rb') as f:magic=f.read(16)
                    if not (magic[4:8]==b'ftyp' or magic[:4]==b'RIFF' and magic[8:12]==b'AVI ' or magic[:4]==bytes.fromhex('1a45dfa3')):raise DomainError('Nội dung không phải video được hỗ trợ.',415)
                else:
                    with Image.open(path) as im:
                        if im.format not in ('JPEG','PNG','WEBP'):raise DomainError('Nội dung ảnh không hợp lệ.',415)
                        if im.width*im.height>20_000_000:raise DomainError('Ảnh vượt giới hạn 20 megapixel.',422)
                        im.verify()
                identity=secrets.token_urlsafe(24)
                job=dict(id=identity,source_type=source,filename=secure_filename(upload.filename or 'media'),bytes=size,
                    session_id=session_id,profile=profile,state='queued',processed_frames=0,total_frames=None,progress=None,
                    frames=[],students=[],face_errors=0,error=None,_path=path,_cancel=threading.Event(),_created=time.monotonic())
                self.jobs[identity]=job
                threading.Thread(target=self.run,args=(job,),daemon=True,name='media-analysis').start()
                return self.view(job)
            except (UnidentifiedImageError,OSError) as exc:
                path.unlink(missing_ok=True);raise DomainError('Không đọc được tệp ảnh.',415) from exc
            except Exception:
                path.unlink(missing_ok=True);raise

    def cancel(self,identity):
        with self.lock:
            job=self.jobs.get(identity)
            if not job:raise DomainError('Không tìm thấy tác vụ.',404)
            if job['state'] in ('queued','running','cancelling'):job['_cancel'].set();job['state']='cancelling'
            return self.view(job)

    def infer(self,image,job):
        scale=min(1.,640/image.shape[1],480/image.shape[0])
        if scale<1:image=cv2.resize(image,(round(image.shape[1]*scale),round(image.shape[0]*scale)),interpolation=cv2.INTER_AREA)
        deadline=time.monotonic()+15
        while True:
            if job['_cancel'].is_set():raise SessionStopped()
            context=self.attendance.context(job['session_id'])
            try:return self.engine.analyze(image,job['_cancel'].is_set,profile=job['profile'],allowed_student_ids=context['student_ids'])
            except EngineBusy:
                if time.monotonic()>deadline:raise DomainError('AI đang bận với camera khác. Hãy tắt camera rồi thử lại.',429)
                time.sleep(.15)

    def run(self,job):
        cap=None
        try:
            with self.lock:job['state']='running'
            if job['source_type']=='video':
                cap=cv2.VideoCapture(str(job['_path']))
                fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
                if not cap.isOpened() or not math.isfinite(fps) or fps<=0 or count<=0:raise DomainError('Video hỏng hoặc codec chưa được hỗ trợ.',415)
                duration=count/fps
                if cap.get(cv2.CAP_PROP_FRAME_WIDTH)*cap.get(cv2.CAP_PROP_FRAME_HEIGHT)>1920*1080:raise DomainError('Video vượt độ phân giải 1080p; hãy giảm kích thước trước khi tải.',422)
                if not math.isfinite(duration) or duration>120:raise DomainError('Video vượt giới hạn 120 giây.',422)
                sample_indices=list(range(0,int(count),max(1,round(fps*2))))
                if len(sample_indices)>60:raise DomainError('Video vượt giới hạn 60 frame phân tích.',422)
                with self.lock:job.update(duration_seconds=duration,fps=fps,video_frames=int(count),total_frames=len(sample_indices),progress=0)
            else:sample_indices=[0];job['total_frames']=1;job['progress']=0
            students={};total_faces=0
            for index in sample_indices:
                if job['_cancel'].is_set():raise SessionStopped()
                if cap:
                    cap.set(cv2.CAP_PROP_POS_FRAMES,index);ok,image=cap.read()
                    if not ok or image is None:raise DomainError('Không giải mã được một frame trong video.',415)
                    reported=cap.get(cv2.CAP_PROP_POS_MSEC)/1000
                    estimated=not (math.isfinite(reported) and 0<=reported<=duration+1 and (index==0 or reported>0))
                    position=index/fps if estimated else reported
                else:
                    with Image.open(job['_path']) as im:
                        im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((640,480));image=np.asarray(im)[:,:,::-1].copy()
                    position=None;estimated=False
                start=time.perf_counter();result=self.infer(image,job)
                if job['_cancel'].is_set():raise SessionStopped()
                detail=self.attendance.detail(job['session_id'])
                if detail['status']!='IN_PROGRESS':raise DomainError('Buổi đã kết thúc; không dùng kết quả cũ.',409)
                faces=normalize_faces(result['faces'],job['source_type'],detail['records'],position)
                total_faces+=len(faces)
                if total_faces>2000:raise DomainError('Tệp có quá nhiều kết quả; hãy dùng video ngắn hơn.',422)
                for face in faces:
                    sid=face['student_id']
                    if sid is None:continue
                    old=students.get(sid)
                    if old is None:students[sid]=dict(student_id=sid,student_code=face['student_code'],student_name=face['student_name'],similarity=face['similarity'],first_seen_seconds=position,last_seen_seconds=position,appearances=1,verification_status=face['verification_status'])
                    else:old.update(similarity=max(old['similarity'],face['similarity']),last_seen_seconds=position,appearances=old['appearances']+1)
                with self.lock:
                    job['frames'].append(dict(frame_index=index,timestamp_seconds=position,timestamp_estimated=estimated,frame=result['frame'],faces=faces,face_errors=result.get('face_errors',0),detected_faces=result.get('detected_faces',len(faces)),timings_ms=result.get('timings_ms',{}),elapsed_ms=(time.perf_counter()-start)*1000))
                    job['face_errors']+=result.get('face_errors',0)
                    job['students']=list(students.values());job['processed_frames']+=1;job['progress']=job['processed_frames']/job['total_frames']
            with self.lock:job['state']='completed'
        except SessionStopped:
            with self.lock:job['state']='cancelled';job['error']='Đã hủy phân tích; không ghi điểm danh.'
        except Exception as exc:
            with self.lock:job['state']='failed';job['error']=str(exc) if isinstance(exc,DomainError) else 'Không xử lý được tệp. Kiểm tra codec, RAM và Terminal.'
        finally:
            if cap:cap.release()
            job['_path'].unlink(missing_ok=True)

    def confirm(self,identity,ids):
        with self.lock:
            job=self.jobs.get(identity)
            if not job or job['state']!='completed':raise DomainError('Tác vụ chưa hoàn thành hoặc đã hết hạn.',409)
            if not isinstance(ids,list) or not 1<=len(ids)<=500 or any(isinstance(i,bool) or not isinstance(i,int) for i in ids):raise DomainError('Hãy chọn từ 1 đến 500 sinh viên và duyệt danh sách.')
            proposed={s['student_id'] for s in job['students']}
            if not set(ids)<=proposed:raise DomainError('Danh sách có người không thuộc đề xuất của tác vụ.',422)
            # One atomic transaction; source is explicitly a human-reviewed MANUAL proposal.
            return self.attendance.confirm_reviewed(job['session_id'],ids)


def media_blueprint(engine,attendance,temp_dir):
    bp=Blueprint('media',__name__);manager=MediaJobs(engine,attendance,temp_dir)
    @bp.post('/api/media/jobs')
    def create():
        upload=request.files.get('file')
        if not upload:raise DomainError('Chưa chọn tệp.')
        try:sid=int(request.form.get('session_id',''))
        except ValueError:raise DomainError('Hãy bắt đầu buổi điểm danh trước.')
        return jsonify(manager.create(upload,request.form.get('source_type','image'),sid,request.form.get('profile','retinaface_scaled_experimental'))),202
    @bp.get('/api/media/jobs/<identity>')
    def get(identity):return jsonify(manager.get(identity))
    @bp.delete('/api/media/jobs/<identity>')
    def cancel(identity):return jsonify(manager.cancel(identity))
    @bp.post('/api/media/jobs/<identity>/confirm')
    def confirm(identity):
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or data.get('confirmed') is not True:raise DomainError('Cần người dùng duyệt và xác nhận rõ ràng.')
        return jsonify(manager.confirm(identity,data.get('student_ids')))
    return bp,manager
