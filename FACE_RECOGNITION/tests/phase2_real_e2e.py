"""Real-model attendance check called inside the existing disposable AI runner."""
from io import BytesIO
import json
from pathlib import Path
import re
import time
import uuid

from PIL import Image,ImageOps

ROOT=Path(__file__).resolve().parents[1]


def check(app,engine):
    client=app.test_client();store=app.extensions['management_store']
    html=client.get('/overview').get_data(as_text=True)
    token=json.loads(re.search(r'<script id="management-config" type="application/json">(.*?)</script>',html).group(1))['csrfToken'];headers={'X-Camera-Token':token}
    def post(url,data):
        response=client.post(url,headers=headers,json=data)
        assert response.status_code<300,(response.status_code,response.json)
        return response.json
    existing=store.list_entities('students')[0]
    absent=post('/api/students',dict(student_code='QA-ABSENT',full_name='Hồ sơ chỉ trong database kiểm thử'))
    classroom=post('/api/classes',dict(class_code='QA-ATTENDANCE',class_name='Lớp kiểm thử tạm'))
    for person in (existing,absent):post(f"/api/classes/{classroom['id']}/students",dict(student_id=person['id']))
    session=post('/api/attendance/sessions',dict(class_id=classroom['id'],session_name='Buổi QA AI thật',session_date='2026-11-14',request_key=str(uuid.uuid4())))
    camera=post('/api/camera/sessions',dict(profile='retinaface_scaled_experimental',attendance_session_id=session['id']))
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    with Image.open(Path(gallery[0]['image_path'])) as image:
        image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((640,480));stream=BytesIO();image.save(stream,format='JPEG',quality=95)
    began=time.perf_counter()
    response=client.post('/api/recognize',data=stream.getvalue(),headers=dict(headers,**{'X-Camera-Session':camera['session_id']}),content_type='image/jpeg')
    elapsed=(time.perf_counter()-began)*1000
    assert response.status_code==200,response.json
    faces=response.json['faces'];assert len(faces)==1 and faces[0]['top1']['student']['id']==existing['id'],response.json
    assert faces[0]['eligible_for_confirmation'] and not faces[0]['identity_confirmed']
    assert app.extensions['attendance_service'].detail(session['id'])['stats']['present']==0
    result=post(f"/api/attendance/sessions/{session['id']}/confirm",dict(student_id=existing['id'],confirmed=True,recognition_source='MANUAL'))
    assert result['changed'];assert not post(f"/api/attendance/sessions/{session['id']}/confirm",dict(student_id=existing['id'],confirmed=True))['changed']
    final=post(f"/api/attendance/sessions/{session['id']}/finish",{})
    assert final['stats']==dict(total=2,present=1,absent=1,pending=0)
    report=client.get('/api/attendance/reports?class_id='+str(classroom['id'])).json
    assert report['by_class'][0]['attendance_rate']==.5
    # Same camera binding must reject a late image after the session is closed.
    late=client.post('/api/recognize',data=stream.getvalue(),headers=dict(headers,**{'X-Camera-Session':camera['session_id']}),content_type='image/jpeg')
    assert late.status_code==409
    client.delete('/api/camera/sessions/'+camera['session_id'],headers=headers)
    result=dict(status='passed',kind='real_arcface_and_sqlite_disposable_database',recognition_ms=elapsed,faces=len(faces),
        candidate_student_id=existing['id'],manual_source=True,no_automatic_present=True,duplicate_prevented=True,
        completed_stats=final['stats'],report_rate=.5,late_frame_status=late.status_code,model_load_count=engine.model_load_count,production_records_created=0,physical_people_tested=False)
    (ROOT/'outputs/phase2/real_attendance_e2e.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
