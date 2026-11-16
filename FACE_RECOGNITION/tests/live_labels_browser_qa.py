"""Live overlay tests: synthetic video + fake AI, real Flask APIs and disposable SQLite."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import create_app
from backend.database.import_legacy_students import import_students
from tests.test_management import EnrollmentFake

URL = 'http://127.0.0.1:5055'


def protected():
    paths = list((ROOT/'DATASET').rglob('*')) + [ROOT/'outputs'/n for n in
        ('face_embeddings.json', 'face_config.json', 'recognition_threshold.json')]
    result = {}
    for p in paths:
        if p.is_file():
            h = hashlib.sha256()
            with p.open('rb') as f:
                for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
            result[str(p.relative_to(ROOT))] = h.hexdigest()
    with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:
        result['sqlite_logical_sha256'] = hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
    return result


async def run():
    before = protected(); checks = {}; errors = []
    with tempfile.TemporaryDirectory(prefix='live-labels-', dir=ROOT/'.venv') as tmp:
        engine = EnrollmentFake()
        app = create_app(engine, start_models=False, overrides={'TESTING': True, 'DATABASE_PATH': Path(tmp)/'test.sqlite3'})
        store = app.extensions['management_store']
        import_students(store, json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8')))
        people = sorted(store.list_entities('students'), key=lambda s: s['id'])
        classroom = store.save('classes', dict(class_code='OVERLAY-QA', class_name='Lớp chỉ trong kiểm thử'))
        for p in people: store.membership(classroom['id'], p['id'])
        client = app.test_client(); state = dict(count=2, unknown=False, delay=.25, requests=0)

        def boxes():
            return [dict(face_id=i+1, box=dict(x=80+240*i, y=120, w=100, h=120)) for i in range(state['count'])]

        def detect(bgr, cancelled):
            return dict(faces=boxes(), frame=dict(width=bgr.shape[1], height=bgr.shape[0]), preview_only=True)

        def recognize(bgr, cancelled, profile, allowed_student_ids=None):
            faces = boxes()
            for i, face in enumerate(faces):
                p = people[i]
                face.update(top1=dict(candidate=p['full_name'], similarity=.81-i*.01,
                    student=None if state['unknown'] else {k:p[k] for k in ('id','full_name','student_code')}),
                    top2=dict(candidate=None, similarity=None), margin=None, eligible_for_confirmation=not state['unknown'], outside_class=False)
            return dict(faces=faces, detected_faces=len(faces), frame=dict(width=bgr.shape[1], height=bgr.shape[0]),
                processing_ms=10, identity_confirmation_enabled=False, attendance_enabled=False, profile=profile)

        engine.detect_preview = detect; engine.analyze = recognize
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',
                headless=True, args=['--disable-gpu','--renderer-process-limit=1'])
            context = await browser.new_context(viewport=dict(width=1440,height=1050), device_scale_factor=2)
            await context.add_init_script("""(() => {
              const c=document.createElement('canvas');c.width=640;c.height=480;
              window.paintFace=(changed=false)=>{const x=c.getContext('2d');x.fillStyle='#eee';x.fillRect(0,0,640,480);
                x.fillStyle=changed?'#cc3300':'#224488';x.fillRect(80,120,100,120);x.fillStyle='#aa7722';x.fillRect(320,120,100,120);};
              paintFace();navigator.mediaDevices.getUserMedia=async()=>c.captureStream(15);
              // Periodic identical redraw keeps the synthetic MediaStream alive.
              setInterval(()=>c.getContext('2d').fillRect(0,0,1,1),100);
            })();""")
            page = await context.new_page(); page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: d.accept())

            async def route(route):
                req=route.request
                response=client.open(req.url.removeprefix(URL),method=req.method,
                    headers={k:v for k,v in req.headers.items() if k in ('content-type','origin','x-camera-token','x-camera-session')},
                    data=req.post_data_buffer,base_url=URL)
                body=response.get_data(); headers=dict(response.headers); status=response.status_code; response.close()
                if req.url.endswith('/api/recognize'):
                    state['requests']+=1
                    await asyncio.sleep(state['delay'])
                try: await route.fulfill(status=status, headers=headers, body=body)
                except Exception:
                    if not page.is_closed(): raise

            await context.route(URL+'/**', route)
            await page.goto(URL+'/attendance')
            await page.locator('#attendance-class').select_option(str(classroom['id']))
            await page.locator('#session-name').fill('Buổi thử nhãn camera')
            await page.click('#begin-session')
            assert await page.locator('.candidates-card,#candidate-list').count()==0
            await page.wait_for_function('window.attendanceBridge?.canStart() && !document.getElementById("start-camera").disabled')
            await page.click('#start-camera')
            await page.wait_for_function('window.cameraDiagnostics.live_labels?.some(t=>t.lines.length===0)')
            checks['waiting_has_no_assigned_name']=True
            await page.wait_for_function('window.cameraDiagnostics.live_labels?.filter(t=>t.lines.length===3).length===2')
            labels=await page.evaluate('window.cameraDiagnostics.live_labels')
            assert [t['lines'][0] for t in labels]==[p['full_name'] for p in people[:2]]
            assert all('chưa xác nhận' in t['lines'][2] for t in labels)
            assert all(people[i]['student_code'] in t['lines'][1] and 'cosine' in t['lines'][1] for i,t in enumerate(labels))
            assert app.extensions['attendance_service'].detail(1)['stats']['present']==0
            checks['two_independent_names_codes_scores_without_auto_attendance']=True
            await page.locator('[data-confirm-student]').first.click()
            await page.wait_for_function('window.cameraDiagnostics.live_labels?.some(t=>t.lines.includes("Đã điểm danh"))')
            assert app.extensions['attendance_service'].detail(1)['stats']['present']==1
            checks['label_changes_only_after_manual_sqlite_confirmation']=True
            await page.locator('#mirror-camera').check()
            assert await page.locator('#camera-video').evaluate('(v)=>getComputedStyle(v).transform')=='matrix(-1, 0, 0, 1, 0, 0)'
            await page.set_viewport_size(dict(width=800,height=950))
            await page.wait_for_function('window.cameraDiagnostics.live_labels?.length===2')
            await page.wait_for_timeout(200)
            geometry=await page.evaluate("""() => {
              const b=document.getElementById('live-overlay').getBoundingClientRect();
              return {width:b.width,height:b.height,box:cameraDiagnostics.live_labels[0].box};
            }""")
            scale=min(geometry['width']/640,geometry['height']/480)
            assert abs(geometry['box']['x']-((geometry['width']-640*scale)/2+460*scale))<1
            assert abs(geometry['box']['y']-((geometry['height']-480*scale)/2+120*scale))<1
            assert await page.locator('#live-overlay').evaluate('(c)=>Math.abs(c.width-c.getBoundingClientRect().width*devicePixelRatio)<2')
            await page.screenshot(path=str(ROOT/'.venv/live_labels_synthetic.png'), full_page=True, scale='css')
            checks['mirror_resize_and_high_dpi_coordinates']=True
            # Same count and same box, different appearance while a response is in flight.
            state['delay']=2
            await page.wait_for_function('window.cameraDiagnostics.identity_active===1')
            await page.evaluate('paintFace(true)')
            await page.wait_for_function('window.cameraDiagnostics.live_labels?.[0]?.lines.length===1')
            await page.wait_for_timeout(700)
            assert not (await page.evaluate('window.cameraDiagnostics.live_labels'))[0]['lines'][0]==people[0]['full_name']
            checks['replacement_clears_old_name_before_slow_response']=True
            state['count']=0
            await page.wait_for_function('window.cameraDiagnostics.live_labels?.length===0')
            checks['no_faces_clears_all_names']=True
            state['count']=1;state['unknown']=True;state['delay']=.25
            await page.wait_for_function('window.cameraDiagnostics.live_labels?.some(t=>t.lines.length===0)',timeout=20000)
            checks['unknown_candidate_has_no_student_name']=True
            state['unknown']=False
            await page.wait_for_function('window.cameraDiagnostics.live_labels?.some(t=>t.lines.length===3)', timeout=20000)
            checks['one_face_reacquired_only_after_new_result']=True
            await page.wait_for_function('window.cameraDiagnostics.identity_active===0')
            state['delay']=2
            await page.wait_for_function('window.cameraDiagnostics.identity_active===1')
            await page.wait_for_timeout(100)
            await page.click('#stop-camera')
            await page.wait_for_function('!document.getElementById("camera-video").srcObject')
            await page.wait_for_timeout(2500)
            assert await page.evaluate('window.cameraDiagnostics.live_labels.length')==0
            checks['stop_ignores_late_replies']=True

            # Pure tracker tests cover ordering, ambiguous matching, gaps, expiry,
            # aspect change and invalidated tracks independent of request timing.
            result=await page.evaluate("""() => {
              const check=(x,m)=>{if(!x)throw Error(m);};
              const c=document.createElement('canvas');c.width=640;c.height=480;
              const ctx=c.getContext('2d');ctx.fillStyle='#224488';ctx.fillRect(0,0,640,480);
              const frame={width:640,height:480},a={box:{x:80,y:120,w:100,h:120}},b={box:{x:320,y:120,w:100,h:120}};
              const f=(box,id)=>({...box,top1:{student:{id},similarity:.8},eligible_for_confirmation:true});
              const p=SafeFaceTracks.project({x:.125,y:.25,w:.15625,h:.25},640,480,600,500);
              const q=SafeFaceTracks.project({x:.125,y:.25,w:.15625,h:.25},1280,960,600,500);
              check(JSON.stringify(p)===JSON.stringify(q),'resolution scaling');
              const t=new SafeFaceTracks();t.update([a,b],frame,c,0);let ticket=t.ticket(c,1);
              t.update([a,b],frame,c,100);t.apply([f(b,2),f(a,1)],frame,ticket,101);
              check(t.tracks[0].candidate.top1.student.id===1&&t.tracks[1].candidate.top1.student.id===2,'reversed order');
              t.visible(c,2700);check(t.tracks.every(x=>!x.candidate),'expired');
              t.reset();t.update([a],frame,c,0);ticket=t.ticket(c,1);
              // A slow inference must survive stable preview observations.
              for(let at=1000;at<=14000;at+=1000)t.update([a],frame,c,at);
              t.apply([f(a,1)],frame,ticket,14001);
              check(t.visible(c,14002)[0].candidate?.top1.student.id===1,'slow inference stable track');
              t.update([a],frame,c,25000); // gap invalidates instead of reviving a name
              check(!t.tracks[0].candidate,'gap resets');
              t.reset();t.update([a],frame,c,0);ticket=t.ticket(c,1);t.apply([f(a,1)],frame,ticket,2);
              for(let at=1000;at<=21000;at+=1000)t.update([a],frame,c,at);
              check(t.visible(c,21001)[0].stale&&t.tracks[0].candidate,'stale clearly marked');
              ctx.fillStyle='#2d4f93';ctx.fillRect(0,0,640,480);
              t.update([a],frame,c,21002);check(t.tracks[0].candidate,'modest exposure preserves track');
              ctx.fillStyle='#224488';ctx.fillRect(0,0,640,480);
              t.update([a],frame,c,3000);ticket=t.ticket(c,3001);t.update([],frame,c,3010);t.update([a],frame,c,3020);t.apply([f(a,1)],frame,ticket,3030);
              check(!t.tracks[0].candidate,'lost and returned stale ticket');
              ticket=t.ticket(c,3031);ctx.fillStyle='#cc3300';ctx.fillRect(0,0,640,480);t.visible(c,3032);
              ctx.fillStyle='#224488';ctx.fillRect(0,0,640,480);t.apply([f(a,1)],frame,ticket,3033);check(!t.tracks[0].candidate,'invalid stays invalid');
              t.reset();t.update([a,b],frame,c,4000);ticket=t.ticket(c,4001);t.apply([f(a,1),f(a,2)],frame,ticket,4002);check(t.tracks.every(x=>!x.candidate),'ambiguous');
              t.reset();t.update([a],frame,c,5000);ticket=t.ticket(c,5001);t.update([a],{width:640,height:360},c,5010);t.apply([f(a,1)],frame,ticket,5020);check(!t.tracks[0].candidate,'aspect reset');
              t.reset();t.apply([f(a,1)],frame,ticket,5030);check(t.tracks.length===0,'stop reset');
              return true;
            }""")
            assert result; checks['tracker_reordering_ambiguity_loss_expiry_aspect_and_late_tickets']=True
            api_report=ROOT/'outputs/overlay_api_check.json'
            if api_report.exists():
                from tests.camera_live_api_qa import payload
                actual=json.loads(api_report.read_text(encoding='utf-8'))
                gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
                labels=sorted({r['student_id'] for r in gallery})
                fixture=[]
                for i in range(2):
                    data,_=payload(Path(next(r for r in gallery if r['student_id']==labels[i])['image_path']))
                    fixture.append(dict(image='data:image/jpeg;base64,'+base64.b64encode(data).decode(),
                        **actual['cases'][i]))
                await page.evaluate("""async cases => {
                  const check=(v,m)=>{if(!v)throw Error(m);};
                  const images=await Promise.all(cases.map(async r=>{const im=new Image();im.src=r.image;await im.decode();return im;}));
                  const c=document.createElement('canvas'),frame=cases[0].recognition.frame;c.width=frame.width;c.height=frame.height;
                  const x=c.getContext('2d');x.drawImage(images[0],0,0,c.width,c.height);
                  const t=new SafeFaceTracks();t.update(cases[0].preview.faces,frame,c,0);const ticket=t.ticket(c,1);
                  for(let at=1000;at<=14000;at+=1000)t.update(cases[0].preview.faces,frame,c,at);
                  t.apply(cases[0].recognition.faces,frame,ticket,14001);
                  check(t.visible(c,14002)[0].candidate?.top1.student.id===cases[0].recognition.faces[0].top1.student.id,'real API student binding');
                  x.filter='brightness(1.05)';x.drawImage(images[0],0,0,c.width,c.height);x.filter='none';
                  t.update(cases[0].preview.faces,frame,c,14003);
                  check(!!t.visible(c,14004)[0].candidate,'real face exposure continuity');
                  x.drawImage(images[1],0,0,c.width,c.height);t.visible(c,14005);
                  check(!t.tracks[0].candidate,'real different-person replacement');
                }""",fixture)
                checks['real_api_json_and_photo_binding_slow_reply_exposure_and_replacement']=True
            assert not errors, errors; checks['no_javascript_errors']=True
            await browser.close()
    assert protected()==before, 'Production dataset/config/gallery/SQLite changed'
    checks['original_dataset_embeddings_threshold_and_sqlite_unchanged']=True
    report=dict(status='passed', kind='synthetic_camera_mock_ai_real_flask_disposable_sqlite', checks=checks,
        physical_people_tested=False, production_attendance_written=False)
    (ROOT/'outputs/live_labels_tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
