"""Readonly production-page smoke; injected permission errors, no AI or attendance simulation."""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]


def snapshot():
    with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:sql='\n'.join(db.iterdump())
    return hashlib.sha256(sql.encode()).hexdigest(),hashlib.sha256((ROOT/'outputs/face_embeddings.json').read_bytes()).hexdigest()


async def run():
    before=snapshot();errors=[]
    with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:
        opened=db.execute("SELECT id FROM attendance_sessions WHERE status='IN_PROGRESS' ORDER BY id DESC LIMIT 1").fetchone()
    if not opened:raise RuntimeError('Readonly smoke requires an existing open session; it never creates one in production.')
    session_id=opened[0]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--disable-gpu','--renderer-process-limit=1'])
        page=await browser.new_page(viewport=dict(width=1366,height=1000));page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(f'http://127.0.0.1:5055/attendance?session={session_id}')
        await page.wait_for_function('(id)=>window.attendanceBridge?.canStart() && document.getElementById("existing-session")?.value===String(id)',arg=session_id)
        await page.wait_for_function('document.getElementById("automatic-mode")?.disabled===true')
        assert await page.locator('#input-source option').count()==4
        assert await page.locator('#roster-filter option').count()==4
        assert await page.locator('.candidates-card').count()==0
        await page.locator('#input-source').select_option('snapshot')
        await page.evaluate('()=>{navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException("Denied by test","NotAllowedError")};}')
        await page.click('#open-source-camera');await page.wait_for_function('document.getElementById("media-status").textContent.includes("bị từ chối")')
        await page.evaluate("""()=>{navigator.mediaDevices.getUserMedia=async()=>{await new Promise(r=>setTimeout(r,800));const c=document.createElement('canvas');c.width=320;c.height=240;window.testPendingMedia=c.captureStream(10);return testPendingMedia;};}""")
        await page.click('#open-source-camera');await page.locator('#input-source').select_option('image')
        await page.wait_for_function('window.testPendingMedia?.getTracks().every(t=>t.readyState==="ended")')
        assert await page.locator('#media-video').evaluate('(v)=>v.srcObject===null')
        await page.locator('#input-source').select_option('video')
        assert await page.locator('#media-video').evaluate('(v)=>getComputedStyle(v).position')=='static'
        await page.screenshot(path=str(ROOT/'.venv/upgrade_layout_final.png'),full_page=True)
        await page.set_viewport_size(dict(width=390,height=844));assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors;await browser.close()
    assert snapshot()==before
    report=dict(status='passed',readonly_production_sqlite_unchanged=True,gallery_unchanged=True,
        checks=['existing_session_selector','automatic_mode_locked_without_policy','four_sources','four_roster_filters','camera_candidate_card_absent','permission_denied_message',
            'late_media_permission_closed_after_mode_switch','video_static_position','responsive','no_javascript_errors'],physical_camera_tested=False)
    (ROOT/'outputs/upgrade_readonly_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':asyncio.run(run())
