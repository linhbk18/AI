"""Evaluate RetinaFace native-size preprocessing via actual HTTP, all 25 labels."""
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
OUT=ROOT/'outputs/camera_optimization'


def run():
    while not requests.get(URL+'/api/health',timeout=5).json()['ready']:time.sleep(2)
    html=requests.get(URL+'/',timeout=5).text
    token=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',html).group(1))['csrfToken']
    headers={'X-Camera-Token':token}
    response=requests.post(URL+'/api/camera/sessions',headers=headers,json={'profile':'retinaface_native_experimental'},timeout=5)
    response.raise_for_status();sid=response.json()['session_id']
    post_headers=dict(headers,**{'Content-Type':'image/jpeg','X-Camera-Session':sid})
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    student=sorted({r['student_id'] for r in gallery})[0]
    first=Path(next(r for r in gallery if r['student_id']==student)['image_path'])
    cases=dict(one_face=first,two_faces=ROOT/'DATASET/Test/Group10.jpg',no_face=None,many_faces=ROOT/'DATASET/Test/Group01.jpg')
    bench,validation=[],[]
    try:
        for size in ((640,480),(640,360),(320,240)):
            def request_frame(path):
                data=frame(path,size)
                start=time.perf_counter()
                response=requests.post(URL+'/api/recognize',headers=post_headers,data=data,timeout=120)
                wall=(time.perf_counter()-start)*1000
                response.raise_for_status()
                result=response.json()
                assert not result['identity_confirmation_enabled'] and not result['attendance_enabled']
                return result,wall
            for case,path in cases.items():
                for repeat in range(3):
                    result,wall=request_frame(path)
                    times=result['timings_ms']
                    row=dict(profile='retinaface_native_experimental',case=case,width=size[0],height=size[1],repeat=repeat,
                        **times,total_ms=wall,faces=len(result['faces']),candidates=[f['top1']['candidate'] for f in result['faces']],
                        http_inclusive=True,warmup_sample=repeat==0)
                    bench.append(row)
                    (OUT/'benchmark_native_http.json').write_text(json.dumps(bench,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(f"NATIVE {size} {case} r{repeat} faces={row['faces']} total={wall:.1f}ms detect={times['detection_ms']:.1f}ms",flush=True)
            for path in sorted((ROOT/'DATASET/validation').rglob('*')):
                if not path.is_file():continue
                result,wall=request_frame(path)
                faces=result['faces']
                prediction=faces[0]['top1']['candidate'] if len(faces)==1 else None
                row=dict(profile='retinaface_native_experimental',width=size[0],height=size[1],source=path.relative_to(ROOT).as_posix(),
                    true_student=path.parent.name,predicted_student=prediction,faces=len(faces),correct=prediction==path.parent.name,
                    elapsed_ms=wall,**result['timings_ms'])
                validation.append(row)
                (OUT/'validation_native_http.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
                print(f"NATIVE VALIDATION {size} {path.name} faces={len(faces)} correct={row['correct']}",flush=True)
    finally:requests.delete(URL+'/api/camera/sessions/'+sid,headers=headers,timeout=5)
    print('DONE native-size experiment; original gallery unchanged.',flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');run()
