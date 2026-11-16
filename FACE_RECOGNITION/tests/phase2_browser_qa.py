"""Full browser workflow on disposable SQLite; AI double explicitly labelled."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.database.import_legacy_students import import_students
from tests.test_management import EnrollmentFake

URL='http://127.0.0.1:5055'


async def run():
    checks={};errors=[]
    with tempfile.TemporaryDirectory(prefix='phase2-ui-',dir=ROOT/'.venv') as tmp:
        engine=EnrollmentFake()
        engine.detect_preview=lambda bgr,cancelled:dict(faces=[dict(face_id=i+1,box=dict(x=80+140*i,y=60,w=90,h=100)) for i in range(2)],frame=dict(width=bgr.shape[1],height=bgr.shape[0]),preview_only=True)
        app=create_app(engine,start_models=False,overrides={'TESTING':True,'DATABASE_PATH':Path(tmp)/'test.sqlite3'})
        store=app.extensions['management_store'];import_students(store,json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8')))
        def recognize(bgr,cancelled,profile,allowed_student_ids=None):
            people=store.list_entities('students');people.sort(key=lambda s:s['id']);chosen=next(s for s in people if allowed_student_ids is None or s['id'] in allowed_student_ids)
            eligible=[s for s in people if allowed_student_ids is None or s['id'] in allowed_student_ids][:2]
            faces=[dict(face_id=i+1,box=dict(x=80+140*i,y=60,w=90,h=100),top1=dict(candidate=s['full_name'],similarity=.8,student=dict(id=s['id'],full_name=s['full_name'],student_code=s['student_code'])),top2=dict(candidate=None,similarity=None),margin=None,eligible_for_confirmation=True,outside_class=False) for i,s in enumerate(eligible)]
            return dict(faces=faces,detected_faces=len(faces),frame=dict(width=bgr.shape[1],height=bgr.shape[0]),processing_ms=10,identity_confirmation_enabled=False,attendance_enabled=False,profile=profile)
        engine.analyze=recognize;client=app.test_client()
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True,args=['--use-fake-device-for-media-stream','--disable-gpu','--renderer-process-limit=1'])
            context=await browser.new_context(viewport=dict(width=1440,height=1050),permissions=['camera']);page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('dialog',lambda d:d.accept())
            async def route(route):
                req=route.request
                r=client.open(req.url.removeprefix(URL),method=req.method,headers={k:v for k,v in req.headers.items() if k in ('content-type','origin','x-camera-token','x-camera-session')},data=req.post_data_buffer,base_url=URL)
                await route.fulfill(status=r.status_code,headers=dict(r.headers),body=r.get_data());r.close()
            await context.route(URL+'/**',route)
            async def go(path):await page.goto(URL+path);await page.locator('h1').wait_for()
            await go('/classes');await page.locator('[data-action="new"]').first.click()
            await page.locator('[name="class_code"]').fill('QA-PHASE2');await page.locator('[name="class_name"]').fill('Lớp demo trong database tạm')
            await page.locator('#editor-form [type=submit]').click();await page.wait_for_function('!document.getElementById("editor").open')
            await page.locator('a.text-button',has_text='Chi tiết').click()
            for i in range(5):
                await page.locator('#member-select').select_option(index=1);await page.locator('#member-form button').click();await page.wait_for_function(f'document.querySelectorAll("tbody tr").length==={i+1}')
            checks['create_class_assign_five_students_existing_management_ui']=True
            await go('/attendance');await page.locator('#attendance-class').wait_for()
            assert await page.locator('.gallery-card,.processed-card,#processed-frame').count()==0
            assert await page.locator('.attendance-progress').count()==0
            assert await page.locator('#start-camera').is_disabled()
            await page.locator('#attendance-class').select_option(index=1)
            await page.wait_for_function('document.querySelectorAll(".attendance-roster-row").length===5')
            assert await page.locator('.attendance-progress').count()==0
            await page.locator('#session-name').fill('Buổi kiểm thử E2E')
            await page.locator('#begin-session').click();await page.wait_for_function('document.getElementById("session-status").textContent==="Đang điểm danh"')
            checks['select_real_class_create_session_snapshot_and_stats']=True
            assert await page.locator('[data-count="total"]').text_content()=='5'
            assert await page.locator('.attendance-progress').get_attribute('value')=='0'
            assert 'Vắng mặt' not in await page.locator('#attendance-stats').text_content()
            await page.click('#start-camera');await page.wait_for_function('window.cameraDiagnostics.identity_records.length>0',timeout=20000)
            assert await page.locator('.candidates-card').count()==0
            assert await page.locator('[data-confirm-student]').count()==5
            checks['multiple_faces_have_separate_candidates_and_manual_confirm_buttons']=True
            assert app.extensions['attendance_service'].detail(1)['stats']['present']==0
            checks['ai_candidate_does_not_automatically_mark_present']=True
            await page.locator('[data-confirm-student]').first.click()
            await page.wait_for_function('document.querySelector("[data-count=present]")?.textContent==="1"')
            assert await page.locator('.attendance-progress').get_attribute('value')=='1'
            assert '20%' in await page.locator('#attendance-stats').text_content()
            records=app.extensions['attendance_service'].detail(1)['records'];assert sum(r['attendance_status']=='PRESENT' for r in records)==1
            assert all(r['recognition_source']=='MANUAL' for r in records if r['attendance_status']=='PRESENT')
            checks['explicit_human_confirmation_persists_manual_source']=True
            await page.evaluate('document.getElementById("camera-video").srcObject.getVideoTracks()[0].dispatchEvent(new Event("ended"))')
            await page.wait_for_function('document.getElementById("camera-video").srcObject===null')
            assert app.extensions['attendance_service'].detail(1)['status']=='IN_PROGRESS'
            checks['simulated_camera_disconnect_stops_tracks_without_ending_or_losing_session']=True
            await page.click('#start-camera');await page.wait_for_function('window.cameraDiagnostics.identity_records.length>0',timeout=20000)
            await page.evaluate('window.scrollTo(0,0)');await page.screenshot(path=str(ROOT/'.venv/phase2_attendance.png'),full_page=True)
            await page.click('#finish-session');await page.wait_for_function('document.getElementById("session-status").textContent==="Đã kết thúc"')
            assert app.extensions['attendance_service'].detail(1)['stats']==dict(total=5,present=1,absent=4,pending=0)
            assert await page.locator('[data-count="absent"]').text_content()=='4'
            watch=await context.new_page();await watch.goto(URL+'/attendance?session=1')
            await watch.wait_for_function('document.querySelector("[data-count=present]")?.textContent==="1"')
            assert await page.locator('#start-camera').is_disabled()
            checks['finish_stops_camera_and_finalizes_four_absences']=True
            await go('/history');await page.get_by_role('link',name='Chi tiết',exact=True).click()
            await page.wait_for_function('document.querySelectorAll("tbody tr").length===5')
            assert await page.locator('tbody tr').count()==5
            await page.locator('[data-edit-record]').nth(1).click();await page.locator('dialog textarea').fill('Đối chiếu và chỉnh sửa thủ công')
            await page.locator('dialog select').select_option('PRESENT');await page.locator('dialog .primary').click()
            await page.wait_for_function('document.querySelectorAll("dialog[open]").length===0')
            assert app.extensions['attendance_service'].detail(1)['stats']['present']==2
            await watch.wait_for_function('document.querySelector("[data-count=present]")?.textContent==="2"')
            assert '40%' in await watch.locator('#attendance-stats').text_content()
            checks['progress_updates_after_history_edit_in_another_tab_without_reload']=True
            await watch.close()
            checks['history_edit_and_audit_saved_only_to_selected_session']=True
            await go('/reports');await page.locator('#report-results table').first.wait_for()
            assert '40.0%' in await page.locator('#report-results').text_content()
            checks['reports_completed_sessions_real_sqlite_ratio']=True
            await go('/attendance?session=1');await page.locator('#new-session').wait_for(state='visible')
            await page.click('#new-session');await page.locator('#session-name').fill('Buổi tiếp theo chưa kết thúc');await page.click('#begin-session')
            await page.wait_for_function('document.getElementById("session-info").textContent.includes("#2")')
            assert app.extensions['attendance_service'].reports()['completed_sessions']==1
            checks['new_session_reuses_class_but_reports_exclude_unfinished_session']=True
            other=store.save('classes',dict(class_code='QA-EMPTY',class_name='Lớp trống chỉ trong test'))
            await page.reload();await page.locator('#attendance-class').select_option(str(other['id']))
            await page.wait_for_function('document.getElementById("session-status").textContent==="Chưa bắt đầu"')
            assert await page.locator('#start-camera').is_disabled()
            assert await page.locator('#begin-session').is_disabled()
            assert app.extensions['attendance_service'].detail(2)['status']=='IN_PROGRESS'
            checks['changing_class_drops_camera_context_keeps_open_history_and_handles_empty_class']=True
            pair=store.save('classes',dict(class_code='QA-PAIR',class_name='Lớp có hai sinh viên trong test'))
            for person in store.list_entities('students')[:2]:store.membership(pair['id'],person['id'])
            await page.reload();await page.locator('#attendance-class').select_option(str(pair['id']))
            await page.wait_for_function('document.querySelectorAll(".attendance-roster-row").length===2')
            await page.locator('#session-name').fill('Buổi sĩ số hai');await page.click('#begin-session')
            await page.wait_for_function('document.querySelector("[data-count=total]")?.textContent==="2"')
            assert await page.locator('.attendance-progress').get_attribute('max')=='2'
            assert await page.locator('.attendance-progress').get_attribute('value')=='0'
            checks['switching_to_two_student_session_resets_progress_and_uses_snapshot_total']=True
            restarted=create_app(EnrollmentFake(),start_models=False,overrides={'DATABASE_PATH':Path(tmp)/'test.sqlite3'})
            assert restarted.test_client().get('/api/attendance/sessions/1').json['stats']['present']==2
            client=restarted.test_client()
            await go('/attendance?session=1')
            await page.wait_for_function('document.querySelector("[data-count=present]")?.textContent==="2"')
            assert await page.locator('.attendance-progress').get_attribute('max')=='5'
            assert '40%' in await page.locator('#attendance-stats').text_content()
            checks['reopen_flask_preserves_session_and_edits']=True
            for path in ('/overview','/classes','/students','/attendance','/history','/reports','/'):
                await go(path)
                assert await page.locator('.sidebar nav a').count()==6
                assert await page.locator('.unified-header strong').text_content()=='AI STUDENT ATTENDANCE'
                assert await page.locator('.sidebar').evaluate('(e)=>getComputedStyle(e).backgroundColor')=='rgb(20, 45, 78)'
                assert await page.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--primary").trim()')=='#2176AE'
            checks['all_seven_pages_shared_sidebar_header_navy_blue_design_tokens']=True
            await page.screenshot(path=str(ROOT/'.venv/phase2_camera.png'),full_page=True)
            await page.set_viewport_size(dict(width=390,height=844));await go('/attendance')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await page.screenshot(path=str(ROOT/'.venv/phase2_mobile.png'),full_page=True)
            checks['mobile_no_page_overflow']=True
            assert not errors,errors;checks['no_javascript_errors']=True
            await browser.close()
    out=ROOT/'outputs/phase2';out.mkdir(exist_ok=True)
    result=dict(status='passed',kind='browser_real_flask_sqlite_mock_ai',checks=checks,production_records_created=0,physical_people_tested=False)
    Path(os.environ.get('PHASE2_UI_REPORT',str(out/'browser_tests.json'))).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');asyncio.run(run())
