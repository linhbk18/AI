"""Read-only live UI check plus editing/class assignment in disposable SQLite."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.database.import_legacy_students import import_students,ROSTER
from tests.test_management import EnrollmentFake

URL='http://127.0.0.1:5055'


async def run():
    checks={};errors=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        page=await browser.new_page(viewport=dict(width=1440,height=1000))
        page.on('pageerror',lambda exc:errors.append(str(exc)))
        await page.goto(URL+'/students')
        await page.wait_for_function('document.querySelectorAll("tbody tr").length===5')
        rows=page.locator('tbody tr')
        for item in ROSTER:
            row=rows.filter(has_text=item['temporary_code'])
            assert await row.count()==1
            assert await row.get_by_text(item['display_name'],exact=True).count()==1
            assert 'Mã tạm · demo' in await row.text_content()
            assert f"Đã đăng ký · {item['expected_embeddings']} vector" in await row.text_content()
            assert await row.locator('[data-action="edit"]').count()==1
        await page.screenshot(path=str(ROOT/'.venv/student_import_students.png'),full_page=True)
        checks['live_five_rows_codes_names_temporary_badges_and_exact_face_counts']=True
        assert await page.get_by_text('SV1',exact=True).count()==0
        checks['requested_removed_profile_not_in_live_students']=True
        await page.close()
        with tempfile.TemporaryDirectory(prefix='student-import-ui-',dir=ROOT/'.venv') as tmp:
            app=create_app(EnrollmentFake(),start_models=False,overrides={'TESTING':True,'DATABASE_PATH':Path(tmp)/'test.sqlite3'})
            store=app.extensions['management_store']
            config=json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8'))
            import_students(store,config)
            client=app.test_client()
            context=await browser.new_context();page=await context.new_page()
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            async def route(route):
                req=route.request
                response=client.open(req.url.removeprefix(URL),method=req.method,
                    headers={k:v for k,v in req.headers.items() if k in ('content-type','x-camera-token','origin')},
                    data=req.post_data_buffer,base_url=URL)
                await route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.get_data());response.close()
            await page.route(URL+'/**',route)
            await page.goto(URL+'/students')
            row=page.locator('tbody tr').filter(has_text='DEMO-SV001')
            await row.locator('[data-action="edit"]').click()
            await page.get_by_text('Mã hiện tại là mã tạm',exact=False).wait_for()
            await page.locator('[name="student_code"]').fill('QA-REAL-CODE')
            await page.locator('[name="full_name"]').fill('Tên hiển thị kiểm thử')
            await page.locator('#editor-form [type="submit"]').click()
            await page.wait_for_function('!document.getElementById("editor").open')
            row=page.locator('tbody tr').filter(has_text='QA-REAL-CODE')
            assert 'Đã đăng ký · 20 vector' in await row.text_content()
            assert await row.get_by_text('Mã tạm · demo',exact=True).count()==0
            checks['editing_code_and_display_name_preserves_20_vectors_and_clears_temporary_flag']=True
            await page.goto(URL+'/classes')
            await page.locator('[data-action="new"]').first.click()
            await page.locator('[name="class_code"]').fill('QA-TEMP-ONLY')
            await page.locator('[name="class_name"]').fill('Lớp kiểm thử tạm')
            await page.locator('#editor-form [type="submit"]').click()
            await page.wait_for_function('!document.getElementById("editor").open')
            await page.locator('a.text-button',has_text='Chi tiết').click()
            for _ in range(5):
                await page.locator('#member-select').select_option(index=1)
                await page.locator('#member-form button').click()
                await page.wait_for_function(f'document.querySelectorAll("tbody tr").length==={_+1}')
            assert await page.locator('tbody tr').count()==5
            checks['all_five_added_to_one_class_through_website_in_disposable_db']=True
            assert all(s['face_count'] in (16,17,19,20) for s in store.detail('classes',1)['students'])
            await context.close()
        assert not errors,errors
        checks['no_browser_javascript_error']=True
        await browser.close()
    output=ROOT/'outputs/student_import'
    result=dict(checks=checks,status='passed',live_mutations=False,physical_camera_tested=False)
    (output/'browser_tests.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
