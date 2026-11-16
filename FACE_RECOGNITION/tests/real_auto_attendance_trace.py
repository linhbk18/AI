"""Real ArcFace/API evidence: candidate != verified. All writes use disposable SQLite."""
import asyncio
import base64
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time

import requests
from playwright.async_api import async_playwright
from werkzeug.serving import make_server

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.face_recognition.runtime_service import RecognitionEngine
from tests.camera_live_api_qa import payload
from tests.real_ai_execution_benchmark import protected


async def run():
    before=protected();report={'ai_mocks_used':False,'physical_camera_tested':False};base='http://127.0.0.1:5055'
    with tempfile.TemporaryDirectory(dir=ROOT/'.venv',prefix='auto-trace-') as tmp:
        path=Path(tmp)/'copy.sqlite3'
        with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as src,closing(sqlite3.connect(path)) as dst:src.backup(dst)
        engine=RecognitionEngine();app=create_app(engine,True,{'DATABASE_PATH':path})
        while engine.state not in ('ready','error'):await asyncio.sleep(1)
        assert engine.state=='ready'
        store=app.extensions['management_store'];service=app.extensions['attendance_service']
        people=store.list_entities('students');linh=next(p for p in people if p['student_code']=='DEMO-SV001')
        partner=next(p for p in people if p['student_code']=='DEMO-SV003')
        classroom=store.save('classes',dict(class_code='AUTO-TRACE-QA',class_name='Chỉ kiểm thử database tạm'))
        for p in (linh,partner):store.membership(classroom['id'],p['id'])
        session=service.create(dict(class_id=classroom['id'],session_name='Real candidate trace',request_key='auto-trace'))
        aid=session['id'];gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
        linhsample=Path(next(r for r in gallery if r['student_id']==linh['full_name'])['image_path'])
        outsider=next(p for p in people if p['student_code']=='DEMO-SV002')
        outsidersample=Path(next(r for r in gallery if r['student_id']==outsider['full_name'])['image_path'])
        server=make_server('127.0.0.1',5055,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            html=requests.get(base+'/attendance').text
            cfg=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',html).group(1))
            headers={'X-Camera-Token':cfg['csrfToken']}
            cs=requests.post(base+'/api/camera/sessions',headers=headers,json=dict(profile='retinaface_scaled_experimental',attendance_session_id=aid)).json()['session_id']
            fh=dict(headers,**{'X-Camera-Session':cs,'Content-Type':'image/jpeg'})
            for case,sample in (('linh',linhsample),('outside_class',outsidersample),('blank',None)):
                data,_=payload(sample)
                # Feed ACTUAL YuNet previews while real RetinaFace/ArcFace processes this frame.
                requests.post(base+'/api/detect',data=data,headers=fh,timeout=30).raise_for_status()
                done=threading.Event();preview_errors=[]
                def previews():
                    while not done.wait(.65):
                        try:requests.post(base+'/api/detect',data=data,headers=fh,timeout=30).raise_for_status()
                        except Exception as exc:preview_errors.append(str(exc))
                worker=threading.Thread(target=previews);worker.start()
                try:r=requests.post(base+'/api/recognize',data=data,headers=fh,timeout=180);r.raise_for_status();body=r.json()
                finally:done.set();worker.join(35)
                assert not preview_errors,preview_errors
                assert body['automatic_attendance']['enabled'] is False
                assert service.detail(aid)['stats']['present']==0
                if case=='linh':
                    f=body['faces'][0];assert f['top1']['student']['id']==linh['id'] and not f['outside_class'] and not f['identity_confirmed']
                    assert f['tracking']['valid'],body
                elif case=='outside_class':assert body['faces'][0]['outside_class'] and not body['faces'][0]['eligible_for_confirmation']
                else:assert body['faces']==[]
                report[case]=body;print('REAL API',case,'PASS',flush=True)
            # Real native inference continues while an independent blank preview removes its track.
            from concurrent.futures import ThreadPoolExecutor
            data,_=payload(linhsample);blank,_=payload(None)
            with ThreadPoolExecutor(max_workers=1) as pool:
                requests.post(base+'/api/detect',data=data,headers=fh,timeout=30).raise_for_status()
                pending=pool.submit(requests.post,base+'/api/recognize',data=data,headers=fh,timeout=180)
                until=time.monotonic()+20
                while not requests.get(base+'/api/health').json()['busy']:
                    assert time.monotonic()<until
                    time.sleep(.05)
                requests.post(base+'/api/detect',data=blank,headers=fh,timeout=30).raise_for_status()
                late=pending.result();late.raise_for_status()
                assert all(not f['tracking']['valid'] for f in late.json()['faces'])
                assert late.json()['automatic_attendance']['grants']==[]
                report['left_before_inference_finished_real_http']=True
                requests.post(base+'/api/detect',data=data,headers=fh,timeout=30).raise_for_status()
                pending=pool.submit(requests.post,base+'/api/recognize',data=data,headers=fh,timeout=180)
                until=time.monotonic()+20
                while not requests.get(base+'/api/health').json()['busy']:
                    assert time.monotonic()<until
                    time.sleep(.05)
                requests.delete(base+'/api/camera/sessions/'+cs,headers=headers)
                assert pending.result().status_code==409
                report['camera_stopped_during_real_inference_no_late_result']=True
            requests.delete(base+'/api/camera/sessions/'+cs,headers=headers)
            status=requests.get(base+'/api/attendance/automation-status').json()
            assert not status['ready'] and status['unknown_samples']==0;report['automation_status']=status
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--disable-gpu','--renderer-process-limit=1'])
                context=await browser.new_context(viewport=dict(width=1366,height=900))
                await context.add_init_script("""(() => {
                  const c=document.createElement('canvas');c.width=640;c.height=480;const x=c.getContext('2d');
                  window.paintDataset=async url=>{const im=new Image();im.src=url;await im.decode();x.fillStyle='#eee';x.fillRect(0,0,640,480);const s=Math.min(640/im.width,480/im.height);x.drawImage(im,(640-im.width*s)/2,(480-im.height*s)/2,im.width*s,im.height*s);};
                  navigator.mediaDevices.getUserMedia=async()=>c.captureStream(15);
                  setInterval(()=>{x.fillStyle='#eee';x.fillRect(0,0,1,1);},100);
                })();""")
                page=await context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
                await page.goto(base+f'/attendance?session={aid}')
                await page.wait_for_function('!document.getElementById("start-camera").disabled')
                data,_=payload(linhsample);await page.evaluate('url=>paintDataset(url)','data:image/jpeg;base64,'+base64.b64encode(data).decode())
                await page.click('#start-camera')
                await page.wait_for_function('(name)=>cameraDiagnostics.live_labels.some(t=>t.lines[0]===name)',arg=linh['full_name'],timeout=120000)
                assert await page.locator('[data-count=total]').text_content()=='2'
                assert await page.locator('[data-count=present]').text_content()=='0'
                assert 'đang khóa' in await page.locator('.policy-notice strong').text_content()
                await page.locator(f'[data-confirm-student="{linh["id"]}"]').click()
                await page.wait_for_function('document.querySelector("[data-count=present]").textContent==="1"')
                assert await page.locator('.attendance-progress').get_attribute('value')=='1'
                assert await page.locator('.attendance-progress').get_attribute('max')=='2'
                assert service.detail(aid)['records'][0]['recognition_source']=='MANUAL'
                await page.click('#stop-camera')
                # Test the frontend auto-save event with an actual committed SQL payload.
                # No AI verification is fabricated: this is a UI component contract test only.
                detail=requests.get(base+f'/api/attendance/sessions/{aid}').json()
                await page.evaluate('s=>dispatchEvent(new CustomEvent("attendance:auto",{detail:s}))',detail)
                assert await page.locator('[data-count=present]').text_content()=='1'
                assert not errors,errors
                await page.reload();await page.wait_for_function('document.querySelector("[data-count=present]")?.textContent==="1"')
                await browser.close()
            again=requests.post(base+f'/api/attendance/sessions/{aid}/confirm',headers=headers,json=dict(student_id=linh['id'],confirmed=True));assert again.status_code==200 and again.json()['changed'] is False
            reopened=create_app(engine,False,{'DATABASE_PATH':path})
            assert reopened.test_client().get(f'/api/attendance/sessions/{aid}').json['stats']==dict(total=2,present=1,absent=0,pending=1)
            report.update(frontend_manual_zero_of_two_to_one_of_two=True,frontend_auto_event_contract_with_committed_sql_payload=True,
                restart_persistence=True,no_duplicate=True,automatic_positive_camera_test='not_possible_without_independent_verification_evidence')
        finally:server.shutdown()
    assert protected()==before
    report.update(protected_unchanged=True,status='passed')
    (ROOT/'outputs/real_auto_attendance_trace.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS real AI trace; verified auto remains blocked; manual/UI/SQL persistence on temporary copy.',flush=True)


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
