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
from .verification_policy import VerificationPolicy, POLICY_PATH

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ROOT / 'outputs'
SETTING_KEYS = ('model_name', 'detector_backend', 'align', 'normalization',
                'enforce_detection', 'expand_percentage', 'anti_spoofing', 'max_faces')
logger = logging.getLogger(__name__)


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
        self.verification_policy=VerificationPolicy.load(POLICY_PATH)
        self.verification_policy_path=POLICY_PATH

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
        # Gallery label -> numeric SQLite student id. Labels are built from the
        # UNIQUE student_code by the same rule as ManagementStore._snapshot; the
        # class/enrollment decision downstream uses only the numeric id.
        identities={}
        for s in self.management_store.list_entities('students'):
            label=s['student_code']+' · '+s['full_name']
            if label in identities:identities[label]=None  # ambiguous -> never an identity
            else:identities[label]=dict(id=s['id'],student_code=s['student_code'],full_name=s['full_name'],code_is_temporary=bool(s['student_code_is_temporary']))
        identities={k:v for k,v in identities.items() if v is not None}
        with self._state_guard:
            self.matcher=matcher
            self._gallery_revision=revision
            self._public_identities=identities

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

    def refresh_gallery(self,wait=0):
        """Reload the SQLite gallery into the matcher. wait>0 blocks up to wait seconds.

        Returns True only when the matcher now serves the store's current revision.
        """
        if self.state!='ready':
            return False
        if not (self._guard.acquire(timeout=wait) if wait>0 else self._guard.acquire(blocking=False)):
            return False
        try:
            self._refresh_gallery_locked()
            return self.management_store is None or self._gallery_revision==self.management_store.gallery_revision()
        finally:
            self._guard.release()

    def validate_gallery(self,records):
        """Raise if a would-be gallery cannot be served (checked before SQLite COMMIT)."""
        FaceMatcher(records,self._gallery_config,'pre-commit-validation',None)

    def loaded_gallery_revision(self):
        with self._state_guard:
            return self._gallery_revision

    def gallery_records(self):
        """Current in-memory gallery (labels + vectors) for enrollment identity checks."""
        with self._state_guard:
            return list(self.matcher.records) if self.matcher is not None else []

    def enrollment_detect(self,bgr,score_threshold=.6):
        """YuNet boxes + 5 landmarks; separate lock from ArcFace so feedback stays live."""
        if self.state!='ready': raise EngineUnavailable()
        return self.pipeline.yunet.detect_landmarks(bgr,dimension=640,score_threshold=score_threshold)

    def enrollment_embedding(self,bgr,box,collection,cancelled=lambda:False):
        """Gallery-compatible ArcFace vector for the single face inside box.

        Runs the unchanged retinaface_reuse code path (RetinaFace detection,
        DeepFace eye alignment, ArcFace forward, normalization=base) on a
        face-centred crop instead of the whole frame: same model and alignment,
        far fewer detector pixels. The crop lives only in RAM.
        """
        import cv2
        if self.state!='ready': raise EngineUnavailable()
        if not self._guard.acquire(timeout=collection['embedding_lock_wait_s']): raise EngineBusy()
        try:
            start=time.perf_counter()
            height,width=bgr.shape[:2]
            m=collection['crop_margin_ratio']
            cx,cy=box['x']+box['w']/2,box['y']+box['h']/2
            half=max(box['w'],box['h'])*(0.5+m)
            x0,y0,x1,y1=int(cx-half),int(cy-half),int(cx+half),int(cy+half)
            crop=cv2.copyMakeBorder(bgr[max(0,y0):min(height,y1),max(0,x0):min(width,x1)],
                max(0,-y0),max(0,y1-height),max(0,-x0),max(0,x1-width),cv2.BORDER_CONSTANT,value=[0,0,0])
            limit=collection.get('crop_max_dimension')
            if limit and max(crop.shape[:2])>limit:
                s=limit/max(crop.shape[:2])
                crop=cv2.resize(crop,(round(crop.shape[1]*s),round(crop.shape[0]*s)),interpolation=cv2.INTER_AREA)
            try:
                faces,timings=self.pipeline.run(crop,'retinaface_reuse',cancelled)
            except InterruptedError:
                raise SessionStopped()
            if len(faces)!=1:
                raise ValueError('NO_FACE' if not faces else 'MULTIPLE_FACES')
            vector=np.asarray(faces[0]['embedding'],dtype=float)
            if vector.shape!=(512,) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0:
                raise ValueError('INVALID_EMBEDDING')
            with self._state_guard:
                self.inference_count+=1
            timings['total_ms']=(time.perf_counter()-start)*1000
            timings['crop_size']=list(crop.shape[:2])
            return vector/np.linalg.norm(vector),timings
        finally:
            self._guard.release()

    def enroll(self,bgr):
        """Original RetinaFace/ArcFace enrollment, one aligned face, no raw image persistence."""
        if self.state!='ready': raise EngineUnavailable()
        if not self._guard.acquire(blocking=False): raise EngineBusy()
        try:
            try:
                faces=self.model.extract_faces(img_path=bgr,detector_backend=self.settings['detector_backend'],
                    enforce_detection=True,align=self.settings['align'],expand_percentage=0,
                    color_face='rgb',normalize_face=True,anti_spoofing=False)
            except ValueError as exc:
                if 'Face could not be detected' in str(exc): raise ValueError('NO_FACE') from exc
                raise
            if not faces: raise ValueError('NO_FACE')
            if len(faces)!=1: raise ValueError('MULTIPLE_FACES')
            from deepface.modules import preprocessing
            image=faces[0]['face'][:,:,::-1]
            tensor=preprocessing.resize_image(image,target_size=(self.pipeline.model.input_shape[1],self.pipeline.model.input_shape[0]))
            tensor=preprocessing.normalize_input(tensor,normalization=self.settings['normalization'])
            vector=np.asarray(self.pipeline.model.forward(tensor),dtype=float)
            if vector.shape!=(512,) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0:
                raise ValueError('INVALID_EMBEDDING')
            return vector.tolist(),dict(model_name='ArcFace',detector_backend='retinaface',align=True,
                normalization='base',embedding_dimension=512,enforce_detection=True,expand_percentage=0,
                anti_spoofing=False,attribution='manually_confirmed_by_operator_not_biometrically_verified')
        finally:
            self._guard.release()

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
            self.verification_policy=VerificationPolicy.load(self.verification_policy_path,config)
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
                        threshold_enabled=bool(self.verification_policy.valid),
                        gallery_signature=self.matcher.signature if self.matcher is not None else None,
                        identity_confirmation_enabled=False, attendance_enabled=False,
                        model_load_count=self.model_load_count, inference_count=self.inference_count,
                        startup_ms=self.startup_ms, profiles=PROFILES,
                        arcface_warmup_ms=self.arcface_warmup_ms,
                        retina_and_preview_warmup_ms=self.retina_warmup_ms,
                        preview_detector='YuNet · vị trí khuôn mặt, không nhận diện',
                        busy=self._guard.locked(), last_processing_ms=self.last_processing_ms)

    def analyze(self, bgr, cancelled=lambda: False, profile='retinaface_reuse',allowed_student_ids=None):
        if self.state != 'ready':
            raise EngineUnavailable()
        if not self._guard.acquire(blocking=False):
            raise EngineBusy()
        start = time.perf_counter()
        try:
            self._refresh_gallery_locked()
            if cancelled():
                raise SessionStopped()
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
                    match = self.matcher.match(face['embedding'])
                    # GLOBAL top-1/top-2 over every registered student. Never restricted to
                    # the class roster: an outsider must not be pulled onto a classmate.
                    person=self._public_identities.get(match['best_candidate'])
                    verified,reasons=self.verification_policy.verify(match['best_similarity'],match['margin'],profile)
                    if not person:
                        verified=False;reasons=reasons+['candidate_not_mapped_to_student_id']
                    outside=bool(allowed_student_ids is not None and (not person or person['id'] not in allowed_student_ids))
                    # Internal record. HTTP routes MUST pass it through
                    # attendance.classification.public_face before returning it.
                    public_faces.append(dict(face_id=len(public_faces) + 1,
                        box=dict(x=x, y=y, w=right-x, h=bottom-y),
                        top1=dict(candidate=match['best_candidate'], similarity=match['best_similarity'],student=person),
                        top2=dict(candidate=match['second_candidate'], similarity=match['second_similarity']),
                        margin=match['margin'], verified=verified, verification_reasons=reasons,
                        candidate_student_id=person['id'] if person else None,
                        decision='verified' if verified else 'rejected',
                        eligible_for_confirmation=bool(verified and not outside),
                        outside_class=outside,
                        identity_confirmed=verified, label='Đã xác minh' if verified else 'Không xác định',
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
                        threshold_enabled=self.verification_policy.valid and not self.verification_policy.profile_reasons(profile),
                        identity_confirmation_enabled=False, attendance_enabled=False)
        finally:
            self._guard.release()
