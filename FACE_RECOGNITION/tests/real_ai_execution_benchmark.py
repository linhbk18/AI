"""Real weights only: compare eager/graph on identical aligned tensors and benchmark HTTP."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.face_recognition.runtime_service import RecognitionEngine
from tests.camera_live_api_qa import payload


def protected():
    paths=[p for p in (ROOT/'DATASET').rglob('*') if p.is_file()]
    paths += [ROOT/'outputs'/name for name in ('face_embeddings.json','face_config.json','recognition_threshold.json')]
    out={}
    for p in paths:
        h=hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        out[str(p.relative_to(ROOT))]=h.hexdigest()
    with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:out['sqlite']=hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
    return out


def run():
    import numpy as np
    from PIL import Image
    from io import BytesIO
    before=protected();engine=RecognitionEngine();engine.start_loading()
    while engine.state not in ('ready','error'):time.sleep(1)
    if engine.state!='ready':raise RuntimeError('Actual models failed to load')
    report=dict(real_models=True,profile='retinaface_scaled_experimental',startup=engine.health(),benchmark=[],equivalence=[])
    out=ROOT/'outputs/real_ai_execution_benchmark.json'
    def save():out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    labels=sorted({r['student_id'] for r in gallery})
    cases=[('single',Path(next(r for r in gallery if r['student_id']==labels[0])['image_path'])),('multiple',ROOT/'DATASET/Test/Group10.jpg'),('blank',None)]
    with tempfile.TemporaryDirectory(dir=ROOT/'.venv',prefix='real-ai-') as tmp:
        dbpath=Path(tmp)/'test.sqlite3'
        with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as src,closing(sqlite3.connect(dbpath)) as dest:src.backup(dest)
        app=create_app(engine,False,{'DATABASE_PATH':dbpath});client=app.test_client()
        import re
        cfg=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',client.get('/attendance').text).group(1))
        headers={'X-Camera-Token':cfg['csrfToken']}
        sid=client.post('/api/camera/sessions',json={'profile':report['profile']},headers=headers).json['session_id']
        fh=dict(headers,**{'Content-Type':'image/jpeg','X-Camera-Session':sid})
        for mode in ('eager','graph'):
            if mode=='graph':
                start=time.perf_counter();engine.pipeline.prepare_graph();report['graph_compile_ms']=(time.perf_counter()-start)*1000
            for name,path in cases:
                data,_=payload(path)
                for repeat in range(2):
                    start=time.perf_counter();r=client.post('/api/recognize',data=data,headers=fh);elapsed=(time.perf_counter()-start)*1000
                    assert r.status_code==200,r.json
                    row=dict(mode=mode,case=name,repeat=repeat,http_ms=elapsed,faces=len(r.json['faces']),timings=r.json['timings_ms'])
                    report['benchmark'].append(row);save();print(json.dumps(row),flush=True)
        compiled=engine.pipeline.forward
        def compare(tensor):
            eager=np.asarray(engine.pipeline.model.forward(tensor));graph=np.asarray(compiled(tensor))
            error=float(np.max(np.abs(eager-graph)))
            cosine=float(eager@graph/(np.linalg.norm(eager)*np.linalg.norm(graph)))
            assert error<1e-4 and cosine>0.999999,(error,cosine)
            return graph.tolist()
        engine.pipeline.forward=compare
        for path in sorted((ROOT/'DATASET/validation').rglob('*')):
            if not path.is_file():continue
            data,_=payload(path)
            image=np.asarray(Image.open(BytesIO(data)).convert('RGB'))[:,:,::-1].copy()
            faces,_=engine.pipeline.run(image,report['profile'])
            predicted=engine.matcher.match(faces[0]['embedding'])['best_candidate'] if len(faces)==1 else None
            predicted=engine._public_identities.get(predicted,{}).get('full_name',predicted)
            correct=predicted==path.parent.name
            report['equivalence'].append(dict(source=path.relative_to(ROOT).as_posix(),faces=len(faces),correct=correct))
            save();print('REAL VALIDATION',len(report['equivalence']),correct,flush=True)
        assert len(report['equivalence'])==25 and all(r['correct'] for r in report['equivalence'])
        engine.pipeline.forward=compiled
        store=app.extensions['management_store'];attendance=app.extensions['attendance_service']
        people=store.list_entities('students')
        classroom=store.save('classes',dict(class_code='REAL-AI-QA',class_name='Chỉ trong database kiểm thử'))
        for person in people:store.membership(classroom['id'],person['id'])
        r=client.post('/api/attendance/sessions',headers=headers,json=dict(class_id=classroom['id'],session_name='Real AI QA',request_key='real-ai-qa'))
        assert r.status_code==201,r.json
        aid=r.json['id'];cs=client.post('/api/camera/sessions',headers=headers,json=dict(profile=report['profile'],attendance_session_id=aid)).json['session_id']
        data,_=payload(cases[0][1]);response=client.post('/api/recognize',data=data,headers=dict(fh,**{'X-Camera-Session':cs}))
        assert response.status_code==200,response.json
        assert response.json['automatic_attendance']['enabled'] is False
        assert attendance.detail(aid)['stats']['present']==0
        person=response.json['faces'][0]['top1']['student'];assert person
        posted=client.post(f'/api/attendance/sessions/{aid}/confirm',headers=headers,json=dict(student_id=person['id'],confirmed=True,recognition_source='AI_VERIFIED'))
        assert posted.status_code==400
        posted=client.post(f'/api/attendance/sessions/{aid}/confirm',headers=headers,json=dict(student_id=person['id'],confirmed=True))
        assert posted.status_code==200 and posted.json['session']['stats']['present']==1
        again=client.post(f'/api/attendance/sessions/{aid}/confirm',headers=headers,json=dict(student_id=person['id'],confirmed=True))
        assert again.status_code==200 and again.json['changed'] is False
        assert attendance.confirm_verified(aid,[person['id']],lambda:False,time.monotonic()-3)['changed'] is False
        ended=client.post(f'/api/attendance/sessions/{aid}/finish',headers=headers,json={})
        assert ended.status_code==200 and ended.json['stats']['pending']==0
        report['attendance_real_ai_disabled_manual_idempotent_finish']=True
        assert protected()==before
        report['protected_unchanged']=True;report['status']='passed';save()
    print('PASS real weights, 25 same-tensor eager/graph comparisons, real API benchmark and protected data.',flush=True)


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');run()
