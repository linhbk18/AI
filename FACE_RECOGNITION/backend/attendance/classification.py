"""Single backend decision point for what the camera may show about a face.

IN_CLASS   : verified by the similarity/margin policy, mapped to a numeric SQLite
             student_id, that id is in the CURRENT enrollment of the open session's
             class, and the result is bound to the current face track.
UNKNOWN    : everything else that has a recognition result (outsider, unregistered,
             not verified, unmapped, top-1 belongs to another class...).
PROCESSING : a verified in-class result that cannot (yet) be bound to the face that
             is on screen now. No identity is exposed.
VERIFIED   : only on the stand-alone camera page (no class/session context).

Only IN_CLASS / VERIFIED faces carry a name or student code in the response.
"""
IN_CLASS = 'IN_CLASS'
UNKNOWN = 'UNKNOWN'
PROCESSING = 'PROCESSING'
VERIFIED = 'VERIFIED'


def classify(face, roster_ids=None, session_open=False, track_valid=False, has_context=True):
    student_id = face.get('candidate_student_id')
    if face.get('verified') is not True or not isinstance(student_id, int) or isinstance(student_id, bool):
        return UNKNOWN
    if not has_context:
        return VERIFIED
    if not session_open or roster_ids is None or student_id not in roster_ids:
        return UNKNOWN
    return IN_CLASS if track_valid else PROCESSING


def public_face(face, status, **extra):
    """Whitelist output. No embedding, no gallery label, no top-2 name, no outsider identity."""
    shown = status in (IN_CLASS, VERIFIED)
    person = (face.get('top1') or {}).get('student') if shown else None
    out = dict(face_id=face.get('face_id'), box=face.get('box'), status=status,
               label=('Không xác định' if status == UNKNOWN else '' if status == PROCESSING else None),
               student=dict(id=person['id'], student_code=person['student_code'], full_name=person['full_name']) if person else None,
               similarity=(face.get('top1') or {}).get('similarity') if shown else None,
               margin=face.get('margin') if shown else None,
               tracking=face.get('tracking', dict(valid=False, server_track_id=None)))
    out.update(extra)
    return out
