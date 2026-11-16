"""Open the available real browser camera for one HTTP inference, then stop.

No screenshot, video recording, raw frame, embedding or identity is persisted.
This does not stage or verify the identities/counts of physical people.
"""
import asyncio
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:5055'


async def run():
    result=dict(kind='actual_browser_camera_to_local_model',images_saved=False,
                staged_single_and_multiple_people=False,identity_ground_truth_available=False)
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--disable-gpu','--renderer-process-limit=1','--disable-extensions'])
        context=await browser.new_context()
        await context.grant_permissions(['camera'],origin=URL)
        page=await context.new_page()
        try:
            await page.goto(URL,wait_until='networkidle')
            await page.wait_for_function("!document.getElementById('start-camera').disabled",timeout=180000)
            await page.click('#start-camera')
            await page.wait_for_function("document.getElementById('camera-video').videoWidth>0",timeout=20000)
            await page.evaluate("window.__qaActualTrack=document.getElementById('camera-video').srcObject.getVideoTracks()[0]")
            result['camera_opened']=True
            result['video_size']=await page.evaluate("[document.getElementById('camera-video').videoWidth,document.getElementById('camera-video').videoHeight]")
            await page.wait_for_function("Number(document.getElementById('frame-count').textContent)>=1 || document.getElementById('message').dataset.level==='error'",timeout=130000)
            result['completed_frames']=int(await page.locator('#frame-count').text_content())
            face_text=await page.locator('#face-count').text_content()
            result['detected_faces_in_last_frame']=int(face_text) if face_text.isdigit() else None
            result['processing_time_display']=await page.locator('#latency').text_content()
            result['message']=await page.locator('#message').text_content()
            result['candidate_cards_all_unconfirmed']=all(label=='Ứng viên chưa xác nhận' for label in await page.locator('.candidate-label').all_text_contents())
            await page.click('#stop-camera')
            await page.wait_for_timeout(1000)
            result['tracks_ended_after_stop']=await page.evaluate("window.__qaActualTrack.readyState==='ended' && document.getElementById('camera-video').srcObject===null")
            result['identities_cleared_after_stop']=await page.locator('.candidate-item').count()==0
            result['status']='passed' if result['completed_frames']>=1 and result['tracks_ended_after_stop'] else 'failed'
        except Exception as exc:
            result.update(status='unverified',error=f'{type(exc).__name__}: {exc}')
            try: await page.click('#stop-camera',timeout=2000)
            except Exception: pass
        finally:
            await context.close(); await browser.close()
    (ROOT/'.venv/camera_physical_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(run())
