"""Read-only data audit and one real camera-API image query after restart."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np
from PIL import Image,ImageOps
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.database.db import ManagementStore
from backend.database.import_legacy_students import audit_links,validate_gallery
from backend.face_recognition.face_service import FaceMatcher

URL='http://127.0.0.1:5055'


def run():
    gallery_path=ROOT/'outputs/face_embeddings.json'
    store=ManagementStore(ROOT/'outputs/management.sqlite3',gallery_path)
    config=json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8'))
    validate_gallery(store,config)
    with store.connection() as db:
        rows=audit_links(store,db)
        assert db.execute('SELECT count(*) FROM students WHERE deleted_at IS NULL').fetchone()[0]==5
        assert db.execute('SELECT count(*) FROM face_embeddings').fetchone()[0]==92
        assert db.execute('SELECT count(*) FROM class_students').fetchone()[0]==0
        assert not db.execute('SELECT 1 FROM students WHERE student_code=?',('SV1',)).fetchone()
        baseline=json.loads((ROOT/'.venv/student_import_database_before.json').read_text(encoding='utf-8'))
        for row in baseline['classes']:
            assert dict(db.execute('SELECT * FROM classes WHERE id=?',(row['id'],)).fetchone())==row
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    actual=requests.get(URL+'/api/students',timeout=10).json()['items']
    assert len(actual)==5
    for row in rows:
        api_row=next(r for r in actual if r['id']==row['student_id'])
        assert api_row['face_count']==row['legacy_embedding_count']
        assert api_row['student_code']==row['student_code'] and api_row['student_code_is_temporary']==1
    options=requests.get(URL+'/api/legacy-gallery',timeout=10).json()['items']
    assert len(options)==5 and sum(r['embedding_count'] for r in options)==92 and all(r['student_id'] for r in options)
    records,revision=store.gallery_snapshot()
    assert len(records)==92
    matcher=FaceMatcher(records,config,hashlib.sha256(gallery_path.read_bytes()).hexdigest(),None)
    similarity_checks=[]
    for row in rows:
        original=next(r for r in store.legacy if r['student_id']==row['dataset_label'])
        match=matcher.match(original['embedding'])
        expected=row['student_code']+' · '+row['full_name']
        assert match['best_candidate']==expected and not match['identity_confirmed']
        similarity_checks.append(dict(dataset_label=row['dataset_label'],runtime_candidate=match['best_candidate'],score=match['best_similarity']))
    health=requests.get(URL+'/api/health',timeout=5).json()
    assert health['ready'] and health['registered_embeddings']==92 and health['registered_students']==5 and health['model_load_count']==1
    html=requests.get(URL+'/',timeout=5).text
    token=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',html).group(1))['csrfToken']
    headers={'X-Camera-Token':token}
    response=requests.post(URL+'/api/camera/sessions',json={'profile':'retinaface_reuse'},headers=headers,timeout=5)
    response.raise_for_status();sid=response.json()['session_id']
    try:
        source=Path(store.legacy[0]['image_path'])
        with Image.open(source) as image:
            image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((640,480))
            stream=BytesIO();image.save(stream,format='JPEG',quality=95)
        response=requests.post(URL+'/api/recognize',data=stream.getvalue(),
            headers=dict(headers,**{'Content-Type':'image/jpeg','X-Camera-Session':sid}),timeout=150)
        response.raise_for_status();recognition=response.json()
        assert len(recognition['faces'])==1,recognition
        target=next(r for r in rows if r['dataset_label']==store.legacy[0]['student_id'])
        assert recognition['faces'][0]['top1']['candidate']==target['student_code']+' · '+target['full_name']
        assert not recognition['faces'][0]['identity_confirmed'] and not recognition['attendance_enabled']
    finally:requests.delete(URL+'/api/camera/sessions/'+sid,headers=headers,timeout=5)
    hashes=json.loads((ROOT/'.venv/student_import_originals.json').read_text(encoding='utf-8'))
    changed=[path for path,digest in hashes.items() if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest]
    assert not changed,changed
    result=dict(status='passed',students=rows,total_legacy_references=92,runtime_vector_count=len(records),
        runtime_gallery_score_checks=similarity_checks,gallery_revision=revision,original_files_verified=len(hashes),original_files_changed=changed,
        existing_classes_preserved=True,no_class_created_or_assigned=True,server_restarted_before_check=True,
        model_load_count=health['model_load_count'],real_http_recognition=recognition,physical_camera_tested=False)
    (ROOT/'outputs/student_import/final_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: five mapped profiles, 92 unique valid references, API/SQLite/runtime agree, class MIS preserved, originals unchanged.')
    print('PASS: one real ArcFace/RetinaFace HTTP image query after restart; candidate unconfirmed; no attendance.')


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');run()
