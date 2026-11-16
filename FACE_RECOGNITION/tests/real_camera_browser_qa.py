"""Actual local HTTP + actual models + dataset video; no mocked AI/API responses."""
import asyncio
import base64
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.face_recognition.runtime_service import RecognitionEngine
from tests.camera_live_api_qa import payload
from tests.real_ai_execution_benchmark import protected
from playwright.async_api import async_playwright
from werkzeug.serving import make_server


async def run():
    before=protected();engine=RecognitionEngine();errors=[];results=[]
    with tempfile.TemporaryDirectory(dir=ROOT/'.venv',prefix='real-camera-web-') as tmp:
        dbpath=Path(tmp)/'test.sqlite3'
        with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as src,closing(sqlite3.connect(dbpath)) as dest:src.backup(dest)
        app=create_app(engine,True,{'DATABASE_PATH':dbpath})
        while engine.state not in ('ready','error'):await asyncio.sleep(1)
        assert engine.state=='ready'
        store=app.extensions['management_store'];attendance=app.extensions['attendance_service']
        classroom=store.save('classes',dict(class_code='REAL-CAMERA-QA',class_name='Kiểm thử chỉ trong database tạm'))
        for p in store.list_entities('students'):store.membership(classroom['id'],p['id'])
        session=attendance.create(dict(class_id=classroom['id'],session_name='Real browser AI QA',request_key='real-browser-qa'))
        server=make_server('127.0.0.1',5055,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
        labels=sorted({r['student_id'] for r in gallery})
        paths=[Path(next(r for r in gallery if r['student_id']==label)['image_path']) for label in labels[:2]]
        try:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,
                    args=['--disable-gpu','--renderer-process-limit=1'])
                context=await browser.new_context(viewport=dict(width=1366,height=900))
                await context.add_init_script("""(() => {
                  const c=document.createElement('canvas');c.width=640;c.height=480;
                  const x=c.getContext('2d');x.fillStyle='#eee';x.fillRect(0,0,640,480);
                  window.showDatasetImage=async url=>{const im=new Image();im.src=url;await im.decode();x.fillStyle='#eee';x.fillRect(0,0,640,480);
                    const s=Math.min(640/im.width,480/im.height);x.drawImage(im,(640-im.width*s)/2,(480-im.height*s)/2,im.width*s,im.height*s);};
                  window.showBlank=()=>{x.fillStyle='#eee';x.fillRect(0,0,640,480);};
                  navigator.mediaDevices.getUserMedia=async()=>c.captureStream(15);
                  setInterval(()=>{x.fillStyle='#eee';x.fillRect(0,0,1,1);},100);
                })();""")
                page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
                await page.goto(f'http://127.0.0.1:5055/attendance?session={session["id"]}')
                await page.wait_for_function('!document.getElementById("start-camera").disabled')
                async def show(path):
                    data,_=payload(path)
                    await page.evaluate('url=>showDatasetImage(url)','data:image/jpeg;base64,'+base64.b64encode(data).decode())
                await show(paths[0]);await page.click('#start-camera')
                for i,path in enumerate(paths):
                    if i:
                        await page.evaluate('showBlank()');await page.wait_for_function('cameraDiagnostics.live_labels.length===0')
                        await show(path)
                    start=time.perf_counter()
                    await page.wait_for_function('(name)=>cameraDiagnostics.live_labels?.some(t=>t.lines[0]===name)',arg=labels[i],timeout=120000)
                    elapsed=(time.perf_counter()-start)*1000
                    assert attendance.detail(session['id'])['stats']['present']==0
                    labels_now=await page.evaluate('cameraDiagnostics.live_labels')
                    assert all('Đang nhận diện' not in line for r in labels_now for line in r['lines'])
                    metrics=await page.evaluate('({time_to_name:cameraDiagnostics.time_to_name_ms,records:cameraDiagnostics.identity_records,reuses:cameraDiagnostics.tracking_reuses||0})')
                    results.append(dict(case='student_'+str(i+1),wait_ms=elapsed,metrics=metrics))
                    print('REAL BROWSER NAME',i+1,round(elapsed),flush=True)
                    if i==0:
                        count=len(metrics['records']);await page.wait_for_timeout(5000)
                        assert await page.evaluate('cameraDiagnostics.identity_records.length')==count
                        assert await page.evaluate('(cameraDiagnostics.tracking_reuses||0)>0')
                    else:await page.wait_for_timeout(1500)
                    assert await page.evaluate('(name)=>cameraDiagnostics.live_labels.some(t=>t.lines[0]===name)',labels[i])
                await page.locator('[data-confirm-student]').first.click()
                await page.wait_for_function('document.querySelector("[data-count=present]").textContent==="1"')
                assert attendance.detail(session['id'])['stats']['present']==1
                await page.evaluate('showBlank()');await page.wait_for_function('cameraDiagnostics.live_labels.length===0')
                await show(ROOT/'DATASET/Test/Group10.jpg')
                await page.wait_for_function('cameraDiagnostics.live_labels?.filter(t=>t.lines.length>0).length===2',timeout=120000)
                results.append(dict(case='multiple',labels=await page.evaluate('cameraDiagnostics.live_labels')))
                await page.click('#stop-camera');await page.wait_for_timeout(1500)
                assert await page.evaluate('cameraDiagnostics.live_labels.length')==0
                assert not errors,errors
                await browser.close()
        finally:server.shutdown()
    assert protected()==before
    report=dict(status='passed',real_models=True,real_http=True,ai_mocks_used=False,physical_camera_tested=False,
        protected_unchanged=True,results=results,automatic_enabled=False,manual_sqlite_confirmation=True)
    (ROOT/'outputs/real_camera_browser_qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS actual HTTP/browser/AI, names, replacement, multiple faces, stop, manual SQLite and protected data.',flush=True)


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
