"""Browser + real Flask/SQLite integration on a disposable DB; AI is a test double.

Routes are intercepted into Flask's test client. Nothing is written to the live
database, and screenshots contain synthetic test profiles only.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app import create_app
from tests.test_management import EnrollmentFake
from tests.test_camera_api import jpeg

URL='http://127.0.0.1:5055'


async def run():
    checks={}
    with tempfile.TemporaryDirectory(prefix='phase1-browser-',dir=ROOT/'.venv') as tmp:
        app=create_app(EnrollmentFake(),start_models=False,overrides={'TESTING':True,'DATABASE_PATH':Path(tmp)/'ui.sqlite3'})
        client=app.test_client()
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--use-fake-device-for-media-stream'])
            context=await browser.new_context(viewport=dict(width=1440,height=1080),permissions=['camera'])
            page=await context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            async def route(route):
                req=route.request
                headers={k:v for k,v in req.headers.items() if k.lower() in ('content-type','x-camera-token','origin')}
                response=client.open(req.url.removeprefix(URL),method=req.method,headers=headers,data=req.post_data_buffer,base_url=URL)
                await route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.get_data())
                response.close()
            await page.route(URL+'/**',route)
            async def go(path):await page.goto(URL+path);await page.locator('h1').wait_for()
            async def create(kind,code,name):
                await page.locator(f'[data-action="new"][data-kind="{kind}"]').first.click()
                await page.locator(f'[name="{"student_code" if kind=="students" else "class_code"}"]').fill(code)
                await page.locator(f'[name="{"full_name" if kind=="students" else "class_name"}"]').fill(name)
                await page.locator('#editor-form [type="submit"]').click()
                await page.wait_for_function('!document.getElementById("editor").open')
            await go('/overview')
            assert await page.locator('.metric-value').all_text_contents()==['0','0','0']
            checks['overview_real_database_counts_no_seed']=True
            await page.screenshot(path=str(ROOT/'.venv/phase1_overview.png'),full_page=True)
            await go('/classes');await create('classes','QA-CLASS','Lớp kiểm thử giao diện')
            await page.locator('[data-action="edit"]').click()
            await page.locator('[name="class_name"]').fill('Lớp giao diện đã sửa')
            await page.locator('#editor-form [type="submit"]').click()
            await page.wait_for_function('!document.getElementById("editor").open')
            assert await page.locator('tbody').get_by_text('Lớp giao diện đã sửa',exact=True).count()==1
            await page.locator('[data-action="new"]').first.click()
            await page.locator('[name="class_code"]').fill('QA-CLASS')
            await page.locator('[name="class_name"]').fill('Trùng mã')
            await page.locator('#editor-form [type="submit"]').click()
            await page.locator('#editor-error').get_by_text('Mã hoặc liên kết đã tồn tại',exact=False).wait_for()
            await page.locator('#cancel-editor').click()
            checks['class_create_edit_duplicate_error']=True
            await go('/students');await create('students','QA-STUDENT','Sinh viên kiểm thử')
            await page.locator('[data-action="edit"]').click()
            await page.locator('[name="full_name"]').fill('Sinh viên giao diện đã sửa')
            await page.locator('#editor-form [type="submit"]').click()
            await page.wait_for_function('!document.getElementById("editor").open')
            await page.locator('#search-query').fill('qa-student')
            await page.locator('#search-form button').click()
            await page.locator('tbody').get_by_text('Sinh viên giao diện đã sửa',exact=True).wait_for()
            await page.screenshot(path=str(ROOT/'.venv/phase1_students.png'),full_page=True)
            await page.locator('a.text-button',has_text='Chi tiết').click();await page.locator('#class-select').wait_for()
            await page.locator('#class-select').select_option(index=1)
            await page.locator('#student-class-form button').click()
            await page.locator('[data-action="remove-class"]').wait_for()
            checks['student_create_edit_search_and_membership']=True
            await page.locator('#face-file').set_input_files(dict(name='synthetic.jpg',mimeType='image/jpeg',buffer=jpeg()))
            await page.locator('#face-confirm').check()
            await page.locator('#face-form [type="submit"]').click()
            await page.wait_for_function('document.querySelector(".badge.success")?.textContent.includes("1 vector")')
            checks['upload_form_confirmation_and_persistent_face_status']=True
            await page.locator('#open-enroll-camera').click()
            await page.wait_for_function('document.getElementById("enroll-video").videoWidth>0')
            await page.evaluate('window.qaStream=document.getElementById("enroll-video").srcObject')
            await page.locator('#capture-enroll').click()
            await page.wait_for_function('document.getElementById("enroll-preview").hidden===false')
            assert await page.evaluate('window.qaStream.getTracks().every(t=>t.readyState==="ended")')
            await page.locator('#face-confirm').check();await page.locator('#face-form [type="submit"]').click()
            await page.wait_for_function('document.querySelector(".badge.success")?.textContent.includes("2 vector")')
            checks['camera_capture_stops_tracks_and_saves_embedding_via_api']=True
            await page.locator('#legacy-select').select_option(index=1)
            await page.locator('#mapping-confirm').check();await page.locator('#mapping-form button').click()
            await page.get_by_text('Đã liên kết thủ công với',exact=False).wait_for()
            checks['manual_legacy_mapping_ui']=True
            await page.screenshot(path=str(ROOT/'.venv/phase1_student_detail.png'),full_page=True)
            page.on('dialog',lambda d:d.accept())
            await page.locator('[data-action="remove-class"]').click()
            await page.get_by_text('Chưa phân vào lớp học nào.',exact=True).wait_for()
            await go('/classes/1')
            await page.get_by_text('Lớp chưa có sinh viên',exact=True).wait_for()
            await page.locator('#member-select').select_option(index=1);await page.locator('#member-form button').click()
            await page.locator('[data-action="remove-member"]').wait_for()
            await page.locator('[data-action="remove-member"]').click()
            await page.get_by_text('Lớp chưa có sinh viên',exact=True).wait_for()
            checks['class_detail_add_remove_preserves_student']=True
            await go('/students');await page.locator('[data-action="delete"]').click()
            await page.locator('#notification').get_by_text('Sinh viên đã có dữ liệu khuôn mặt',exact=False).wait_for()
            checks['protected_delete_explained_in_ui']=True
            await go('/overview')
            assert await page.locator('.metric-value').all_text_contents()==['1','1','1']
            await page.set_viewport_size(dict(width=390,height=844))
            await page.screenshot(path=str(ROOT/'.venv/phase1_mobile.png'),full_page=True)
            assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            checks['mobile_layout_no_horizontal_page_overflow']=True
            assert not errors,errors
            checks['no_browser_javascript_errors']=True
            await browser.close()
    out=ROOT/'outputs/phase1';out.mkdir(exist_ok=True)
    result=dict(kind='browser_flask_sqlite_disposable_db_fake_ai',checks=checks,physical_camera=False,production_database_untouched=True)
    (out/'browser_tests.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
