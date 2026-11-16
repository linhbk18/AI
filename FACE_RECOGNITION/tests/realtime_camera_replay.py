"""Real UI + real HTTP AI using a clearly labelled dataset video replay.

Not physical people; not synthetic recognition results. No replay image is saved.
"""
import asyncio
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:5055'


async def run():
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    labels=sorted({r['student_id'] for r in gallery})
    paths={str(i):Path(next(r for r in gallery if r['student_id']==label)['image_path']) for i,label in enumerate(labels[:2])}
    result=dict(kind='dataset_video_replay_real_backend',physical_people=False,images_saved=False)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--disable-gpu','--renderer-process-limit=1','--disable-extensions'])
        page=await browser.new_page(viewport=dict(width=1440,height=1080))
        async def source(route):await route.fulfill(body=paths[route.request.url.rsplit('/',1)[1]].read_bytes(),content_type='image/jpeg')
        await page.route(URL+'/qa-replay/**',source)
        await page.add_init_script('''
        navigator.mediaDevices.getUserMedia=async()=>{
          const c=document.createElement('canvas');c.width=640;c.height=480;
          const ctx=c.getContext('2d');
          window.qaReplay=async(which)=>{ctx.fillStyle='#101827';ctx.fillRect(0,0,640,480);if(which!==null){const img=new Image();img.src='/qa-replay/'+which;await img.decode();const s=Math.min(640/img.width,480/img.height);ctx.drawImage(img,(640-img.width*s)/2,(480-img.height*s)/2,img.width*s,img.height*s);}return performance.now();};
          await window.qaReplay('0');window.qaStream=c.captureStream(20);return window.qaStream;
        };
        ''')
        await page.goto(URL);await page.wait_for_function('!document.getElementById("start-camera").disabled',timeout=180000)
        start=await page.evaluate('performance.now()');await page.click('#start-camera')
        await page.wait_for_function('document.getElementById("face-count").textContent==="1" && (()=>{const c=document.getElementById("live-overlay");return c.getContext("2d").getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)})()',timeout=60000)
        result['first_box_from_start_ms']=await page.evaluate('performance.now()')-start
        await page.locator('.candidate-name').first.wait_for(timeout=150000)
        result['first_candidate_from_start_ms']=await page.evaluate('performance.now()')-start
        result['first_candidate']=await page.locator('.candidate-name').first.text_content()
        result['first_diagnostics']=await page.evaluate('JSON.parse(JSON.stringify(window.cameraDiagnostics))')
        await page.evaluate('window.qaReplay(null)')
        await page.wait_for_function('document.getElementById("face-count").textContent==="0"',timeout=20000)
        assert await page.locator('.candidate-name').count()==0
        result['blank_clears_identity']=True
        changed=await page.evaluate('window.qaReplay("1")')
        await page.wait_for_function('document.getElementById("face-count").textContent==="1"',timeout=20000)
        await page.locator('.candidate-name').first.wait_for(timeout=150000)
        result['second_candidate']=await page.locator('.candidate-name').first.text_content()
        assert result['first_candidate']!=result['second_candidate']
        result['changed_person_to_candidate_ms']=await page.evaluate('performance.now()')-changed
        result['diagnostics']=await page.evaluate('JSON.parse(JSON.stringify(window.cameraDiagnostics))')
        assert result['diagnostics']['max_identity_active']==1
        await page.click('#stop-camera')
        assert await page.evaluate('window.qaStream.getTracks().every(t=>t.readyState==="ended")')
        assert await page.locator('.candidate-name').count()==0
        result['stopped_and_cleared']=True
        await browser.close()
    (ROOT/'outputs/realtime_camera_ui/replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if 'diagnostics' not in k},ensure_ascii=False,indent=2))


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
