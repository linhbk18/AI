"""Readonly browser test; no human ground truth is manufactured or persisted."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]


async def run():
    path=ROOT/'outputs/test_labeling/labels.json';before=hashlib.sha256(path.read_bytes()).hexdigest();errors=[]
    labels=json.loads(path.read_text(encoding='utf-8'));manifest=json.loads((ROOT/'outputs/test_labeling/manifest.json').read_text(encoding='utf-8'))
    expected=lambda im:[labels['faces'][f['face_id']]['label'] for f in im['faces']]
    unknown_count=sum(l['label']=='UNKNOWN' for l in labels['faces'].values())
    unknown_missing=sum(l['label']=='UNKNOWN' and not l.get('unknown_person_id') for l in labels['faces'].values())
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--disable-gpu','--renderer-process-limit=1'])
        page=await browser.new_page(viewport=dict(width=1366,height=1000));page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('http://127.0.0.1:5055/test-labeling')
        await page.wait_for_function('(n)=>document.querySelectorAll("[data-face-id]").length===n',arg=len(manifest['images'][0]['faces']))
        assert await page.locator('#label-images button').count()==9
        assert await page.locator('[data-face-id] select').first.locator('option').count()==8
        assert await page.locator('[data-face-id] select').evaluate_all('(s)=>s.map(e=>e.value)')==expected(manifest['images'][0])
        assert 'UNKNOWN' in await page.locator('[data-face-id] select').first.text_content()
        assert 'IGNORE' in await page.locator('[data-face-id] select').first.text_content()
        await page.select_option('#face-filter','pending')
        first=manifest['images'][0]
        assert await page.locator('[data-face-id]:visible').count()==sum(labels['faces'][f['face_id']]['label']=='UNLABELED' for f in first['faces'])
        await page.select_option('#face-filter','all')
        await page.click('#review-unknown')
        await page.wait_for_function('(n)=>document.querySelectorAll("#unknown-contact-sheet button").length===n',arg=unknown_count)
        assert await page.locator('#unknown-contact-sheet').get_by_text('Thiếu mã',exact=False).count()==unknown_missing
        await page.click('#review-unknown')
        await page.locator('#label-images button',has_text='Group12.jpg').click()
        await page.wait_for_function('document.querySelectorAll("[data-face-id]").length===43')
        assert await page.locator('[data-face-id] select').evaluate_all('(s)=>s.map(e=>e.value)')==expected(next(im for im in manifest['images'] if im['name']=='Group12.jpg'))
        await page.screenshot(path=str(ROOT/'.venv/test_labeling_preview.png'),full_page=True)
        await page.set_viewport_size(dict(width=390,height=844));assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        # Form choice only, not saved: confirms UNKNOWN reveals anonymous-person field.
        await page.locator('[data-face-id] select').first.select_option('UNKNOWN')
        assert await page.locator('.unknown-person').first.is_visible()
        assert await page.locator('.unknown-person').first.get_attribute('list')=='unknown-person-codes'
        await page.locator('[data-face-id] select').first.select_option('IGNORE')
        assert not await page.locator('.unknown-person').first.is_visible()
        assert not errors,errors;await browser.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    report=dict(status='passed',image_count=9,form_matches_saved_human_labels=True,ai_predictions_not_prefilled=True,
        registered_unknown_ignore_controls=True,pending_filter_matches_saved_labels=True,unknown_contact_sheet_faces=unknown_count,
        anonymous_code_datalist=True,group12_rows=43,responsive=True,no_javascript_errors=True,production_labels_unchanged=True)
    (ROOT/'outputs/test_labeling/browser_check.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))


if __name__=='__main__':asyncio.run(run())
