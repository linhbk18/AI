"""One process-local pretrained pipeline; camera frames exist only in RAM."""
from collections import Counter
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path, PurePosixPath
import threading
import time

import numpy as np

from .face_service import FaceMatcher
from .camera_pipeline import CameraPipeline, YuNetDetector, PROFILES
from .enrollment_quality import face_box_feedback,frame_quality_feedback,estimate_head_pose,posture_matches,classify_posture

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ROOT / 'outputs'
SETTING_KEYS = ('model_name', 'detector_backend', 'align', 'normalization',
                'enforce_detection', 'expand_percentage', 'anti_spoofing', 'max_faces')
logger = logging.getLogger(__name__)
CLASS_SCOPE_MIN_COSINE=.70


class EngineBusy(Exception):
    pass


class EngineUnavailable(Exception):
    pass


class SessionStopped(Exception):
    pass


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class RecognitionEngine:
    def __init__(self,threads=2):
        self.threads=threads
        self._guard = threading.Lock()
        self._state_guard = threading.Lock()
        self._loader = None
        self.state = 'not_started'
        self.model = None
        self.matcher = None
        self.settings = None
        self.model_load_count = 0
        self.inference_count = 0
        self.last_processing_ms = None
        self.pipeline = None
        self.startup_ms = None
        self.arcface_warmup_ms=None
        self.retina_warmup_ms=None
        self.management_store=None
        self._gallery_revision=-1
        self._preview_guard=threading.Lock()
        self._public_identities={}
        self._class_matchers={}

    def configure_store(self, store):
        self.management_store=store
        self._gallery_revision=-1

    def _refresh_gallery_locked(self):
        if self.management_store is None or self.management_store.gallery_revision()==self._gallery_revision:
            return
        records,revision=self.management_store.gallery_snapshot()
        digest=self._base_gallery_hash if revision==0 else hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()
        # A changed gallery cannot inherit an old calibrated rejection policy.
        matcher=FaceMatcher(records,self._gallery_config,digest,None)
        if self._base_policy and self._base_policy.get('pipeline_signature')==matcher.signature:
            matcher.policy=self._base_policy
        identities={s['student_code']+' · '+s['full_name']:dict(id=s['id'],student_code=s['student_code'],full_name=s['full_name'],code_is_temporary=bool(s['student_code_is_temporary'])) for s in self.management_store.list_entities('students')}
        with self._state_guard:
            self.matcher=matcher
            self._gallery_revision=revision
            self._public_identities=identities
            self._class_matchers.clear()

    def _class_matcher(self,class_id,allowed_student_ids):
        allowed=tuple(sorted(set(allowed_student_ids or ())))
        key=(self._gallery_revision,int(class_id),allowed)
        cached=self._class_matchers.get(key)
        if cached is not None:return cached
        records=[]
        id_to_gallery_key={person['id']:key for key,person in self._public_identities.items()}
        scoped_student_ids={id_to_gallery_key[sid] for sid in allowed if sid in id_to_gallery_key}
        for record in self.matcher.records:
            if record['student_id'] in scoped_student_ids:records.append(record)
        digest=hashlib.sha256(json.dumps(dict(base=self.matcher.signature,class_id=int(class_id),students=allowed,
            records=records),sort_keys=True).encode()).hexdigest()
        scoped=FaceMatcher(records,self._gallery_config,digest,None)
        if len(self._class_matchers)>=64:self._class_matchers.clear()
        self._class_matchers[key]=scoped
        return scoped

    def detect_preview(self,bgr,cancelled=lambda:False):
        """Anonymous boxes only; independent of the single ArcFace inference lock."""
        if self.state!='ready': raise EngineUnavailable()
        if not self._preview_guard.acquire(blocking=False): raise EngineBusy()
        start=time.perf_counter()
        try:
            if cancelled():raise SessionStopped()
            areas=self.pipeline.yunet.detect_faces(bgr,dimension=640,score_threshold=.75)
            if cancelled():raise SessionStopped()
            height,width=bgr.shape[:2];faces=[]
            for area in areas:
                x,y=max(0,int(area.x)),max(0,int(area.y))
                right,bottom=min(width,int(area.x+area.w)),min(height,int(area.y+area.h))
                if right>x and bottom>y:
                    faces.append(dict(face_id=len(faces)+1,box=dict(x=x,y=y,w=right-x,h=bottom-y),detection_confidence=float(area.confidence)))
            elapsed=(time.perf_counter()-start)*1000
            return dict(faces=faces,detected_faces=len(faces),frame=dict(width=width,height=height),processing_ms=elapsed,
                preview_only=True,detector='yunet_boxes_only',identity_confirmation_enabled=False,attendance_enabled=False,
                timings_ms=dict(detection_ms=elapsed))
        finally:self._preview_guard.release()

    def refresh_gallery(self):
        if self.state!='ready' or not self._guard.acquire(blocking=False):
            return False
        try:
            self._refresh_gallery_locked()
            return True
        finally:
            self._guard.release()

    def enrollment_frame(self,bgr,posture):
        """Score a transient webcam frame and return one normalized ArcFace vector only when valid."""
        if self.state!='ready': raise EngineUnavailable()
        if not self._guard.acquire(blocking=False): raise EngineBusy()
        try:
            import cv2
            started=time.perf_counter()
            height,width=bgr.shape[:2]
            faces=self.pipeline.yunet.detect_faces(bgr,dimension=640,score_threshold=.6)
            if not faces:return dict(accepted=False,reason='Đưa khuôn mặt vào khung hình.',face_count=0)
            if len(faces)!=1:return dict(accepted=False,reason='Đảm bảo chỉ có một khuôn mặt trong khung.',face_count=len(faces))
            face=faces[0];x,y,w,h=face.x,face.y,face.w,face.h
            box_feedback=face_box_feedback(width,height,x,y,w,h,face.confidence)
            if box_feedback:return dict(accepted=False,reason=box_feedback,face_count=1)
            gray=cv2.cvtColor(bgr[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY)
            sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
            brightness=float(gray.mean())
            quality_feedback=frame_quality_feedback(sharpness,brightness)
            if quality_feedback:return dict(accepted=False,reason=quality_feedback,face_count=1,sharpness=sharpness,brightness=brightness)
            # Landmark based pose estimates; thresholds are calibration starting points, not biometric proof.
            yaw,pitch=estimate_head_pose(face.left_eye,face.right_eye,face.nose_tip,face.left_mouth,face.right_mouth)
            if not posture_matches(posture,yaw,pitch):return dict(accepted=False,reason='Hãy thực hiện đúng tư thế đang được hướng dẫn.',face_count=1,yaw=yaw,pitch=pitch,sharpness=sharpness,brightness=brightness)
            try:
                extracted=self.model.extract_faces(img_path=bgr,detector_backend=self.settings['detector_backend'],
                    enforce_detection=True,align=self.settings['align'],expand_percentage=0,color_face='rgb',normalize_face=True,anti_spoofing=False)
            except ValueError as exc:
                if 'Face could not be detected' in str(exc):return dict(accepted=False,reason='Không căn chỉnh được khuôn mặt. Hãy nhìn rõ vào camera.',face_count=0)
                raise
            if len(extracted)!=1:return dict(accepted=False,reason='AI chưa căn chỉnh được đúng một khuôn mặt.',face_count=len(extracted))
            from deepface.modules import preprocessing
            image=extracted[0]['face'][:,:,::-1]
            tensor=preprocessing.resize_image(image,target_size=(self.pipeline.model.input_shape[1],self.pipeline.model.input_shape[0]))
            tensor=preprocessing.normalize_input(tensor,normalization=self.settings['normalization'])
            embedding_started=time.perf_counter()
            vector=np.asarray(self.pipeline.model.forward(tensor),dtype=np.float64).reshape(-1)
            embedding_ms=(time.perf_counter()-embedding_started)*1000
            norm=float(np.linalg.norm(vector))
            if vector.shape!=(512,) or not np.isfinite(vector).all() or norm==0:raise ValueError('INVALID_EMBEDDING')
            vector=(vector/norm).tolist()
            return dict(accepted=True,embedding=vector,face_count=1,yaw=yaw,pitch=pitch,sharpness=sharpness,
                brightness=brightness,embedding_ms=embedding_ms,processing_ms=(time.perf_counter()-started)*1000)
        finally:self._guard.release()

    def extract_face_stateless(self,bgr):
        """Stateless extraction: detects face, estimates pose (yaw, pitch, posture), and extracts normalized ArcFace embedding."""
        if self.state!='ready':raise EngineUnavailable()
        if not self._guard.acquire(blocking=False):raise EngineBusy()
        try:
            import cv2
            started=time.perf_counter()
            height,width=bgr.shape[:2]
            faces=self.pipeline.yunet.detect_faces(bgr,dimension=640,score_threshold=.6)
            if not faces:return dict(success=False,face_found=False,reason='Không phát hiện khuôn mặt. Hãy nhìn vào camera.')
            if len(faces)!=1:return dict(success=False,face_found=True,face_count=len(faces),reason='Đảm bảo chỉ có một khuôn mặt trong khung.')
            face=faces[0];x,y,w,h=face.x,face.y,face.w,face.h
            box_feedback=face_box_feedback(width,height,x,y,w,h,face.confidence)
            if box_feedback:return dict(success=False,face_found=True,face_count=1,reason=box_feedback)
            crop=bgr[max(0,y):min(height,y+h),max(0,x):min(width,x+w)]
            if crop.size==0:return dict(success=False,face_found=True,face_count=1,reason='Khuôn mặt nằm ngoài khung hình.')
            gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
            sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
            brightness=float(gray.mean())
            quality_feedback=frame_quality_feedback(sharpness,brightness)
            if quality_feedback:return dict(success=False,face_found=True,face_count=1,reason=quality_feedback,sharpness=sharpness,brightness=brightness)
            yaw,pitch=estimate_head_pose(face.left_eye,face.right_eye,face.nose_tip,face.left_mouth,face.right_mouth)
            posture=classify_posture(yaw,pitch)
            try:
                extracted=self.model.extract_faces(img_path=bgr,detector_backend=self.settings['detector_backend'],
                    enforce_detection=True,align=self.settings['align'],expand_percentage=0,color_face='rgb',normalize_face=True,anti_spoofing=False)
            except ValueError as exc:
                if 'Face could not be detected' in str(exc):return dict(success=False,face_found=False,reason='Không căn chỉnh được khuôn mặt. Hãy nhìn rõ vào camera.')
                raise
            if len(extracted)!=1:return dict(success=False,face_found=True,face_count=len(extracted),reason='AI chưa căn chỉnh được đúng một khuôn mặt.')
            from deepface.modules import preprocessing
            image=extracted[0]['face'][:,:,::-1]
            tensor=preprocessing.resize_image(image,target_size=(self.pipeline.model.input_shape[1],self.pipeline.model.input_shape[0]))
            tensor=preprocessing.normalize_input(tensor,normalization=self.settings['normalization'])
            embedding_started=time.perf_counter()
            vector=np.asarray(self.pipeline.model.forward(tensor),dtype=np.float64).reshape(-1)
            embedding_ms=(time.perf_counter()-embedding_started)*1000
            norm=float(np.linalg.norm(vector))
            if vector.shape!=(512,) or not np.isfinite(vector).all() or norm==0:raise ValueError('INVALID_EMBEDDING')
            vector=(vector/norm).tolist()
            posture_labels={'thẳng':'Nhìn thẳng','quay_trái':'Quay đầu sang trái','quay_phải':'Quay đầu sang phải','ngẩng_nhẹ':'Ngẩng nhẹ','cúi_nhẹ':'Cúi nhẹ'}
            return dict(success=True,face_found=True,face_count=1,posture=posture,posture_label=posture_labels.get(posture),
                yaw=round(float(yaw),2),pitch=round(float(pitch),2),sharpness=round(sharpness,1),brightness=round(brightness,1),
                embedding=vector,embedding_ms=round(embedding_ms,2),processing_ms=round((time.perf_counter()-started)*1000,2))
        finally:self._guard.release()

    def start_loading(self):
        with self._state_guard:
            if self._loader is not None:
                return
            self.state = 'loading'
            self._loader = threading.Thread(target=self._load, name='face-model-loader', daemon=True)
            self._loader.start()

    def _load(self):
        load_start=time.perf_counter()
        try:
            config = json.loads((OUTPUTS / 'face_config.json').read_text(encoding='utf-8'))
            settings = {key: config[key] for key in SETTING_KEYS}
            if (settings['model_name'] != 'ArcFace' or settings['detector_backend'] != 'retinaface'
                    or settings['align'] is not True or settings['normalization'] != 'base'
                    or settings['max_faces'] is not None or settings['enforce_detection'] is not True
                    or settings['expand_percentage']!=0 or settings['anti_spoofing'] is not False
                    or config['embedding_dimension'] != 512 or config['status'] != 'complete'):
                raise ValueError('Unsupported enrollment configuration; no settings changed')
            for package, version in config['packages'].items():
                if importlib.metadata.version(package) != version:
                    raise ValueError(f'Enrollment package version mismatch: {package}')
            for name, metadata in config['weights'].items():
                if file_hash(OUTPUTS / '.deepface' / 'weights' / name) != metadata['sha256']:
                    raise ValueError(f'Missing/changed pretrained weights: {name}; downloads disabled')
            gallery_file = OUTPUTS / 'face_embeddings.json'
            gallery = json.loads(gallery_file.read_text(encoding='utf-8'))
            if not isinstance(gallery, list) or len(gallery) != 92:
                raise ValueError('Expected 92 registered vectors')
            seen = set()
            for record in gallery:
                source = PurePosixPath(record['source'])
                if (len(source.parts) < 4 or source.parts[:2] != ('DATASET', 'registered')
                        or source.parts[2] != record['student_id'] or '..' in source.parts
                        or record['source'] in seen or record['face_count'] != 1):
                    raise ValueError('Invalid or duplicate registered embedding provenance')
                for key in ('model_name', 'detector_backend', 'align', 'normalization', 'enforce_detection'):
                    if record[key] != settings[key]:
                        raise ValueError(f'Registered embedding setting mismatch: {key}')
                seen.add(record['source'])
            if len(Counter(r['student_id'] for r in gallery)) != 5:
                raise ValueError('Expected five registered identities')
            policy = json.loads((OUTPUTS / 'recognition_threshold.json').read_text(encoding='utf-8'))
            self.matcher = FaceMatcher(gallery, config, file_hash(gallery_file), policy)
            self._gallery_config=config
            self._base_gallery_hash=file_hash(gallery_file)
            self._base_policy=policy
            self._refresh_gallery_locked()
            os.environ['DEEPFACE_HOME'] = str(OUTPUTS)
            os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
            import tensorflow as tf
            import cv2
            tf.config.threading.set_intra_op_parallelism_threads(self.threads)
            tf.config.threading.set_inter_op_parallelism_threads(1)
            cv2.setNumThreads(self.threads)
            from deepface import DeepFace
            from deepface.modules import modeling
            arcface=DeepFace.build_model(model_name=settings['model_name'])
            retina=modeling.build_model(task='face_detector', model_name=settings['detector_backend'])
            yunet_path=OUTPUTS/'models'/'face_detection_yunet_2023mar.onnx'
            if file_hash(yunet_path)!='8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4':
                raise ValueError('YuNet experiment weight checksum mismatch')
            self.pipeline=CameraPipeline(arcface,retina,YuNetDetector(yunet_path),settings,DeepFace)
            # Warm only ArcFace's small tensor path; no real image, matching or gallery write.
            warm_start=time.perf_counter()
            arcface.forward(np.zeros((1,112,112,3),dtype=np.float32))
            self.arcface_warmup_ms=round((time.perf_counter()-warm_start)*1000,2)
            # Move the native RetinaFace first-call graph setup to explicit startup.
            # Synthetic RAM-only frame; does not create enrollment or test samples.
            warm_start=time.perf_counter()
            self.pipeline.native_retina_faces(np.zeros((480,640,3),dtype=np.uint8),max_dimension=480)
            self.pipeline.yunet.detect_faces(np.zeros((480,640,3),dtype=np.uint8),640,.75)
            self.retina_warmup_ms=round((time.perf_counter()-warm_start)*1000,2)
            self.model = DeepFace
            self.settings = settings
            with self._state_guard:
                self.model_load_count += 1
                self.state = 'ready'
                self.startup_ms=round((time.perf_counter()-load_start)*1000,2)
            logger.info('ArcFace/RetinaFace ready; 92 registered vectors loaded once; no camera storage')
        except Exception:
            logger.exception('Cannot initialize the configured face pipeline')
            with self._state_guard:
                self.state = 'error'

    def health(self):
        with self._state_guard:
            return dict(state=self.state, ready=self.state == 'ready',
                        model_name='ArcFace', detector_backend='retinaface', align=True, normalization='base',
                        registered_embeddings=len(self.matcher.records) if self.matcher is not None else 0,
                        registered_students=len(self.matcher.students) if self.matcher is not None else 0,
                        threshold_enabled=bool(self.matcher and self.matcher.policy and self.matcher.policy.get('enabled')),
                        identity_confirmation_enabled=False, attendance_enabled=False,
                        model_load_count=self.model_load_count, inference_count=self.inference_count,
                        startup_ms=self.startup_ms, profiles=PROFILES,
                        arcface_warmup_ms=self.arcface_warmup_ms,
                        retina_and_preview_warmup_ms=self.retina_warmup_ms,
                        preview_detector='YuNet · vị trí khuôn mặt, không nhận diện',
                        busy=self._guard.locked(), last_processing_ms=self.last_processing_ms)

    def analyze(self, bgr, cancelled=lambda: False, profile='retinaface_reuse',allowed_student_ids=None,class_id=None):
        if self.state != 'ready':
            raise EngineUnavailable()
        if not self._guard.acquire(blocking=False):
            raise EngineBusy()
        start = time.perf_counter()
        try:
            self._refresh_gallery_locked()
            if cancelled():
                raise SessionStopped()
            matcher=self._class_matcher(class_id,allowed_student_ids) if class_id is not None else self.matcher
            height, width = bgr.shape[:2]
            try:
                faces,timings=self.pipeline.run(bgr,profile,cancelled)
            except InterruptedError:
                raise SessionStopped()
            if cancelled():
                raise SessionStopped()
            public_faces = []
            errors = 0
            match_start=time.perf_counter()
            for face in sorted(faces, key=lambda f: (f['facial_area']['y'], f['facial_area']['x'])):
                if cancelled():
                    raise SessionStopped()
                try:
                    area = face['facial_area']
                    x, y = max(0, int(area['x'])), max(0, int(area['y']))
                    right = min(width, int(area['x'] + area['w']))
                    bottom = min(height, int(area['y'] + area['h']))
                    if right <= x or bottom <= y:
                        raise ValueError('Invalid face bounds')
                    match=matcher.match(face['embedding']) if class_id is not None else self.matcher.match(face['embedding'],allowed_students=allowed_student_ids)
                    scope_match=bool(match and (class_id is None or match['best_similarity']>=CLASS_SCOPE_MIN_COSINE))
                    unknown=not scope_match
                    if match and PROFILES.get(profile,{}).get('experimental',False):
                        # Enrollment rejection thresholds cannot be inherited by a different detector.
                        match['decision']='unconfirmed'
                    verifier=getattr(self,'automatic_verifier',None)
                    verified=bool(class_id is None and match and verifier and verifier.verify(match,self.matcher.signature,profile,self.matcher.policy))
                    # Explicit public whitelist: no embedding, gallery image or filesystem path.
                    public_faces.append(dict(face_id=len(public_faces) + 1,
                        box=dict(x=x, y=y, w=right-x, h=bottom-y),
                        status='unknown' if unknown else 'candidate',
                        name='Không xác định' if unknown else self._public_identities.get(match['best_candidate'],{}).get('full_name'),
                        top1=dict(candidate=match['best_candidate'] if scope_match else None, similarity=match['best_similarity'] if scope_match else None,
                            student=self._public_identities.get(match['best_candidate']) if scope_match else None),
                        top2=dict(candidate=match['second_candidate'] if scope_match else None, similarity=match['second_similarity'] if scope_match else None),
                        margin=match['margin'] if scope_match else None, decision='verified_for_automatic_attendance' if verified else ('unknown' if unknown else match['decision']),
                        eligible_for_confirmation=bool(scope_match and self._public_identities.get(match['best_candidate'])),
                        outside_class=False,scope_match=scope_match,unknown=unknown,
                        identity_confirmed=verified, label='Không xác định' if unknown else ('Đã xác minh' if verified else 'Ứng viên chưa xác nhận'),
                        manual_review_required=not verified))
                except Exception:
                    errors += 1
                    logger.exception('Could not process one detected face; no frame data logged')
            elapsed = round((time.perf_counter()-start)*1000, 2)
            timings['matching_ms']=(time.perf_counter()-match_start)*1000
            with self._state_guard:
                self.inference_count += 1
                self.last_processing_ms = elapsed
            return dict(faces=public_faces, detected_faces=len(faces), face_errors=errors,
                        frame=dict(width=width, height=height), processing_ms=elapsed,
                        profile=profile, experimental=PROFILES.get(profile,{}).get('experimental',False),timings_ms=timings,
                        threshold_enabled=bool(self.matcher.policy and self.matcher.policy.get('enabled')) and not PROFILES.get(profile,{}).get('experimental',False),
                        identity_confirmation_enabled=False, attendance_enabled=False)
        finally:
            self._guard.release()
