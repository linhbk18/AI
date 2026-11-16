"""Measured inference profiles; gallery settings/weights are never rewritten.

retinaface_reuse reproduces DeepFace 0.0.93 crop/align/preprocessing, detects once
and calls the already-loaded ArcFace forward method. No 'skip' on raw frames.
yunet_experimental changes only detection/landmarks and requires new evaluation.
"""
import time
import threading
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROFILES=json.loads((Path(__file__).resolve().parents[1]/'camera_profiles.json').read_text(encoding='utf-8'))['profiles']


class CameraFace:
    """YuNet landmarks used for preview and transient enrollment quality checks."""
    def __init__(self,x,y,w,h,right_eye,left_eye,nose_tip,right_mouth,left_mouth,confidence):
        self.x,self.y,self.w,self.h=x,y,w,h
        self.right_eye,self.left_eye=right_eye,left_eye
        self.nose_tip,self.right_mouth,self.left_mouth=nose_tip,right_mouth,left_mouth
        self.confidence=confidence


class YuNetDetector:
    """OpenCV YuNet 2023mar: one detection call on the unpadded camera frame."""
    def __init__(self, path):
        import cv2
        self.model = cv2.FaceDetectorYN.create(str(path), '', (320,320), .9, .3, 5000)
        self._guard=threading.Lock()

    def detect_faces(self, image,dimension=640,score_threshold=.9):
        # OpenCV's input size/score settings are mutable; preview and experimental
        # recognition must never mutate the same cached detector concurrently.
        with self._guard:
            return self._detect_faces(image,dimension,score_threshold)

    def _detect_faces(self,image,dimension,score_threshold):
        import cv2
        height,width=image.shape[:2]
        scale=dimension/max(height,width)
        size=(max(1,round(width*scale)),max(1,round(height*scale)))
        resized=cv2.resize(image,size) if size!=(width,height) else image
        self.model.setInputSize(size)
        self.model.setScoreThreshold(score_threshold)
        _,faces=self.model.detect(resized)
        result=[]
        if faces is None: return result
        sx,sy=size[0]/width,size[1]/height
        for face in faces:
            point=lambda i:(int(face[i]/sx),int(face[i+1]/sy))
            result.append(CameraFace(x=int(face[0]/sx),y=int(face[1]/sy),w=int(face[2]/sx),h=int(face[3]/sy),
                right_eye=point(4),left_eye=point(6),nose_tip=point(8),right_mouth=point(10),left_mouth=point(12),confidence=float(face[-1])))
        return result


class CameraPipeline:
    def __init__(self, model, retina_detector, yunet_detector, settings, deepface):
        self.model=model
        self.retina=retina_detector
        self.yunet=yunet_detector
        self.settings=settings
        self.deepface=deepface
        self.graph_forward=None

    def prepare_graph(self):
        """Same pretrained ArcFace operations, traced once; no detector change."""
        import tensorflow as tf
        model=self.model.model
        self.graph_forward=tf.function(lambda tensor:model(tensor,training=False),
            input_signature=[tf.TensorSpec((1,112,112,3),tf.float32)],autograph=False)
        self.graph_forward(tf.zeros((1,112,112,3),tf.float32))

    def forward(self,tensor):
        if self.graph_forward is None:return self.model.forward(tensor)
        import tensorflow as tf
        return self.graph_forward(tf.convert_to_tensor(tensor,dtype=tf.float32)).numpy()[0].tolist()

    def native_retina_faces(self,image,max_dimension=None):
        import cv2
        from retinaface import RetinaFace as rf
        from deepface.models.Detector import FacialAreaRegion
        scale=1.
        if max_dimension and max(image.shape[:2])>max_dimension:
            scale=max_dimension/max(image.shape[:2])
            image=cv2.resize(image,(round(image.shape[1]*scale),round(image.shape[0]*scale)),interpolation=cv2.INTER_AREA)
        detections=rf.detect_faces(image,model=self.retina.model,threshold=.9,allow_upscaling=False)
        result=[]
        if not isinstance(detections,dict): return result
        for face in detections.values():
            x,y,right,bottom=face['facial_area']
            eye_coordinate=lambda v:int(v) if scale==1 else round(v/scale)
            result.append(FacialAreaRegion(x=round(x/scale),y=round(y/scale),w=round((right-x)/scale),h=round((bottom-y)/scale),
                left_eye=tuple(eye_coordinate(v) for v in face['landmarks']['left_eye']),
                right_eye=tuple(eye_coordinate(v) for v in face['landmarks']['right_eye']),confidence=face['score']))
        return result

    def baseline(self,bgr,cancelled=lambda:False):
        """Instrument the original represent(), without editing installed libraries."""
        from deepface.modules import detection
        timings=dict(detection_ms=0.,alignment_ms=0.,embedding_ms=0.,arcface_forward_ms=0.)
        original_detect=self.retina.detect_faces
        original_extract=detection.extract_faces
        original_forward=self.model.forward
        extraction=0.
        def detect(*args,**kwargs):
            start=time.perf_counter()
            try: return original_detect(*args,**kwargs)
            finally: timings['detection_ms']+=(time.perf_counter()-start)*1000
        def extract(*args,**kwargs):
            nonlocal extraction
            start=time.perf_counter()
            try: return original_extract(*args,**kwargs)
            finally: extraction+=(time.perf_counter()-start)*1000
        def forward(*args,**kwargs):
            start=time.perf_counter()
            try: return original_forward(*args,**kwargs)
            finally: timings['arcface_forward_ms']+=(time.perf_counter()-start)*1000
        start=time.perf_counter()
        with patch.object(self.retina,'detect_faces',detect),patch.object(detection,'extract_faces',extract),patch.object(self.model,'forward',forward):
            try: faces=self.deepface.represent(img_path=bgr,**self.settings)
            except ValueError as exc:
                if 'Face could not be detected' in str(exc): faces=[]
                else: raise
        total=(time.perf_counter()-start)*1000
        timings['alignment_ms']=max(0.,extraction-timings['detection_ms'])
        timings['embedding_ms']=max(0.,total-extraction)
        return faces,timings

    def run(self,bgr,profile='retinaface_reuse',cancelled=lambda:False,diagnostic=None):
        if profile=='retinaface_original': return self.baseline(bgr,cancelled)
        if profile not in PROFILES: raise ValueError('Unknown camera inference profile')
        import cv2
        from deepface.modules import detection,preprocessing
        fast=profile.startswith('yunet')
        native=profile in ('retinaface_native_experimental','retinaface_scaled_experimental')
        detector=self.yunet if fast else self.retina
        timings=dict(detection_ms=0.,alignment_ms=0.,embedding_ms=0.,arcface_forward_ms=0.)
        height,width=bgr.shape[:2]
        hb,wb=int(.5*height),int(.5*width)
        start=time.perf_counter()
        padded=cv2.copyMakeBorder(bgr,hb,hb,wb,wb,cv2.BORDER_CONSTANT,value=[0,0,0])
        timings['alignment_ms']+=(time.perf_counter()-start)*1000
        start=time.perf_counter()
        if fast:
            areas=detector.detect_faces(bgr,dimension=PROFILES[profile]['detector_input_dimension'],score_threshold=PROFILES[profile]['detector_confidence'])
        elif native:
            areas=self.native_retina_faces(bgr,PROFILES[profile].get('detector_max_dimension'))
        else:
            areas=detector.detect_faces(padded)
        timings['detection_ms']=(time.perf_counter()-start)*1000
        if cancelled(): raise InterruptedError('Camera session stopped')
        results=[]
        for area in areas:
            if cancelled(): raise InterruptedError('Camera session stopped')
            start=time.perf_counter()
            x,y,w,h=area.x,area.y,area.w,area.h
            le,re=area.left_eye,area.right_eye
            if fast or native:
                x,y=x+wb,y+hb
                le=(le[0]+wb,le[1]+hb) if le else None
                re=(re[0]+wb,re[1]+hb) if re else None
            aligned,angle=detection.align_img_wrt_eyes(padded,le,re)
            x1,y1,x2,y2=detection.project_facial_area((x,y,x+w,y+h),angle,padded.shape[:2])
            crop=aligned[int(y1):int(y2),int(x1):int(x2)]
            if crop.size==0: continue
            # Same RGB-normalized extraction followed by RGB->BGR as represent().
            # Algebraically crop(BGR)/255 avoids the two redundant channel reversals.
            normalized=crop/255
            region=dict(x=max(0,int(x-wb)),y=max(0,int(y-hb)))
            region.update(w=min(width-region['x']-1,int(w)),h=min(height-region['y']-1,int(h)))
            timings['alignment_ms']+=(time.perf_counter()-start)*1000
            start=time.perf_counter()
            tensor=preprocessing.resize_image(normalized,target_size=(self.model.input_shape[1],self.model.input_shape[0]))
            tensor=preprocessing.normalize_input(tensor,normalization=self.settings['normalization'])
            forward_start=time.perf_counter()
            embedding=self.forward(tensor)
            if diagnostic is not None:
                diagnostic(dict(crop=crop.copy(),tensor=tensor.copy(),angle=float(angle),left_eye=le,right_eye=re,
                    facial_area=dict(region),embedding=embedding))
            timings['arcface_forward_ms']+=(time.perf_counter()-forward_start)*1000
            timings['embedding_ms']+=(time.perf_counter()-start)*1000
            results.append(dict(embedding=embedding,facial_area=region,face_confidence=round(area.confidence,2)))
        return results,timings
