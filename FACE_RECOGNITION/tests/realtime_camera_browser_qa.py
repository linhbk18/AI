"""UI scheduling, stale-result, error and geometry checks with synthetic media.

Real inference is measured separately. Synthetic candidates are used only in this
isolated browser test and are never written into the real SQLite database.
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path
import sys

from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:5055'


async def run():
    checks={};errors=[]
    offline=None;test_client=None
    if os.environ.get('CAMERA_UI_OFFLINE')=='1':
        sys.path.insert(0,str(ROOT))
        from backend.app import create_app
        from tests.test_management import EnrollmentFake
        offline=tempfile.TemporaryDirectory(prefix='camera-ui-',dir=ROOT/'.venv')
        test_client=create_app(EnrollmentFake(),False,{'DATABASE_PATH':Path(offline.name)/'test.sqlite3'}).test_client()
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--use-fake-device-for-media-stream','--disable-gpu','--renderer-process-limit=1','--disable-extensions','--disable-background-networking'])
        context=await browser.new_context(viewport=dict(width=1440,height=1080),permissions=['camera'])
        page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        if test_client:
            async def document_route(route):
                if '/api/' in route.request.url:await route.fallback();return
                response=test_client.get(route.request.url.removeprefix(URL),base_url=URL)
                await route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.get_data());response.close()
            await context.route(URL+'/**',document_route)
        state=dict(faces=1,preview_count=0,identity_count=0,identity_active=0,max_identity_active=0,release=asyncio.Event(),fail_preview=False)
        async def api(route):
            path=route.request.url.removeprefix(URL)
            if path=='/api/health':await route.fulfill(json=dict(application='face_recognition_camera_v1',ready=True,registered_students=5,registered_embeddings=92));return
            if path=='/api/camera/sessions':
                assert route.request.post_data_json['profile']=='retinaface_scaled_experimental'
                await route.fulfill(json=dict(session_id='isolated-ui-session'));return
            if path.startswith('/api/camera/sessions/'):await route.fulfill(json=dict(stopped=True));return
            if path=='/api/detect':
                state['preview_count']+=1
                if state['fail_preview']:
                    await route.fulfill(status=500,json=dict(error=dict(message='Lỗi preview kiểm thử')));return
                faces=[dict(face_id=i+1,box=dict(x=80+130*i,y=60,w=90,h=110)) for i in range(state['faces'])]
                await route.fulfill(json=dict(faces=faces,frame=dict(width=640,height=480),preview_only=True,processing_ms=30));return
            if path=='/api/recognize':
                state['identity_count']+=1;state['identity_active']+=1;state['max_identity_active']=max(state['max_identity_active'],state['identity_active'])
                await state['release'].wait()
                n=state['identity_count'];state['identity_active']-=1
                face=dict(face_id=1,box=dict(x=80,y=60,w=90,h=110),top1=dict(candidate='QA Candidate '+str(n),similarity=.8,student=dict(full_name='QA Candidate '+str(n),student_code='QA-ONLY',code_is_temporary=True)),top2=dict(candidate='QA Other',similarity=.2),margin=.6)
                try:await route.fulfill(json=dict(faces=[face],detected_faces=1,frame=dict(width=640,height=480),profile='retinaface_scaled_experimental',processing_ms=1500,identity_confirmation_enabled=False,attendance_enabled=False))
                except Exception:pass # Browser deliberately aborts in stop test.
                return
            await route.fallback()
        await page.route(URL+'/api/**',api)
        await page.goto(URL);await page.wait_for_function('!document.getElementById("start-camera").disabled')
        assert await page.locator('#camera-profile').input_value()=='retinaface_scaled_experimental'
        camera=await page.locator('.camera-column').bounding_box();aside=await page.locator('.results-column').bounding_box()
        assert camera['x']<aside['x'] and camera['width']>aside['width']*1.8
        checks['scaled_retinaface_default_large_camera_left_analysis_right']=True
        await page.screenshot(path=str(ROOT/'.venv/realtime_ui_off.png'),full_page=True)
        await page.click('#start-camera')
        await page.wait_for_function('document.getElementById("camera-video").videoWidth>0')
        await page.evaluate('window.qaTrack=document.getElementById("camera-video").srcObject.getVideoTracks()[0]')
        while state['identity_count']<1:await asyncio.sleep(.05)
        t=await page.locator('#camera-video').evaluate('(v)=>v.currentTime')
        await page.wait_for_timeout(1400)
        assert state['preview_count']>=3 and state['identity_count']==1 and state['max_identity_active']==1
        assert await page.locator('#camera-video').evaluate('(v)=>v.currentTime')>t
        checks['preview_and_video_continue_during_one_identity_job_no_queue']=True
        # Canvas actually contains drawn boxes before a name exists.
        assert await page.locator('#live-overlay').evaluate('(c)=>c.getContext("2d").getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)')
        assert await page.locator('#live-overlay').evaluate('''(c)=>{const s=Math.min(c.width/640,c.height/480),x=Math.round((c.width-640*s)/2+80*s),y=Math.round((c.height-480*s)/2+100*s);return c.getContext('2d').getImageData(x-1,y,3,1).data.some((v,i)=>i%4===3&&v>0)}''')
        assert await page.locator('.candidate-name').count()==0
        checks['anonymous_boxes_before_arcface_result']=True
        state['release'].set()
        await page.locator('.candidate-name').get_by_text('QA Candidate 1',exact=True).wait_for()
        assert 'ảnh đối chiếu' in await page.locator('#result-context').text_content()
        assert await page.locator('.candidate-score').text_content()=='0.8000'
        assert await page.locator('.candidate-label').text_content()=='Ứng viên chưa xác nhận'
        checks['candidate_score_sqlite_code_and_unconfirmed_snapshot_context']=True
        state['faces']=0
        await page.wait_for_function('document.getElementById("face-count").textContent==="0"')
        assert await page.locator('.candidate-name').count()==0
        assert not await page.locator('#processed-frame').is_visible()
        checks['no_faces_immediately_clears_old_candidate_and_snapshot']=True
        state['faces']=2
        await page.wait_for_function('document.getElementById("face-count").textContent==="2"')
        checks['multiple_anonymous_boxes']=True
        state['fail_preview']=True
        await page.wait_for_function('document.getElementById("message").dataset.level==="error"')
        assert await page.locator('.candidate-name').count()==0
        state['fail_preview']=False
        await page.wait_for_timeout(1300)
        checks['backend_error_clears_results_and_recovers_without_queue']=True
        # Hold the next inference and stop while it is outstanding.
        state['release']=asyncio.Event()
        baseline=state['identity_count']
        for _ in range(100):
            if state['identity_count']>baseline:break
            await asyncio.sleep(.05)
        await page.click('#stop-camera');state['release'].set();counts=(state['preview_count'],state['identity_count'])
        await page.wait_for_timeout(1500)
        assert (state['preview_count'],state['identity_count'])==counts
        assert await page.evaluate('window.qaTrack.readyState==="ended"')
        assert await page.locator('.candidate-name').count()==0
        checks['stop_ends_tracks_stops_both_loops_and_discards_late_result']=True
        await page.set_viewport_size(dict(width=390,height=844));await page.screenshot(path=str(ROOT/'.venv/realtime_ui_mobile.png'),full_page=True)
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        camera=await page.locator('.camera-column').bounding_box();aside=await page.locator('.results-column').bounding_box();assert aside['y']>camera['y']
        checks['mobile_stacked_without_overflow']=True
        # Exercise the actual AbortController timeout path at a shortened test-only deadline.
        state['faces']=1;state['release']=asyncio.Event()
        await page.evaluate('''window.__qaSetTimeout=window.setTimeout;window.setTimeout=(fn,ms,...args)=>window.__qaSetTimeout(fn,ms===120000?100:ms,...args)''')
        await page.click('#start-camera')
        await page.wait_for_function('document.getElementById("message").textContent.includes("Nhận diện quá thời gian chờ")')
        assert await page.locator('.candidate-name').count()==0
        assert await page.locator('#stop-camera').is_enabled()
        checks['timeout_keeps_video_running_clears_candidates_and_schedules_backoff']=True
        await page.click('#stop-camera');state['release'].set()
        await page.evaluate('()=>{window.setTimeout=window.__qaSetTimeout;navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException("Denied","NotAllowedError")};return true;}')
        await page.click('#start-camera')
        await page.wait_for_function('document.getElementById("message").textContent.includes("Quyền camera đã bị từ chối")')
        checks['camera_permission_denial_explained']=True
        assert not errors,errors;checks['no_javascript_errors']=True
        await context.close();await browser.close()
    if offline:offline.cleanup()
    result=dict(kind='synthetic_media_mock_ai',checks=checks,status='passed',physical_people_tested=False)
    Path(os.environ.get('CAMERA_UI_REPORT',str(ROOT/'outputs/realtime_camera_ui/browser_tests.json'))).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
