"""Warm before/after samples, separated from startup/first-request effects."""
import json
from pathlib import Path
import re
import sys
import time

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from benchmark_camera_optimization import frame

URL='http://127.0.0.1:5055'


def run():
    path=ROOT/'outputs/camera_optimization/http_qa.json'
    result=json.loads(path.read_text(encoding='utf-8'))
    html=requests.get(URL+'/',timeout=5).text
    cfg=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',html).group(1))
    headers={'X-Camera-Token':cfg['csrfToken']}
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    labels=sorted({r['student_id'] for r in gallery})
    sources=[Path(next(r for r in gallery if r['student_id']==label)['image_path']) for label in labels[:2]]
    payloads=[frame(p,(640,480)) for p in sources]
    rows=[]
    for profile in ('retinaface_original','retinaface_reuse','yunet_recall_experimental'):
        response=requests.post(URL+'/api/camera/sessions',headers=headers,json={'profile':profile},timeout=5)
        response.raise_for_status();sid=response.json()['session_id']
        post_headers=dict(headers,**{'Content-Type':'image/jpeg','X-Camera-Session':sid})
        start_sequence=time.perf_counter()
        try:
            for i in range(3):
                start=time.perf_counter()
                # Before/after RetinaFace use exactly the same one-face image.
                data=payloads[i%2] if profile.startswith('yunet') else payloads[0]
                response=requests.post(URL+'/api/recognize',headers=post_headers,data=data,timeout=120)
                wall=(time.perf_counter()-start)*1000
                response.raise_for_status();record=response.json()
                metrics={part.strip().split(';dur=')[0]:float(part.strip().split(';dur=')[1])
                         for part in response.headers['Server-Timing'].split(',')}
                assert len(record['faces'])==1 and not record['faces'][0]['identity_confirmed']
                row=dict(profile=profile,repeat=i,temperature='warm',http_roundtrip_ms=wall,timings_ms=metrics,
                    candidate=record['faces'][0]['top1']['candidate'],identity_confirmed=False)
                rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
                time.sleep(.15)
            if profile.startswith('yunet'):
                result['warm_serialized_three_single_face_fps']=3/(time.perf_counter()-start_sequence)
        finally:requests.delete(URL+'/api/camera/sessions/'+sid,headers=headers,timeout=5)
    result['warm_records']=rows
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Warm measured FPS:',result['warm_serialized_three_single_face_fps'])


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');run()
