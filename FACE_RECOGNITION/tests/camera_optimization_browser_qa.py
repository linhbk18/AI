"""Deterministic newest-frame/stop/profile tests using synthetic browser media."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys

from PIL import Image
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:5055'


async def run():
    checks={}
    state=dict(count=0,active=0,max_active=0,session_options=[],sizes=[])
    arrived=[asyncio.Event() for _ in range(4)]
    releases=[asyncio.Event() for _ in range(4)]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',
                                      headless=True,args=['--use-fake-device-for-media-stream'])
        context=await browser.new_context(viewport=dict(width=1440,height=1050))
        await context.grant_permissions(['camera'],origin=URL)
        page=await context.new_page()
        js_errors=[]
        page.on('pageerror',lambda exc:js_errors.append(str(exc)))
        async def route_api(route):
            request=route.request
            path=request.url.removeprefix(URL)
            if path=='/api/health':
                await route.fulfill(json=dict(application='face_recognition_camera_v1',ready=True,state='ready',
                    registered_embeddings=92,registered_students=5,attendance_enabled=False));return
            if path=='/api/camera/sessions':
                state['session_options'].append(request.post_data_json)
                await route.fulfill(json=dict(session_id='optimization-test-session'));return
            if path.startswith('/api/camera/sessions/'):
                await route.fulfill(json=dict(stopped=True));return
            if path!='/api/recognize': await route.continue_();return
            i=state['count'];state['count']+=1
            assert i<4,'Unexpected queued frame'
            state['active']+=1;state['max_active']=max(state['max_active'],state['active'])
            with Image.open(BytesIO(request.post_data_buffer)) as frame:
                width,height=frame.size
            state['sizes'].append([width,height]);arrived[i].set()
            await releases[i].wait()
            names=[] if i==2 else ['Synthetic A' if i==0 else 'Synthetic B' if i==1 else 'Late stale identity']
            faces=[dict(face_id=1,box=dict(x=20,y=30,w=90,h=110),top1=dict(candidate=name,similarity=.7),
                top2=dict(candidate='Synthetic other',similarity=.4),margin=.3,
                identity_confirmed=False,label='Ứng viên chưa xác nhận') for name in names]
            try:
                await route.fulfill(json=dict(faces=faces,detected_faces=len(faces),face_errors=0,
                    frame=dict(width=width,height=height),processing_ms=300,threshold_enabled=False,
                    identity_confirmation_enabled=False,attendance_enabled=False))
            except Exception:
                if i!=3: raise
            finally: state['active']-=1
        await page.route('**/api/**',route_api)
        await page.goto(URL,wait_until='networkidle')
        await page.wait_for_function("!document.getElementById('start-camera').disabled")
        assert await page.locator('#camera-profile').input_value()=='retinaface_reuse'
        assert await page.locator('option[value="yunet_960_experimental"]').count()==0
        checks['retinaface_default_and_rejected_experiment_not_in_ui']=True
        await page.select_option('#camera-profile','yunet_recall_experimental')
        await page.select_option('#processing-size','320x240')
        assert 'thử nghiệm' in await page.locator('#detector-name').text_content()
        await page.click('#start-camera')
        await asyncio.wait_for(arrived[0].wait(),15)
        assert state['session_options'][0]['profile']=='yunet_recall_experimental'
        checks['experimental_profile_is_explicit_per_session']=True
        t0=await page.locator('#camera-video').evaluate('v=>v.currentTime')
        await page.wait_for_timeout(700)
        assert state['count']==1 and state['max_active']==1
        assert await page.locator('#camera-video').evaluate('v=>v.currentTime')>t0
        assert await page.locator('.candidate-item').count()==0
        checks['live_video_continues_no_queued_frames_during_inference']=True
        assert all(w<=320 and h<=240 for w,h in state['sizes'])
        ratios=await page.evaluate("[document.getElementById('camera-video').videoWidth,document.getElementById('camera-video').videoHeight]")
        assert abs(state['sizes'][0][0]/state['sizes'][0][1]-ratios[0]/ratios[1])<.01
        checks['resolution_cap_and_aspect_preserved']=True
        releases[0].set()
        await page.wait_for_function("document.querySelector('.candidate-name')?.textContent==='Synthetic A'")
        await asyncio.wait_for(arrived[1].wait(),10)
        assert await page.locator('.candidate-item').count()==0
        checks['old_identity_cleared_on_next_capture']=True
        releases[1].set()
        await page.wait_for_function("document.querySelector('.candidate-name')?.textContent==='Synthetic B' && !document.getElementById('candidate-list').textContent.includes('Synthetic A')")
        checks['person_switch_replaces_candidate']=True
        await asyncio.wait_for(arrived[2].wait(),10)
        releases[2].set()
        await page.wait_for_function("document.getElementById('frame-count').textContent==='3' && document.querySelectorAll('.candidate-item').length===0")
        checks['no_face_never_shows_identity']=True
        await asyncio.wait_for(arrived[3].wait(),10)
        await page.evaluate("window.__qaTrack=document.getElementById('camera-video').srcObject.getVideoTracks()[0]")
        await page.click('#stop-camera')
        releases[3].set()
        await page.wait_for_timeout(900)
        assert state['count']==4 and state['max_active']==1
        assert await page.evaluate("window.__qaTrack.readyState==='ended' && document.getElementById('camera-video').srcObject===null")
        assert await page.locator('.candidate-item').count()==0
        assert not await page.locator('#processed-frame').is_visible()
        checks['stop_suppresses_late_result_and_further_requests']=True
        assert not js_errors,js_errors
        checks['no_browser_javascript_error']=True
        await page.screenshot(path=str(ROOT/'.venv/optimization_camera_ui.png'),full_page=True)
        await context.close();await browser.close()
    result=dict(kind='synthetic_camera_ui_qa',checks=checks,requests=state['count'],max_in_flight=state['max_active'],
                frame_sizes=state['sizes'],camera_images_saved=False)
    (ROOT/'outputs/camera_optimization/browser_qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
