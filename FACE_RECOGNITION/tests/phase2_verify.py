"""Read-only final audit: production data preserved, no test attendance rows."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import requests

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/phase2';URL='http://127.0.0.1:5055'


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(65536),b''):digest.update(chunk)
    return digest.hexdigest()


def run():
    originals=json.loads((ROOT/'.venv/phase2_originals.json').read_text(encoding='utf-8'))
    changed=[name for name,digest in originals.items() if sha(ROOT/name)!=digest];assert not changed,changed
    old=json.loads((ROOT/'.venv/phase2_database_before.json').read_text(encoding='utf-8'))
    with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:
        db.row_factory=sqlite3.Row
        for table,rows in old.items():assert [dict(r) for r in db.execute('SELECT * FROM '+table)]==rows,table
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        attendance={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('attendance_sessions','attendance_records','attendance_edits')}
        assert all(n==0 for n in attendance.values()),attendance
    for _ in range(150):
        h=requests.get(URL+'/api/health',timeout=5).json()
        if h['ready']:break
        if h['state']=='error':raise RuntimeError('Model initialization failed')
        time.sleep(1)
    assert h['ready'] and h['registered_embeddings']==92 and h['registered_students']==5
    pages={p:requests.get(URL+p,timeout=5).status_code for p in ('/overview','/classes','/students','/attendance','/history','/reports','/')}
    assert all(v==200 for v in pages.values()),pages
    for path in ('/DATASET/registered','/outputs/management.sqlite3','/outputs/face_embeddings.json'):
        assert requests.get(URL+path).status_code==404
    api_students=requests.get(URL+'/api/students').json()['items'];assert len(api_students)==5 and sum(s['face_count'] for s in api_students)==92
    assert requests.get(URL+'/api/attendance/sessions').json()['items']==[]
    reports=requests.get(URL+'/api/attendance/reports').json();assert reports['completed_sessions']==reports['present']==reports['absent']==0
    assert requests.post(URL+'/api/attendance/sessions',json={}).status_code==403
    # Literal palette declarations live in one shared stylesheet only.
    for file in (ROOT/'frontend/static/css').glob('*.css'):
        if file.name!='design-system.css':
            import re
            assert not re.search(r'#[0-9a-fA-F]{3,8}\b',file.read_text(encoding='utf-8')),str(file)
    result=dict(status='passed',original_files_verified=len(originals),original_files_changed=changed,
        original_sqlite_tables_and_rows_unchanged=True,attendance_rows=attendance,pages=pages,health=h,
        production_student_count=5,production_embedding_count=92,production_test_attendance_records=0,
        reports=reports,palette='shared navy-blue-white design-system.css',physical_people_tested=False)
    (OUT/'final_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS:',len(originals),'original files, all original SQLite rows unchanged; 5 students / 92 vectors.')
    print('PASS: all 7 pages HTTP 200; attendance tables empty in real DB; no public biometric routes; shared palette.')


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');run()
