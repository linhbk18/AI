"""Real weights and real HTTP. Static media fixtures contain actual dataset pixels, never fake AI results."""
import asyncio
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time

import cv2
import numpy as np
from PIL import Image,ImageOps,ImageFilter
import requests
from playwright.async_api import async_playwright
from werkzeug.serving import make_server

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.face_recognition.runtime_service import RecognitionEngine
from tests.real_ai_execution_benchmark import protected

URL='http://127.0.0.1:5055'


def photo(path=None,fmt='JPEG',blur=False):
    if path:
        with Image.open(path) as im:im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((640,480))
    else:im=Image.new('RGB',(640,480),'#eee')
    if blur:im=im.filter(ImageFilter.GaussianBlur(12))
    out=BytesIO();im.save(out,format=fmt);return out.getvalue()


def movie(path,image,seconds=4,fps=4,codec='mp4v',size=(640,480)):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*codec),fps,size)
    if not writer.isOpened():raise RuntimeError('Test video encoder unavailable: '+codec)
    scale=min(size[0]/image.shape[1],size[1]/image.shape[0]);scaled=cv2.resize(image,(round(image.shape[1]*scale),round(image.shape[0]*scale)))
    frame=np.full((size[1],size[0],3),230,np.uint8);x=(size[0]-scaled.shape[1])//2;y=(size[1]-scaled.shape[0])//2;frame[y:y+scaled.shape[0],x:x+scaled.shape[1]]=scaled
    for _ in range(seconds*fps):writer.write(frame)
    writer.release()


async def run():
    quick='--ui-only' in sys.argv
    before=protected();report=dict(real_ai=True,ai_mocks_used=False,physical_camera_tested=False,checks={},benchmark=[],validation=[])
    def save(): (ROOT/('outputs/upgrade_ui_followup.json' if quick else 'outputs/upgrade_real_qa.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(dir=ROOT/'.venv',prefix='upgrade-qa-') as folder:
        tmp=Path(folder);dbpath=tmp/'copy.sqlite3'
        with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as src,closing(sqlite3.connect(dbpath)) as dest:src.backup(dest)
        engine=RecognitionEngine();app=create_app(engine,True,{'DATABASE_PATH':dbpath})
        while engine.state not in ('ready','error'):await asyncio.sleep(1)
        assert engine.state=='ready';report['startup']=engine.health()
        store=app.extensions['management_store'];attendance=app.extensions['attendance_service']
        people=store.list_entities('students');linh=next(p for p in people if p['student_code']=='DEMO-SV001')
        gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
        source=Path(next(r for r in gallery if r['student_id']==linh['full_name'])['image_path']);group=ROOT/'DATASET/Test/Group10.jpg'
        c=store.save('classes',dict(class_code='UPGRADE-QA',class_name='Only in temporary database'))
        for p in people:store.membership(c['id'],p['id'])
        session=attendance.create(dict(class_id=c['id'],session_name='Media QA',request_key='media-qa'));sid=session['id']
        server=make_server('127.0.0.1',5055,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            cfg=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',requests.get(URL+'/attendance').text).group(1));headers={'X-Camera-Token':cfg['csrfToken']}
            if not quick:
                cs=requests.post(URL+'/api/camera/sessions',headers=headers,json={'profile':'retinaface_scaled_experimental','attendance_session_id':sid}).json()['session_id']
                fh=dict(headers,**{'X-Camera-Session':cs,'Content-Type':'image/jpeg'})
                for case,path in (('single',source),('multiple',group),('blank',None)):
                    for repeat in range(3):
                        start=time.perf_counter();r=requests.post(URL+'/api/recognize',data=photo(path),headers=fh,timeout=180);r.raise_for_status();body=r.json()
                        report['benchmark'].append(dict(case=case,repeat=repeat,http_ms=(time.perf_counter()-start)*1000,timings=body['timings_ms'],faces=len(body['faces'])))
                        assert 'recognition_results' in body;save();print('BENCH',case,repeat,flush=True)
                for path in sorted((ROOT/'DATASET/validation').rglob('*')):
                    if not path.is_file():continue
                    r=requests.post(URL+'/api/recognize',data=photo(path),headers=fh,timeout=180);r.raise_for_status();faces=r.json()['faces']
                    predicted=faces[0]['top1']['student']['full_name'] if len(faces)==1 and faces[0]['top1']['student'] else None
                    report['validation'].append(dict(source=path.relative_to(ROOT).as_posix(),correct=predicted==path.parent.name,faces=len(faces)));save()
                    print('VALIDATION',len(report['validation']),report['validation'][-1]['correct'],flush=True)
                requests.delete(URL+'/api/camera/sessions/'+cs,headers=headers)
            def post_file(data,name,kind='image',session_id=None):
                return requests.post(URL+'/api/media/jobs',headers=headers,data=dict(source_type=kind,session_id=session_id or sid,profile='retinaface_scaled_experimental'),files={'file':(name,data)},timeout=60)
            async def finish(identity):
                until=time.monotonic()+300
                while True:
                    r=requests.get(URL+'/api/media/jobs/'+identity,timeout=15);r.raise_for_status();job=r.json()
                    if job['state'] not in ('queued','running','cancelling'):return job
                    assert time.monotonic()<until,job
                    await asyncio.sleep(.2)
            async def submit(data,name,kind='image',session_id=None):
                r=post_file(data,name,kind,session_id);assert r.status_code==202,r.text
                job=await finish(r.json()['id']);assert job['state']=='completed',job
                assert all(not f['auto_attendance_eligible'] and not f['presence_verified'] for frame in job['frames'] for f in frame['faces'])
                return job
            if not quick:
                first=None
                for fmt,ext in (('JPEG','jpg'),('PNG','png'),('WEBP','webp')):
                    job=await submit(photo(source,fmt),f'photo.{ext}');first=job
                    assert len(job['frames'][0]['faces'])==1
                    assert job['students'][0]['student_id']==linh['id']
                report['checks']['jpeg_png_webp_actual_inference']=True
                assert attendance.detail(sid)['stats']['present']==0
                r=requests.post(URL+'/api/media/jobs/'+first['id']+'/confirm',headers=headers,json={'student_ids':[linh['id'],linh['id']],'confirmed':True});assert r.status_code==200 and r.json()['session']['stats']['present']==1
                r=requests.post(URL+'/api/media/jobs/'+first['id']+'/confirm',headers=headers,json={'student_ids':[linh['id']],'confirmed':True});assert r.status_code==200 and r.json()['changed']==0
                report['checks']['static_no_auto_manual_atomic_deduplicated']=True
                assert all(r['recognition_source']=='MANUAL' for r in attendance.detail(sid)['records'] if r['attendance_status']=='PRESENT')
                job=await submit(photo(group),'group.jpg');assert len(job['frames'][0]['faces'])==2
                blank=await submit(photo(),'blank.jpg');assert blank['students']==[] and blank['frames'][0]['faces']==[]
                blur=await submit(photo(source,blur=True),'blur.jpg');report['blur_detected_faces']=len(blur['frames'][0]['faces'])
                report['checks']['group_blank_blur_actual_inference']=True
                for name,data in (('bad.jpg',b'not an image'),('bad.exe',b'xxx')):
                    assert post_file(data,name).status_code==415
                assert post_file(b'x'*(8*1024*1024+1),'large.jpg').status_code==413
                safe=await submit(photo(source),'../../escape.jpg');assert '/' not in safe['filename'] and '\\' not in safe['filename']
                report['checks']['invalid_format_size_and_traversal_rejected_or_sanitized']=True
                im=cv2.imdecode(np.frombuffer(photo(source),np.uint8),cv2.IMREAD_COLOR)
                paths=[]
                for name,pixels,codec,secs in (('one.mp4',im,'mp4v',4),('group.mp4',cv2.imdecode(np.frombuffer(photo(group),np.uint8),cv2.IMREAD_COLOR),'mp4v',4),('empty.avi',np.full((480,640,3),230,np.uint8),'MJPG',2)):
                    path=tmp/name;movie(path,pixels,secs,codec=codec);paths.append(path)
                    job=await submit(path.read_bytes(),name,'video')
                    assert job['processed_frames']==job['total_frames'] and job['progress']==1
                    assert len({s['student_id'] for s in job['students']})==len(job['students'])
                    if name=='one.mp4':assert len(job['students'])==1 and job['students'][0]['appearances']==2
                    if name=='empty.avi':assert job['students']==[]
                report['checks']['real_mp4_avi_sampling_timestamps_unique_students']=True
                assert post_file(b'broken','broken.mp4','video').status_code==415
                corrupt=post_file(b'\x00\x00\x00\x18ftypisom'+b'x'*50,'corrupt.mp4','video');assert corrupt.status_code==202
                assert (await finish(corrupt.json()['id']))['state']=='failed'
                long=tmp/'long.mp4';movie(long,np.zeros((64,64,3),np.uint8),121,fps=1,size=(64,64))
                too_long=post_file(long.read_bytes(),'long.mp4','video');assert too_long.status_code==202
                assert (await finish(too_long.json()['id']))['state']=='failed'
                cancel_path=tmp/'cancel.mp4';movie(cancel_path,im,20)
                running=post_file(cancel_path.read_bytes(),'cancel.mp4','video');assert running.status_code==202
                blocked=post_file(photo(source),'another.jpg');assert blocked.status_code==429
                requests.delete(URL+'/api/media/jobs/'+running.json()['id'],headers=headers)
                assert (await finish(running.json()['id']))['state']=='cancelled'
                await asyncio.sleep(.3)
                manager=app.extensions['media_jobs'];assert all(not j['_path'].exists() for j in manager.jobs.values() if j['state'] in ('completed','failed','cancelled'))
                report['checks']['corrupt_codec_duration_cancel_one_job_cleanup']=True
            else:
                im=cv2.imdecode(np.frombuffer(photo(source),np.uint8),cv2.IMREAD_COLOR)
                paths=[tmp/'ui.webm'];movie(paths[0],im,codec='VP80')
            pair=store.save('classes',dict(class_code='UPGRADE-PAIR',class_name='Only temporary two-student class'))
            for p in people:
                if p['student_code'] in ('DEMO-SV001','DEMO-SV003'):store.membership(pair['id'],p['id'])
            pair_session=attendance.create(dict(class_id=pair['id'],session_name='Browser media QA',request_key='media-browser-qa'))
            outsider=next(p for p in people if p['student_code']=='DEMO-SV002');outside_path=Path(next(r for r in gallery if r['student_id']==outsider['full_name'])['image_path'])
            outside=await submit(photo(outside_path),'outside.jpg',session_id=pair_session['id']);assert outside['students']==[]
            report['checks']['outside_class_not_proposed']=True;save()
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--disable-gpu','--renderer-process-limit=1'])
                context=await browser.new_context(viewport=dict(width=1366,height=1000));page=await context.new_page();errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
                # Synthetic video DEVICE only; pixels are an actual registered photograph. AI remains real.
                import base64
                dataurl='data:image/jpeg;base64,'+base64.b64encode(photo(source)).decode()
                await context.add_init_script("""(() => { const c=document.createElement('canvas');c.width=640;c.height=480;const x=c.getContext('2d');
                  navigator.mediaDevices.getUserMedia=async()=>{const im=new Image();im.src="""+json.dumps(dataurl)+""";await im.decode();const s=Math.min(640/im.width,480/im.height);x.fillStyle='#eee';x.fillRect(0,0,640,480);x.drawImage(im,(640-im.width*s)/2,(480-im.height*s)/2,im.width*s,im.height*s);return c.captureStream(15);};
                  setInterval(()=>{x.fillStyle='#eee';x.fillRect(0,0,1,1);},100);
                })();""")
                await page.goto(URL+f'/attendance?session={pair_session["id"]}')
                await page.wait_for_function('window.attendanceBridge?.canStart()')
                assert await page.locator('#input-source option').count()==4
                assert await page.locator('.candidates-card').count()==0
                await page.locator('#input-source').select_option('snapshot')
                await page.click('#open-source-camera');await page.wait_for_function('!document.getElementById("capture-source").disabled')
                await page.click('#capture-source');await page.wait_for_function('document.querySelector("#media-review")?.hidden===false',timeout=120000)
                assert await page.locator('[data-count=present]').text_content()=='0'
                assert await page.locator('#media-students input').count()==1
                await page.locator('#media-students input').check();await page.wait_for_timeout(5500);assert await page.locator('#media-students input').is_checked();await page.click('#confirm-media')
                await page.wait_for_function('document.querySelector("[data-count=present]").textContent==="1"')
                assert await page.locator('.attendance-progress').get_attribute('max')=='2'
                await page.locator('#roster-filter').select_option('PRESENT');assert await page.locator('.attendance-roster-row:visible').count()==1
                await page.locator('#input-source').select_option('image');await page.locator('#media-file').set_input_files(dict(name='group.jpg',mimeType='image/jpeg',buffer=photo(group)));await page.click('#analyze-media')
                await page.wait_for_function('document.querySelector("#media-review")?.hidden===false',timeout=120000)
                await page.locator('#input-source').select_option('video');await page.locator('#media-file').set_input_files(str(paths[0]));await page.click('#analyze-media')
                await page.wait_for_function('document.querySelector("#media-review")?.hidden===false',timeout=120000)
                assert await page.locator('#media-frame option').count()==2
                await page.locator('#media-frame').select_option('1')
                if quick:
                    await page.wait_for_function('document.getElementById("media-canvas").dataset.frameIndex==="1" && !document.getElementById("media-video").seeking')
                    assert await page.locator('#media-canvas').is_visible()
                    assert await page.locator('#media-video').evaluate('(e)=>getComputedStyle(e).position')=='static'
                    assert 'ui.webm' in await page.locator('#media-file-info').text_content()
                await page.screenshot(path=str(ROOT/'.venv/upgrade_media_ui.png'),full_page=True)
                await page.set_viewport_size(dict(width=390,height=844));assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors,errors
                await browser.close()
            report['checks']['browser_snapshot_image_video_manual_sql_progress_filter_responsive']=True
            restarted=create_app(engine,False,{'DATABASE_PATH':dbpath});assert restarted.test_client().get(f'/api/attendance/sessions/{pair_session["id"]}').json['stats']['present']==1
            report['checks']['flask_restart_persistence']=True
        finally:server.shutdown()
    assert protected()==before
    report.update(protected_unchanged=True,status='passed',automatic_enabled=False)
    save();print('PASS upgrade real HTTP/model/browser tests. No physical camera or verified automatic attendance claim.',flush=True)


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
