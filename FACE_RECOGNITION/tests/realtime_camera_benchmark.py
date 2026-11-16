"""Local HTTP measurements; never saves camera images or changes student data."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

import numpy as np
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from benchmark_camera_optimization import frame

URL='http://127.0.0.1:5055'
OUT=ROOT/'outputs/realtime_camera_ui'


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def memory():
    class Memory(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(x,ctypes.c_ulonglong) for x in ('total','available','page_total','page_available','virtual','virtual_available','extended')]
    value=Memory();value.length=ctypes.sizeof(value);ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value))
    return dict(ram_total_gib=value.total/2**30,ram_available_gib=value.available/2**30,memory_load_percent=value.load)


def run(mode):
    OUT.mkdir(exist_ok=True)
    if mode!='before':
        for _ in range(180):
            health=requests.get(URL+'/api/health',timeout=5).json()
            if health['ready']:break
            if health['state']=='error':raise RuntimeError('Model failed to initialize')
            time.sleep(1)
    if mode=='before':
        protected={str(p.relative_to(ROOT)):sha(p) for folder in ('DATASET','outputs') for p in (ROOT/folder).rglob('*') if p.is_file() and 'realtime_camera_ui' not in p.parts and not p.name.startswith('management.sqlite3')}
        (ROOT/'.venv/realtime_ui_originals.json').write_text(json.dumps(protected),encoding='utf-8')
        with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:(ROOT/'.venv/realtime_db_before.sql').write_text('\n'.join(db.iterdump()),encoding='utf-8')
    html=requests.get(URL+'/').text
    token=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',html).group(1))['csrfToken']
    headers={'X-Camera-Token':token}
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    cases=dict(one_face=Path(gallery[0]['image_path']),two_faces=ROOT/'DATASET/Test/Group10.jpg',no_face=None,many_faces=ROOT/'DATASET/Test/Group01.jpg')
    result=dict(mode=mode,memory_before=memory(),health_before=requests.get(URL+'/api/health').json(),records=[],validation=[])
    profiles=('retinaface_reuse','retinaface_native_experimental') if mode=='before' else (('retinaface_scaled_experimental',) if mode=='scaled' else ('retinaface_native_experimental',))
    def save(): (OUT/(mode+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for profile in profiles:
        response=requests.post(URL+'/api/camera/sessions',headers=headers,json={'profile':profile});response.raise_for_status();sid=response.json()['session_id']
        ph=dict(headers,**{'Content-Type':'image/jpeg','X-Camera-Session':sid})
        def post(data,endpoint='/api/recognize'):
            start=time.perf_counter();response=requests.post(URL+endpoint,headers=ph,data=data,timeout=150);wall=(time.perf_counter()-start)*1000
            response.raise_for_status();payload=response.json()
            stages={x.strip().split(';dur=')[0]:float(x.strip().split(';dur=')[1]) for x in response.headers['Server-Timing'].split(',')}
            return payload,wall,stages
        try:
            for case,path in cases.items():
                if mode=='before' and case!='one_face':continue
                data=frame(path,(640,480))
                for repeat in range(3):
                    payload,wall,times=post(data)
                    record=dict(profile=profile,case=case,repeat=repeat,http_ms=wall,faces=payload['detected_faces'],timings_ms=times)
                    result['records'].append(record);save();print(json.dumps(record),flush=True)
            if mode!='before':
                # Independent, capped preview requests continue while ONE identity request runs.
                data=frame(cases['one_face'],(640,480));preview=frame(cases['one_face'],(320,240))
                concurrent=[]
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(post,data)
                    while not future.done():
                        payload,wall,times=post(preview,'/api/detect')
                        concurrent.append(dict(http_ms=wall,faces=len(payload['faces']),timings_ms=times));time.sleep(.2)
                    _,wall,times=future.result()
                result['preview_during_recognition']=concurrent;result['concurrent_identity_http_ms']=wall;save()
                for path in sorted((ROOT/'DATASET/validation').rglob('*')):
                    if not path.is_file():continue
                    data=frame(path,(640,480));payload,wall,times=post(data)
                    expected=next(r['student_id'] for r in gallery if r['student_id']==path.parent.name)
                    with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:
                        row=db.execute('SELECT s.student_code,s.full_name FROM legacy_mappings m JOIN students s ON s.id=m.student_id WHERE dataset_label=?',(expected,)).fetchone()
                    label=row[0]+' · '+row[1]
                    prediction=payload['faces'][0]['top1']['candidate'] if len(payload['faces'])==1 else None
                    light,lightwall,_=post(frame(path,(320,240)),'/api/detect')
                    item=dict(source=str(path.relative_to(ROOT)),expected=label,predicted=prediction,correct=prediction==label,faces=len(payload['faces']),http_ms=wall,preview_faces=len(light['faces']),preview_http_ms=lightwall)
                    result['validation'].append(item);save();print('VALIDATION',path.name,item['correct'],flush=True)
        finally:requests.delete(URL+'/api/camera/sessions/'+sid,headers=headers)
    result['memory_after']=memory();result['health_after']=requests.get(URL+'/api/health').json();save()


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');run(sys.argv[1])
