"""Measure HTTP components and serialized FPS against actual localhost model."""
import json
from pathlib import Path
import re
import sys
import time

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from benchmark_camera_optimization import frame

URL='http://127.0.0.1:5055'


def run():
    deadline=time.monotonic()+180
    while True:
        health=requests.get(URL+'/api/health',timeout=5).json()
        assert health['application']=='face_recognition_camera_v1'
        if health['ready']:break
        if health['state']=='error' or time.monotonic()>deadline:raise RuntimeError(str(health))
        time.sleep(2)
    html=requests.get(URL+'/',timeout=5).text
    config=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',html).group(1))
    headers={'X-Camera-Token':config['csrfToken']}
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    labels=sorted({r['student_id'] for r in gallery})
    a=Path(next(r for r in gallery if r['student_id']==labels[0])['image_path'])
    b=Path(next(r for r in gallery if r['student_id']==labels[1])['image_path'])
    records=[]
    sequence_fps=None
    for profile in ('yunet_recall_experimental','retinaface_original','retinaface_reuse'):
        response=requests.post(URL+'/api/camera/sessions',headers=headers,json={'profile':profile},timeout=5)
        response.raise_for_status();sid=response.json()['session_id']
        request_headers=dict(headers,**{'Content-Type':'image/jpeg','X-Camera-Session':sid})
        cases=[('one_A',a),('one_B',b),('one_A_again',a),('two',ROOT/'DATASET/Test/Group10.jpg'),('none',None)] if profile.startswith('yunet') else [('one_A',a)]
        sequence_start=time.perf_counter()
        count=0
        try:
            for case,path in cases:
                start=time.perf_counter()
                response=requests.post(URL+'/api/recognize',data=frame(path,(640,480)),headers=request_headers,timeout=120)
                elapsed=(time.perf_counter()-start)*1000
                response.raise_for_status();result=response.json()
                timings={}
                for part in response.headers['Server-Timing'].split(','):
                    name,value=part.strip().split(';dur=');timings[name]=float(value)
                assert not result['identity_confirmation_enabled'] and not result['attendance_enabled']
                assert result['profile']==profile
                assert all(not r['identity_confirmed'] for r in result['faces'])
                assert not any('embedding' in face or 'image_path' in face for face in result['faces'])
                expected=0 if case=='none' else 2 if case=='two' else 1
                assert len(result['faces'])==expected,(case,result)
                row=dict(profile=profile,case=case,http_roundtrip_ms=elapsed,
                    http_transport_and_client_ms=max(0.,elapsed-timings['server_total_ms']),
                    timings_ms=timings,faces=len(result['faces']),
                    candidates=[f['top1']['candidate'] for f in result['faces']],identity_confirmed=False)
                records.append(row);count+=1
                print(json.dumps(row,ensure_ascii=False),flush=True)
                time.sleep(.15)
                if count==3 and profile.startswith('yunet'):
                    sequence_fps=count/(time.perf_counter()-sequence_start)
        finally:
            requests.delete(URL+'/api/camera/sessions/'+sid,headers=headers,timeout=5)
    assert records[0]['candidates']!=records[1]['candidates']
    health=requests.get(URL+'/api/health',timeout=5).json()
    assert health['model_load_count']==1 and not health['busy']
    result=dict(kind='actual_http_model_qa',records=records,
        serialized_three_single_face_fps=sequence_fps,scheduled_gap_ms=150,
        camera_images_saved=False,model_load_count=1,startup_ms=health['startup_ms'],arcface_warmup_ms=health['arcface_warmup_ms'])
    (ROOT/'outputs/camera_optimization/http_qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: stage headers, decode/serialization/transport timing, actual sequential FPS, model once, and no identities confirmed.')


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');run()
