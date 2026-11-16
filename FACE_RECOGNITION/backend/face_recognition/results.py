"""Public source-aware recognition metadata; never includes embeddings or photo paths."""
from datetime import datetime,timezone


def normalize_faces(faces,source_type,records=(),timestamp=None):
    present={r['student_id']:r['attendance_status'] for r in records}
    output=[]
    for face in faces:
        top=face.get('top1') or {};person=top.get('student')
        unknown=not person or face.get('outside_class') or face.get('decision')=='rejected' or (source_type=='live_camera' and face.get('identity_confirmed') is not True)
        person=None if unknown else person
        attendance=present.get(person['id']) if person else None
        verified=bool(person and face.get('identity_confirmed'))
        status='DETECTED' if 'top1' not in face else 'UNKNOWN' if unknown else 'VERIFIED' if verified else 'CANDIDATE'
        if attendance=='PRESENT':status='ATTENDED'
        output.append(dict(student_id=person['id'] if person else None,
            student_code=person['student_code'] if person else None,student_name=person['full_name'] if person else None,
            similarity=top.get('similarity'),margin=face.get('margin'),verification_status=status,
            identity_verified=verified,presence_verified=False,auto_attendance_eligible=False,
            face_id=face.get('face_id'),track_id=face.get('tracking',{}).get('server_track_id'),
            bounding_box=face['box'],source_type=source_type,source_timestamp_seconds=timestamp,
            recognized_at=datetime.now(timezone.utc).isoformat(),attendance_status=attendance or 'PENDING',
            outside_class=bool(face.get('outside_class'))))
    return output
