"""Public source-aware recognition metadata; never includes embeddings or photo paths.

Identity (name/code) is exposed ONLY for a face that passed the verification policy
AND belongs to the session's class. Every other face is 'UNKNOWN' / 'Không xác định'.
"""
from datetime import datetime,timezone


def normalize_faces(faces,source_type,records=(),timestamp=None):
    present={r['student_id']:r['attendance_status'] for r in records}
    roster=set(present)
    output=[]
    for face in faces:
        top=face.get('top1') or {};person=top.get('student')
        known=bool(person and face.get('verified') is True and not face.get('outside_class') and person['id'] in roster)
        person=person if known else None
        attendance=present.get(person['id']) if person else None
        status='DETECTED' if 'top1' not in face else 'IN_CLASS' if known else 'UNKNOWN'
        if attendance=='PRESENT':status='ATTENDED'
        output.append(dict(student_id=person['id'] if person else None,
            student_code=person['student_code'] if person else None,student_name=person['full_name'] if person else None,
            label=None if known else 'Không xác định',
            similarity=top.get('similarity') if known else None,margin=face.get('margin') if known else None,verification_status=status,
            identity_verified=known,presence_verified=False,auto_attendance_eligible=False,
            face_id=face.get('face_id'),track_id=face.get('tracking',{}).get('server_track_id'),
            bounding_box=face['box'],source_type=source_type,source_timestamp_seconds=timestamp,
            recognized_at=datetime.now(timezone.utc).isoformat(),attendance_status=attendance or 'PENDING'))
    return output
