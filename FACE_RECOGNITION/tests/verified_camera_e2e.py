"""VERIFIED protocol fixture -> real HTTP -> real SQLite -> real camera.js.

NO biometric verification claim. Synthetic evidence exists ONLY in a temporary
directory and is NEVER published or copied into production outputs policy files.
"""
import asyncio
import base64
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import requests
from playwright.async_api import async_playwright
from werkzeug.serving import make_server

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.face_recognition.verification_workflow import write,digest
from backend.database.db import ManagementStore
from backend.attendance.attendance_service import AttendanceService
from tests.camera_live_api_qa import payload
from tests.real_ai_execution_benchmark import protected

PROFILE='retinaface_scaled_experimental'


class VerifiedProtocolFixture:
    """Test double at Recognition boundary. Never used by the production server."""
    def __init__(self,face,policy):
        self.face=face;self.matcher=SimpleNamespace(signature='protocol-fixture-not-biometric',policy=policy)
        self.mode='verified';self.state='ready';self.calls=0;self.delay=2.41
    def configure_store(self,store):self._gallery_revision=store.gallery_revision()
    def start(self):pass
    def health(self):return dict(ready=True,state='ready',registered_embeddings=92,registered_students=5,model_load_count=0)
    def detect_preview(self,bgr,cancelled):
        return dict(faces=[dict(face_id=1,box=self.face['box'])] if self.mode!='blank' else [],frame=dict(width=bgr.shape[1],height=bgr.shape[0]),detected_faces=0 if self.mode=='blank' else 1,processing_ms=1,timings_ms={})
    def analyze(self,bgr,cancelled=lambda:False,profile=PROFILE,allowed_student_ids=None):
        self.calls+=1;time.sleep(self.delay)
        f=copy.deepcopy(self.face);f.pop('tracking',None)
        match=dict(decision='candidate_passes_gates_manual_review_required' if self.mode=='verified' else 'unconfirmed',policy_applied=self.mode=='verified')
        f['identity_confirmed']=self.automatic_verifier.verify(match,self.matcher.signature,profile,self.matcher.policy)
        f['outside_class']=f['top1']['student']['id'] not in (allowed_student_ids or [])
        f['eligible_for_confirmation']=not f['outside_class']
        f['decision']='verified_for_automatic_attendance' if f['identity_confirmed'] else 'unconfirmed'
        f['manual_review_required']=not f['identity_confirmed']
        faces=[] if self.mode=='blank' else [f]
        return dict(faces=faces,detected_faces=len(faces),frame=dict(width=bgr.shape[1],height=bgr.shape[0]),face_errors=0,
            processing_ms=self.delay*1000,timings_ms={},profile=profile,identity_confirmation_enabled=f['identity_confirmed'])


async def run():
    before=protected();report=dict(test_kind='VERIFIED protocol fixture, NOT biometric evidence',physical_camera_tested=False,manual_confirm_requests=0)
    with tempfile.TemporaryDirectory(dir=ROOT/'.venv',prefix='verified-e2e-') as tmp:
        tmp=Path(tmp);db=tmp/'copy.sqlite3'
        with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as src,closing(sqlite3.connect(db)) as dst:src.backup(dst)
        face=copy.deepcopy(json.loads((ROOT/'outputs/real_auto_attendance_trace.json').read_text(encoding='utf-8'))['linh']['faces'][0])
        evidence=tmp/'FIXTURE_evaluation.json';activation=tmp/'FIXTURE_activation.json'
        keys=['unknown_false_accept_rate','known_false_rejection_rate','known_wrong_identity_rate']
        fake=dict(pipeline_signature='protocol-fixture-not-biometric',camera_profile=PROFILE,independent_holdout=True,approved_for_automatic_attendance=True,
            min_similarity=.8,min_margin=.3,acceptance_targets={k:.1 for k in keys},upper95={k:.01 for k in keys},known_samples=1000,unknown_samples=1000,
            fixture_only_not_real_evaluation=True,**{k:0 for k in keys})
        write(evidence,fake)
        policy=dict(status='official',enabled=True,pipeline_signature=fake['pipeline_signature'],min_similarity=.8,min_margin=.3,
            evidence=dict(labelled_known_validation_samples=1000,labelled_unknown_validation_samples=1000,independent_evaluation_completed=True,
                test_used_for_threshold_optimization=False,automatic_evaluation_sha256=digest(evidence)))
        engine=VerifiedProtocolFixture(face,policy)
        app=create_app(engine,False,dict(DATABASE_PATH=db,AUTO_ATTENDANCE_ENABLED=False,AUTO_ATTENDANCE_EVIDENCE=evidence,AUTO_ATTENDANCE_ACTIVATION=activation))
        store=app.extensions['management_store'];attendance=app.extensions['attendance_service']
        people=store.list_entities('students');linh=next(p for p in people if p['student_code']=='DEMO-SV001');partner=next(p for p in people if p['student_code']=='DEMO-SV003')
        classroom=store.save('classes',dict(class_code='VERIFIED-E2E',class_name='Protocol fixture only'))
        for p in (linh,partner):store.membership(classroom['id'],p['id'])
        sid=attendance.create(dict(class_id=classroom['id'],session_name='VERIFIED fixture only',request_key='verified-e2e'))['id']
        gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
        sample=Path(next(r['image_path'] for r in gallery if r['student_id']==linh['full_name']));data,_=payload(sample)
        server=make_server('127.0.0.1',5055,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:5055'
        try:
            page_html=requests.get(base+'/attendance').text
            token=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',page_html).group(1))['csrfToken']
            headers={'X-Camera-Token':token}
            status=requests.get(base+'/api/attendance/automation-status?profile='+PROFILE).json();assert status['can_enable'] and not status['requested']
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--disable-gpu','--renderer-process-limit=1'])
                context=await browser.new_context(viewport=dict(width=1366,height=1000))
                await context.add_init_script("""(() => {const c=document.createElement('canvas');c.width=640;c.height=480;const x=c.getContext('2d');window.paintFixture=async u=>{const im=new Image();im.src=u;await im.decode();x.drawImage(im,0,0,640,480);};navigator.mediaDevices.getUserMedia=async()=>c.captureStream(15);setInterval(()=>{x.fillStyle='#eee';x.fillRect(0,0,1,1)},100);})();""")
                page=await context.new_page();errors=[];commits=[];manual=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:manual.append(r.url) if r.url.endswith('/confirm') else None)
                page.on('response',lambda r:commits.append(r.status) if r.url.endswith('/attendance/commit') else None)
                await page.goto(base+f'/attendance?session={sid}')
                await page.wait_for_function('document.querySelector("#automatic-mode")&&!document.querySelector("#automatic-mode").disabled')
                await page.click('#automatic-mode')
                await page.wait_for_function('cameraDiagnostics.automatic_attendance.ready===true')
                await page.evaluate('u=>paintFixture(u)','data:image/jpeg;base64,'+base64.b64encode(data).decode())
                await page.click('#start-camera')
                await page.wait_for_function('document.querySelector("[data-count=present]").textContent==="1"',timeout=60000)
                assert engine.calls>=2 and 200 in commits and not manual
                assert await page.locator('[data-count=total]').text_content()=='2'
                await page.wait_for_function('cameraDiagnostics.live_labels.some(t=>t.lines.some(l=>l.includes("Đã điểm danh")))')
                details=attendance.detail(sid);record=next(r for r in details['records'] if r['student_id']==linh['id'])
                assert record['attendance_status']=='PRESENT' and record['recognition_source']=='AI_VERIFIED'
                report.update(automatic_commit_http=True,sqlite_source='AI_VERIFIED',frontend_zero_of_two_to_one_of_two=True,
                    overlay_attended=True,two_consistent_recognition_results=True,inference_seconds=2.41,sqlite_record=record)
                await page.click('#stop-camera');await page.reload()
                await page.wait_for_function('document.querySelector("[data-count=present]")?.textContent==="1"')
                assert not errors,errors;await browser.close()
            # API negative cases and SQL idempotency, all against the same temporary DB.
            def camera():return requests.post(base+'/api/camera/sessions',headers=headers,json=dict(profile=PROFILE,attendance_session_id=sid)).json()['session_id']
            cs=camera();fh=dict(headers,**{'X-Camera-Session':cs,'Content-Type':'image/jpeg'})
            engine.delay=.01
            def recognize():
                requests.post(base+'/api/detect',headers=fh,data=data).raise_for_status()
                r=requests.post(base+'/api/recognize',headers=fh,data=data);r.raise_for_status();return r.json()
            def commit(grants):return requests.post(base+f'/api/camera/sessions/{cs}/attendance/commit',headers=headers,json={'grant_ids':[g['grant_id'] for g in grants]})
            assert not recognize()['automatic_attendance']['grants']
            grants=recognize()['automatic_attendance']['grants'];assert grants
            result=commit(grants);assert result.status_code==200 and not result.json()['changed']
            assert commit(grants).status_code==409
            engine.mode='unknown';assert not recognize()['automatic_attendance']['grants']
            engine.mode='wrong_candidate';assert not recognize()['automatic_attendance']['grants']
            engine.mode='verified';old=copy.deepcopy(engine.face['top1']['student'])
            outsider=next(p for p in people if p['student_code']=='DEMO-SV002');engine.face['top1']['student']=dict(old,id=outsider['id'])
            assert not recognize()['automatic_attendance']['grants'];assert not recognize()['automatic_attendance']['grants']
            engine.face['top1']['student']=old;assert not recognize()['automatic_attendance']['grants']
            engine.face['top1']['student']=dict(old,id=partner['id']);assert not recognize()['automatic_attendance']['grants']
            engine.face['top1']['student']=old;assert not recognize()['automatic_attendance']['grants']
            grants=recognize()['automatic_attendance']['grants'];assert grants
            engine.mode='blank';requests.post(base+'/api/detect',headers=fh,data=data)
            assert commit(grants).status_code==409
            engine.mode='verified';recognize();grants=recognize()['automatic_attendance']['grants'];assert grants
            time.sleep(3.1);assert commit(grants).status_code==409
            recognize();grants=recognize()['automatic_attendance']['grants'];assert grants
            # A new request invalidates receipts from an older request sequence.
            recognize();assert commit(grants).status_code==409
            requests.delete(base+'/api/camera/sessions/'+cs,headers=headers);assert commit(grants).status_code==409
            assert attendance.detail(sid)['stats']==dict(total=2,present=1,pending=1,absent=0)
            reopened=AttendanceService(ManagementStore(db,ROOT/'outputs/face_embeddings.json'));assert reopened.detail(sid)['stats']['present']==1
            report.update(no_manual_requests=True,no_duplicate=True,unknown_rejected=True,wrong_candidate_rejected=True,outside_class_rejected=True,
                lost_track_rejected=True,expired_receipt_rejected=True,old_request_rejected=True,stopped_camera_rejected=True,restart_persistence=True)
            report['changed_identity_resets_consistency']=True
            disabled=requests.post(base+'/api/attendance/automation-mode',headers=headers,json=dict(enabled=False,profile=PROFILE));assert disabled.status_code==200 and not disabled.json()['requested']
            fake['upper95']['unknown_false_accept_rate']=.5;write(evidence,fake)
            engine.matcher.policy['evidence']['automatic_evaluation_sha256']=digest(evidence)
            blocked=requests.post(base+'/api/attendance/automation-mode',headers=headers,json=dict(enabled=True,profile=PROFILE))
            assert blocked.status_code==409
            report['bad_confidence_bound_cannot_enable']=True
        finally:server.shutdown()
    assert protected()==before
    report.update(status='passed',production_data_unchanged=True,production_automatic_enabled=False)
    write(ROOT/'outputs/verified_camera_e2e.json',report);print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=='__main__':asyncio.run(run())
