"""Real ArcFace integration in disposable SQLite, then serve the real empty DB.

Never writes student test records into outputs/management.sqlite3. Run while the
previous owned server is stopped, avoiding two TensorFlow processes on this laptop.
"""
from io import BytesIO
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import time

import numpy as np
from PIL import Image,ImageOps

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.database.db import ManagementStore
from backend.face_recognition.runtime_service import RecognitionEngine,file_hash


def run():
    report=dict(kind='real_models_disposable_database',checks={},physical_camera_tested=False)
    hashes=json.loads((ROOT/'.venv/phase1_originals.json').read_text(encoding='utf-8'))
    engine=RecognitionEngine()
    genuine=ManagementStore(ROOT/'outputs/management.sqlite3',ROOT/'outputs/face_embeddings.json')
    genuine_counts=genuine.overview()
    temp=tempfile.TemporaryDirectory(prefix='phase1-real-ai-',dir=ROOT/'.venv')
    try:
        db=Path(temp.name)/'test.sqlite3'
        app=create_app(engine,overrides={'TESTING':True,'DATABASE_PATH':db,'LEGACY_PHOTO_UPLOAD_ENABLED':True})
        deadline=time.monotonic()+180
        while engine.state not in ('ready','error') and time.monotonic()<deadline:time.sleep(1)
        assert engine.state=='ready',engine.state
        report['startup_ms']=engine.startup_ms
        client=app.test_client()
        html=client.get('/overview').get_data(as_text=True)
        token=json.loads(re.search(r'<script id="management-config" type="application/json">(.*?)</script>',html).group(1))['csrfToken']
        headers={'X-Camera-Token':token}
        def post(url,data):return client.post(url,json=data,headers=headers)
        student=post('/api/students',dict(student_code='QA-ISOLATED',full_name='Hồ sơ kiểm thử tách biệt')).json
        sid=student['id']
        gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
        source=Path(gallery[0]['image_path'])
        raw=source.read_bytes()
        def upload(data):
            start=time.perf_counter()
            response=client.post(f'/api/students/{sid}/faces',headers=headers,
                data=dict(image=(BytesIO(data),'upload.jpg'),confirmed='true'),content_type='multipart/form-data')
            return response,round((time.perf_counter()-start)*1000,2)
        response,elapsed=upload(raw)
        assert response.status_code==201,response.json
        report['valid_upload_ms']=elapsed
        report['checks']['real_retinaface_single_face_arcface_registration']=True
        store=app.extensions['management_store']
        records,_=store.gallery_snapshot()
        assert len(records)==93 and len(engine.matcher.records)==93
        vector=np.asarray(records[-1]['embedding'])
        with Image.open(BytesIO(raw)) as image:bgr=np.ascontiguousarray(np.asarray(ImageOps.exif_transpose(image).convert('RGB'))[:,:,::-1])
        original=np.asarray(engine.model.represent(img_path=bgr,**engine.settings)[0]['embedding'])
        cosine=float(vector@original/(np.linalg.norm(vector)*np.linalg.norm(original)))
        assert cosine>.999999
        report['original_enrollment_embedding_cosine']=cosine
        report['checks']['same_preprocessing_as_original_represent']=True
        original_label=gallery[0]['student_id']
        mapped=post(f'/api/students/{sid}/legacy-mapping',dict(dataset_label=original_label,confirmed=True))
        assert mapped.status_code==201,mapped.json
        assert len(engine.matcher.records)==93
        report['checks']['manual_mapping_relabels_without_duplicating_92_vectors']=True
        # Camera API must immediately see the added/mapped identity, with no identity confirmation.
        response=client.post('/api/camera/sessions',headers=headers,json={'profile':'retinaface_reuse'})
        session=response.json['session_id']
        with Image.open(BytesIO(raw)) as image:
            image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((640,480))
            data=BytesIO();image.save(data,format='JPEG',quality=95)
        response=client.post('/api/recognize',headers=dict(headers,**{'X-Camera-Session':session}),data=data.getvalue(),content_type='image/jpeg')
        assert response.status_code==200,response.json
        assert len(response.json['faces'])==1,response.json
        candidate=response.json['faces'][0]['top1']['candidate']
        assert candidate.startswith('QA-ISOLATED'),candidate
        assert not response.json['faces'][0]['identity_confirmed'] and not response.json['attendance_enabled']
        client.delete('/api/camera/sessions/'+session,headers=headers)
        report['checks']['existing_camera_api_sees_new_gallery_unconfirmed']=True
        blank=BytesIO();Image.new('RGB',(320,240),'#263544').save(blank,format='JPEG')
        response,elapsed=upload(blank.getvalue())
        assert response.status_code==422 and 'khuôn mặt' in response.json['error']['message'],response.json
        report['no_face_ms']=elapsed
        report['checks']['real_no_face_rejected']=True
        response,elapsed=upload((ROOT/'DATASET/Test/Group10.jpg').read_bytes())
        assert response.status_code==422 and 'nhiều' in response.json['error']['message'],response.json
        report['multiple_face_ms']=elapsed
        report['checks']['real_multiple_faces_rejected']=True
        assert len(store.gallery_snapshot()[0])==93
        # A new app and connection reconstruct the exact persisted new vector.
        restarted=create_app(engine,start_models=False,overrides={'TESTING':True,'DATABASE_PATH':db})
        assert restarted.test_client().get(f'/api/students/{sid}').json['face_count']==sum(r['student_id']==original_label for r in gallery)+1
        assert restarted.extensions['management_store'].gallery_snapshot()[0][-1]['embedding']==vector.tolist()
        report['checks']['new_app_reopens_persistent_database']=True
        assert engine.model_load_count==1
        report['checks']['no_model_reload']=True
        assert all(file_hash(ROOT/p)==digest for p,digest in hashes.items())
        report['checks']['all_original_files_unchanged']=True
        report['original_files_verified']=len(hashes)
        if os.environ.get('PHASE1_ATTENDANCE_QA')=='1':
            from tests.phase2_real_e2e import check
            report['attendance_e2e']=check(restarted,engine)
        report['status']='passed'
    except Exception as exc:
        report['status']='failed';report['failure']=str(exc)
        raise
    finally:
        output=Path(os.environ.get('PHASE1_TEST_REPORT_DIR',str(ROOT/'outputs/phase1')));output.mkdir(parents=True,exist_ok=True)
        (output/'real_ai_tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.cleanup()
    # Switch back to the genuine database before accepting any web request.
    production=create_app(engine,start_models=False)
    engine.refresh_gallery()
    assert production.extensions['management_store'].overview()==genuine_counts
    assert len(engine.matcher.records)==len(genuine.gallery_snapshot()[0])
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    print('QA complete; serving genuine SQLite at http://127.0.0.1:5055',flush=True)
    production.run(host='127.0.0.1',port=5055,debug=False,use_reloader=False,threaded=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');logging.basicConfig(level=logging.INFO);run()
