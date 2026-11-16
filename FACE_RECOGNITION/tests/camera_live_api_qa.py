"""Actual ArcFace/RetinaFace HTTP smoke tests; no webcam image is written."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import time

from PIL import Image, ImageOps
import requests

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:5055'


def payload(path=None):
    if path is None:
        image=Image.new('RGB',(640,360),'#101827')
    else:
        with Image.open(path) as source:
            image=ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((640,640),Image.Resampling.LANCZOS)
    output=BytesIO()
    size=image.size
    image.save(output,format='JPEG',quality=85)
    image.close()
    return output.getvalue(),size


def run():
    deadline=time.monotonic()+180
    while True:
        health=requests.get(URL+'/api/health',timeout=5).json()
        assert health['application']=='face_recognition_camera_v1'
        if health['ready']: break
        if health['state']=='error' or time.monotonic()>deadline:
            raise RuntimeError(f'Model not ready: {health}')
        print('Waiting for model:',health['state'],flush=True)
        time.sleep(3)
    html=requests.get(URL+'/',timeout=5).text
    cfg=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',html).group(1))
    headers={'X-Camera-Token':cfg['csrfToken']}
    session=requests.post(URL+'/api/camera/sessions',headers=headers,timeout=5)
    session.raise_for_status()
    sid=session.json()['session_id']
    frame_headers=dict(headers,**{'Content-Type':'image/jpeg','X-Camera-Session':sid})
    # Derive private labels/paths at runtime; do not put student names into QA source.
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    identities=sorted({r['student_id'] for r in gallery})
    first=next(r for r in gallery if r['student_id']==identities[0])
    second_samples=[r for r in gallery if r['student_id']==identities[1]]
    second=second_samples[min(1,len(second_samples)-1)]
    cases=[('single_person_A',Path(first['image_path']),1),
           ('single_person_B',Path(second['image_path']),1),
           ('multiple_faces',ROOT/'DATASET/Test/Group10.jpg',2),('no_face',None,0)]
    outcomes=[]
    try:
        for name,path,expected in cases:
            data,size=payload(path)
            start=time.perf_counter()
            response=requests.post(URL+'/api/recognize',data=data,headers=frame_headers,timeout=120)
            elapsed=(time.perf_counter()-start)*1000
            response.raise_for_status()
            result=response.json()
            assert result['frame']==dict(width=size[0],height=size[1])
            assert len(result['faces'])==expected,(name,result)
            assert result['face_errors']==0
            assert not result['identity_confirmation_enabled'] and not result['attendance_enabled']
            for face in result['faces']:
                assert face['identity_confirmed'] is False
                assert face['label']=='Ứng viên chưa xác nhận'
                assert face['top1']['candidate']!=face['top2']['candidate']
                assert -1<=face['top2']['similarity']<=face['top1']['similarity']<=1
                assert not any(key in face for key in ('embedding','source','image_path','nearest_registered_source'))
                box=face['box']
                assert 0<=box['x']<box['x']+box['w']<=size[0]
                assert 0<=box['y']<box['y']+box['h']<=size[1]
            if expected==0: assert result['faces']==[]
            record=dict(case=name,status='passed',input_kind='existing_dataset_image_in_memory' if path else 'synthetic_blank',
                        frame_size=list(size),faces=expected,candidates=[f['top1']['candidate'] for f in result['faces']],
                        processing_ms=result['processing_ms'],http_elapsed_ms=round(elapsed,2),
                        identity_confirmation=False,attendance_written=False)
            outcomes.append(record)
            print(json.dumps(record,ensure_ascii=False),flush=True)
        assert outcomes[0]['candidates']!=outcomes[1]['candidates']
        assert outcomes[0]['candidates']==[identities[0]]
        assert outcomes[1]['candidates']==[identities[1]]
    finally:
        requests.delete(URL+'/api/camera/sessions/'+sid,headers=headers,timeout=5)

    # Actual inference concurrency and stop: no new inference is queued and no
    # result is returned for a session that is stopped during a native TF call.
    sid=requests.post(URL+'/api/camera/sessions',headers=headers,timeout=5).json()['session_id']
    frame_headers['X-Camera-Session']=sid
    data,_=payload(cases[0][1])
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(requests.post,URL+'/api/recognize',data=data,headers=frame_headers,timeout=120)
        until=time.monotonic()+10
        while not requests.get(URL+'/api/health',timeout=5).json()['busy']:
            if time.monotonic()>until: raise RuntimeError('Inference did not start')
            time.sleep(.1)
        second=requests.post(URL+'/api/recognize',data=data,headers=frame_headers,timeout=5)
        assert second.status_code==429 and second.json()['error']['code']=='ENGINE_BUSY'
        stopped=requests.delete(URL+'/api/camera/sessions/'+sid,headers=headers,timeout=5)
        assert stopped.status_code==200
        late=pending.result(timeout=120)
        assert late.status_code==409 and late.json()['error']['code']=='SESSION_STOPPED'
        assert 'faces' not in late.json()
    health=requests.get(URL+'/api/health',timeout=5).json()
    assert health['model_load_count']==1
    assert not health['attendance_enabled'] and not health['identity_confirmation_enabled']
    assert requests.get(URL+'/outputs/face_embeddings.json',timeout=5).status_code==404
    report=dict(kind='actual_local_http_inference',url=URL,cases=outcomes,
                different_person_changes_candidate=True,global_single_inference=True,
                stopped_session_suppresses_late_result=True,model_load_count=health['model_load_count'],
                no_public_embedding_route=True,no_attendance=True,
                limitations=['Dataset images sent as HTTP test frames, not staged live-camera people.',
                             'A running TensorFlow native call finishes before its cancelled result can be discarded.'])
    (ROOT/'.venv/camera_integration_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: real model HTTP cases, single-flight inference, cancellation, one-time model loading, and no attendance.',flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    run()
