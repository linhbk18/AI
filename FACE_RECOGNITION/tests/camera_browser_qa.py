"""Browser UI QA using synthetic camera media; separately probe real hardware.

No captured camera images are saved. Screenshots are taken only with camera off.
Run with the project .venv Python while the local server is running.
"""
import asyncio
from io import BytesIO
import json
from pathlib import Path
import time

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:5055'
CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'


async def run():
    result = dict(kind='browser_ui_with_synthetic_media_and_mock_api', checks={}, real_camera=None)
    async with async_playwright() as p:
        # Real-device availability is a separate probe, not a staged recognition test.
        real = await p.chromium.launch(executable_path=CHROME, headless=True)
        context = await real.new_context(viewport={'width':1440,'height':1050})
        await context.grant_permissions(['camera'], origin=URL)
        page = await context.new_page()
        await page.goto(URL, wait_until='networkidle')
        await page.screenshot(path=str(ROOT/'.venv/camera_desktop.png'), full_page=True)
        result['real_camera'] = await page.evaluate("""async () => {
          let media = null, expired = false, timer;
          const pending = navigator.mediaDevices.getUserMedia({video:true,audio:false});
          pending.then(s => { if (expired) s.getTracks().forEach(t=>t.stop()); }).catch(()=>{});
          try {
            media = await Promise.race([pending,new Promise((_,reject)=>{
              timer=setTimeout(()=>{expired=true;reject(new Error('device_probe_timeout'));},15000);
            })]);
            const settings=media.getVideoTracks()[0].getSettings();
            return {opened:true,width:settings.width,height:settings.height,frames_saved:false,
                    physical_single_multi_person_scenarios_tested:false};
          } catch(e) {
            return {opened:false,error:e.name,message:e.message,frames_saved:false,
                    physical_single_multi_person_scenarios_tested:false};
          } finally { clearTimeout(timer); if(media)media.getTracks().forEach(t=>t.stop()); }
        }""")
        await context.close(); await real.close()

        browser = await p.chromium.launch(executable_path=CHROME, headless=True,
                                          args=['--use-fake-device-for-media-stream'])
        context = await browser.new_context(viewport={'width':1440,'height':1050})
        await context.grant_permissions(['camera'], origin=URL)
        page = await context.new_page()
        errors=[]
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        state=dict(mode='one',requests=0,active=0,max_active=0,sessions=0,stops=0,frame_sizes=[])

        async def api(route):
            request=route.request
            path=request.url.split(URL,1)[-1]
            if path=='/api/health':
                await route.fulfill(json=dict(application='face_recognition_camera_v1',ready=True,state='ready',
                    registered_embeddings=92,registered_students=5,attendance_enabled=False))
                return
            if path=='/api/camera/sessions':
                state['sessions']+=1
                await route.fulfill(json=dict(session_id=f"synthetic-session-{state['sessions']}"))
                return
            if path.startswith('/api/camera/sessions/'):
                state['stops']+=1
                await route.fulfill(json=dict(stopped=True))
                return
            if path!='/api/recognize':
                await route.continue_(); return
            state['requests']+=1
            state['active']+=1
            state['max_active']=max(state['max_active'],state['active'])
            mode=state['mode']
            with Image.open(BytesIO(request.post_data_buffer)) as image:
                width,height=image.size
            state['frame_sizes'].append([width,height])
            try:
                await asyncio.sleep(2.3 if mode in ('slow','late') else .35)
                if mode=='network_error':
                    await route.abort('failed'); return
                if mode=='backend_error':
                    await route.fulfill(status=500,json=dict(error=dict(code='INFERENCE_FAILED',message='Backend thử nghiệm gặp lỗi.')))
                    return
                names=[] if mode=='none' else ['Sinh viên A','Sinh viên B'] if mode=='two' else ['Sinh viên C'] if mode=='other' else ['Sinh viên A']
                faces=[]
                for i,name in enumerate(names,1):
                    faces.append(dict(face_id=i,box=dict(x=40+(i-1)*210,y=50,w=120,h=140),
                        top1=dict(candidate=name,similarity=.7),top2=dict(candidate='Ứng viên thứ hai',similarity=.4),
                        margin=.3,identity_confirmed=False,label='Ứng viên chưa xác nhận',manual_review_required=True))
                await route.fulfill(json=dict(faces=faces,detected_faces=len(faces),face_errors=0,
                    frame=dict(width=width,height=height),processing_ms=2300 if mode in ('slow','late') else 350,
                    threshold_enabled=False,identity_confirmation_enabled=False,attendance_enabled=False))
            except Exception:
                if mode!='late': raise
            finally:
                state['active']-=1

        await page.route('**/api/**',api)
        await page.goto(URL,wait_until='networkidle')
        await page.wait_for_function("!document.getElementById('start-camera').disabled")
        await page.click('#start-camera')
        await page.wait_for_function("document.querySelector('.candidate-name')?.textContent==='Sinh viên A'")
        assert await page.evaluate("document.getElementById('camera-video').videoWidth>0")
        result['checks']['synthetic_camera_opens_and_auto_frames_without_capture_button']=True
        result['checks']['single_face_candidate_unconfirmed']=await page.locator('.candidate-label').text_content()=='Ứng viên chưa xác nhận'
        state['mode']='two'
        await page.wait_for_function("document.querySelectorAll('.candidate-item').length===2")
        result['checks']['multiple_faces_independent_cards']=await page.locator('.candidate-name').all_text_contents()==['Sinh viên A','Sinh viên B']

        # Letterbox the live video deliberately; verify the canvas includes that offset.
        await page.locator('#video-stage').evaluate("e=>e.style.aspectRatio='1 / 1'")
        await page.wait_for_timeout(250)
        mapped=await page.evaluate("""() => {
          const canvas=document.getElementById('live-overlay'), r=canvas.getBoundingClientRect();
          const video=document.getElementById('camera-video');
          const fw=640,fh=Math.round(video.videoHeight*640/video.videoWidth);
          const scale=Math.min(r.width/fw,r.height/fh), ox=(r.width-fw*scale)/2,oy=(r.height-fh*scale)/2;
          const dpr=window.devicePixelRatio||1, x=Math.round((ox+40*scale)*dpr),y=Math.round((oy+120*scale)*dpr);
          const data=canvas.getContext('2d').getImageData(x-2,y-2,5,5).data;
          return {letterbox:oy>0,painted:Array.from(data).some((_,i)=>i%4===3&&data[i]>0)};
        }""")
        assert mapped['letterbox'] and mapped['painted'],mapped
        result['checks']['letterboxed_coordinate_mapping']=True
        await page.locator('#video-stage').evaluate("e=>e.style.aspectRatio='16 / 9'")

        state['mode']='other'
        await page.wait_for_function("document.querySelector('.candidate-name')?.textContent==='Sinh viên C'")
        result['checks']['candidate_changes_and_old_identity_removed']=await page.locator('.candidate-name').all_text_contents()==['Sinh viên C']
        state['mode']='none'
        await page.wait_for_function("document.querySelectorAll('.candidate-item').length===0 && document.getElementById('frame-count').textContent!=='0'")
        result['checks']['no_face_no_identity']=await page.locator('#face-count').text_content()=='0'
        assert state['max_active']==1
        assert all(max(size)<=640 for size in state['frame_sizes'])
        result['checks']['one_request_in_flight_and_frame_cap']=True

        state['mode']='slow'
        await page.wait_for_function("document.getElementById('message').textContent.includes('xử lý chậm')")
        alpha=await page.evaluate("""()=>{
          const c=document.getElementById('live-overlay'),a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
          return Array.from(a).some((_,i)=>i%4===3&&a[i]>0);
        }""")
        assert not alpha
        assert await page.locator('#processed-frame').is_visible()
        result['checks']['slow_result_only_on_matching_processed_frame']=True

        # Stop while a request is in flight, then ensure no new frames or late identities.
        state['mode']='late'
        old=state['requests']
        await page.wait_for_function("document.getElementById('capture-state').textContent.includes('Đang xử lý')")
        await page.evaluate("window.__qaTrack=document.getElementById('camera-video').srcObject.getVideoTracks()[0]")
        await page.click('#stop-camera')
        await page.wait_for_timeout(2700)
        count=state['requests']
        await page.wait_for_timeout(1400)
        assert state['requests']==count
        assert await page.evaluate("window.__qaTrack.readyState==='ended' && document.getElementById('camera-video').srcObject===null")
        assert await page.locator('.candidate-item').count()==0
        assert not await page.locator('#processed-frame').is_visible()
        result['checks']['stop_tracks_requests_and_late_results']=True

        state['mode']='backend_error'
        await page.click('#start-camera')
        await page.wait_for_function("document.getElementById('message').dataset.level==='error' && document.getElementById('camera-state').textContent==='Đã tắt'")
        assert 'Backend' in await page.locator('#message').text_content()
        result['checks']['backend_http_error_visible_and_camera_stopped']=True
        state['mode']='network_error'
        await page.click('#start-camera')
        await page.wait_for_function("document.getElementById('message').textContent.includes('Mất kết nối backend')")
        result['checks']['backend_disconnect_visible']=True
        assert await page.locator('.candidate-item').count()==0

        # No live camera screenshot is captured; all media are now stopped.
        await page.set_viewport_size(dict(width=390,height=844))
        await page.screenshot(path=str(ROOT/'.venv/camera_mobile.png'),full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        result['checks']['mobile_no_horizontal_overflow']=True
        result['checks']['no_browser_javascript_errors']=not errors
        assert not errors,errors
        result['synthetic_requests']=state['requests']
        result['max_simultaneous_normal_requests']=1
        await context.close()

        denied=await browser.new_context()
        page=await denied.new_page()
        await page.route('**/api/**',api)
        await page.add_init_script("""Object.defineProperty(navigator.mediaDevices,'getUserMedia',{
          value:async()=>{throw new DOMException('denied for UI test','NotAllowedError');}
        });""")
        await page.goto(URL,wait_until='networkidle')
        await page.wait_for_function("!document.getElementById('start-camera').disabled")
        await page.click('#start-camera')
        await page.wait_for_function("document.getElementById('message').textContent.includes('Quyền camera đã bị từ chối')")
        result['checks']['camera_permission_denial_explained']=True
        await denied.close(); await browser.close()
    path=ROOT/'.venv/camera_browser_results.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(run())
