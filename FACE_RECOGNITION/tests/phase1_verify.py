"""Read-only final verification of live localhost and original data preservation."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import numpy as np
import requests

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:5055'


def run():
    hashes=json.loads((ROOT/'.venv/phase1_originals.json').read_text(encoding='utf-8'))
    changed=[p for p,h in hashes.items() if not (ROOT/p).is_file() or hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    assert not changed,changed
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    vectors=np.asarray([r['embedding'] for r in gallery])
    assert vectors.shape==(92,512) and np.isfinite(vectors).all()
    assert len({r['student_id'] for r in gallery})==5
    for _ in range(90):
        health=requests.get(URL+'/api/health',timeout=5).json()
        if health['ready']:break
        if health['state']=='error':raise RuntimeError('Model initialization failed; see server terminal')
        time.sleep(1)
    assert health['ready'],health
    assert health['registered_embeddings']==92 and health['model_load_count']==1
    assert not health['attendance_enabled'] and not health['identity_confirmation_enabled']
    pages={p:requests.get(URL+p,timeout=5).status_code for p in ('/','/overview','/classes','/students','/static/css/management.css','/static/js/management.js')}
    assert all(status==200 for status in pages.values()),pages
    counts=requests.get(URL+'/api/overview').json()
    assert counts==dict(classes=0,students=0,registered_students=0),counts
    assert requests.post(URL+'/api/students',json={}).status_code==403
    assert requests.get(URL+'/outputs/management.sqlite3').status_code==404
    with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        assert all(name in tables for name in ('students','classes','class_students','face_embeddings','legacy_mappings','management_meta'))
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    report=dict(status='passed',server_restarted=True,url=URL,original_files_verified=len(hashes),
        original_files_changed=changed,gallery_shape=list(vectors.shape),gallery_students=5,
        database_counts=counts,database_tables=tables,pages=pages,health=health,
        sqlite_integrity='ok',no_sample_records_in_real_database=True)
    out=ROOT/'outputs/phase1';out.mkdir(exist_ok=True)
    (out/'final_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='health'},ensure_ascii=False,indent=2))


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');run()
