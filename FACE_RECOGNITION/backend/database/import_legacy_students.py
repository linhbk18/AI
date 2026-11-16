"""Import existing dataset identities without AI inference or copying vectors.

Run: .venv/Scripts/python.exe -m backend.database.import_legacy_students
Existing unmapped names require an explicit --link LABEL=ID or --new LABEL.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path,PurePosixPath
import sqlite3
import sys
import unicodedata

import numpy as np

from .db import ManagementStore,DomainError,now

ROOT=Path(__file__).resolve().parents[2]
ROSTER=json.loads(Path(__file__).with_name('legacy_student_roster.json').read_text(encoding='utf-8'))


def validate_gallery(store,config):
    counts=Counter(r.get('student_id') for r in store.legacy)
    expected={r['dataset_label']:r['expected_embeddings'] for r in ROSTER}
    if counts!=expected or len(store.legacy)!=92: raise DomainError('Số vector/nhãn không khớp danh sách 5 sinh viên và 92 vector; không nhập dữ liệu.')
    seen=set()
    for record in store.legacy:
        label=record['student_id'];source=PurePosixPath(record.get('source',''))
        vector=np.asarray(record.get('embedding',[]),dtype=float)
        if (vector.shape!=(512,) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0
            or record.get('embedding_dimension')!=512 or record.get('face_count')!=1):
            raise DomainError(f'Embedding không hợp lệ: {record.get("source")}')
        if (len(source.parts)<4 or source.parts[:3]!=('DATASET','registered',label)
            or '..' in source.parts or str(source) in seen): raise DomainError('Nguồn/nhãn embedding không hợp lệ hoặc trùng.')
        for key in ('model_name','detector_backend','align','normalization','enforce_detection'):
            if record.get(key)!=config.get(key): raise DomainError(f'Embedding khác cấu hình: {key}')
        if config.get('model_name')!='ArcFace' or config.get('embedding_dimension')!=512: raise DomainError('Cấu hình không phải ArcFace 512 chiều.')
        seen.add(str(source))


def audit_links(store,db):
    report=[];references=set()
    for item in ROSTER:
        label=item['dataset_label']
        linked=db.execute('SELECT s.* FROM legacy_mappings m JOIN students s ON s.id=m.student_id WHERE m.dataset_label=? AND s.deleted_at IS NULL',(label,)).fetchone()
        if not linked: raise DomainError(f'Thiếu hồ sơ ánh xạ: {label}')
        expected={f'legacy:{i}' for i,r in enumerate(store.legacy) if r['student_id']==label}
        rows=db.execute('SELECT * FROM face_embeddings WHERE student_id=? AND embedding_reference IS NOT NULL',(linked['id'],)).fetchall()
        if {r['embedding_reference'] for r in rows}!=expected or len(rows)!=len(expected):
            raise DomainError(f'Liên kết thiếu, sai hoặc trùng: {label}')
        for row in rows:
            index=int(row['embedding_reference'].split(':')[1]);record=store.legacy[index]
            if row['embedding_json'] is not None or record['student_id']!=label: raise DomainError('Không được sao chép/ánh xạ sai vector gốc.')
            pipeline=json.loads(row['pipeline_json'])
            if any(pipeline.get(k)!=record[k] for k in ('model_name','detector_backend','align','normalization')):
                raise DomainError('Metadata tham chiếu khác gallery gốc.')
        if expected & references: raise DomainError('Vector bị gán trùng.')
        references|=expected
        uploaded=db.execute('SELECT count(*) FROM face_embeddings WHERE student_id=? AND embedding_json IS NOT NULL',(linked['id'],)).fetchone()[0]
        report.append(dict(dataset_label=label,student_id=linked['id'],student_code=linked['student_code'],full_name=linked['full_name'],
            code_is_temporary=bool(linked['student_code_is_temporary']),legacy_embedding_count=len(rows),additional_embedding_count=uploaded))
    if len(references)!=92: raise DomainError('Không đủ 92 liên kết.')
    return report


def import_students(store,config,explicit_links=None,new_labels=(),dry_run=False):
    validate_gallery(store,config)
    explicit_links=explicit_links or {};labels={r['dataset_label'] for r in ROSTER}
    if (set(explicit_links)|set(new_labels))-labels: raise DomainError('Tên trong lựa chọn ánh xạ không thuộc dataset.')
    if set(explicit_links)&set(new_labels): raise DomainError('Không thể vừa liên kết vừa tạo mới cùng nhãn.')
    created=0;changes=0;warnings=[]
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        before=db.execute('SELECT count(*) FROM students').fetchone()[0]
        for item in ROSTER:
            label=item['dataset_label']
            mapped=db.execute('SELECT student_id FROM legacy_mappings WHERE dataset_label=?',(label,)).fetchone()
            if mapped:
                sid=mapped['student_id']
                if label in explicit_links and explicit_links[label]!=sid: raise DomainError('Lựa chọn mới mâu thuẫn với ánh xạ đã có; không ghi đè.')
            elif label in explicit_links:
                sid=explicit_links[label];store.require(db,'students',sid)
            else:
                # Names only raise ambiguity; they NEVER decide identity or merge.
                same_name=[r for r in db.execute('SELECT id,full_name FROM students WHERE deleted_at IS NULL')
                    if unicodedata.normalize('NFC',r['full_name']).casefold()==unicodedata.normalize('NFC',item['display_name']).casefold()]
                if same_name and label not in new_labels:
                    raise DomainError(f'Hồ sơ chưa ánh xạ trùng tên {label}; cần --link "{label}=ID" đã xác nhận hoặc --new "{label}". Không gộp theo họ tên.')
                if same_name: warnings.append(f'Giữ các hồ sơ trùng tên {label} riêng theo lựa chọn rõ ràng.')
                stamp=now()
                sid=db.execute('INSERT INTO students(student_code,full_name,created_at,updated_at,student_code_is_temporary) VALUES (?,?,?,?,1)',
                    (item['temporary_code'],item['display_name'],stamp,stamp)).lastrowid
                created+=1
            changes+=store.link_legacy_in_transaction(db,sid,label)
        if changes:store.bump(db)
        rows=audit_links(store,db)
        result=dict(status='dry_run' if dry_run else 'complete',created_students=created,existing_students_reused=5-created,
            reference_or_mapping_changes=changes,total_legacy_embeddings=92,students=rows,warnings=warnings,
            student_rows_before=before,student_rows_after=db.execute('SELECT count(*) FROM students').fetchone()[0])
        if db.execute('PRAGMA foreign_key_check').fetchall(): raise DomainError('Database có lỗi khóa ngoại.')
        if dry_run:db.rollback()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=ROOT/'outputs/management.sqlite3')
    parser.add_argument('--link',action='append',default=[],metavar='LABEL=STUDENT_ID')
    parser.add_argument('--new',action='append',default=[],dest='new_labels',metavar='LABEL')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--report',type=Path,default=ROOT/'outputs/student_import/import_result.json')
    args=parser.parse_args()
    if not args.database.is_file():parser.error('Database chưa tồn tại; không tạo lại database trong công cụ nhập.')
    try:
        links={}
        for raw in args.link:
            label,identity=raw.rsplit('=',1)
            if label in links: raise ValueError('Nhãn --link bị lặp.')
            links[label]=int(identity)
        gallery=ROOT/'outputs/face_embeddings.json'
        digest=hashlib.sha256(gallery.read_bytes()).hexdigest()
        store=ManagementStore(args.database,gallery)
        config=json.loads((ROOT/'outputs/face_config.json').read_text(encoding='utf-8'))
        result=import_students(store,config,links,args.new_labels,args.dry_run)
        assert hashlib.sha256(gallery.read_bytes()).hexdigest()==digest
        result['gallery_sha256']=digest
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('NHÃN DATASET | MÃ HIỆN TẠI | LOẠI MÃ | VECTOR GỐC | VECTOR BỔ SUNG')
        for r in result['students']:
            print(f"{r['dataset_label']} | {r['student_code']} | {'TẠM/DEMO' if r['code_is_temporary'] else 'GIỮ MÃ ĐÃ CÓ'} | {r['legacy_embedding_count']} | {r['additional_embedding_count']}")
        print(f"{result['status']}: 92 references; created={result['created_students']}; changes={result['reference_or_mapping_changes']}")
    except (DomainError,ValueError,KeyError,sqlite3.Error) as exc:
        print(f'IMPORT FAILED: {exc}',file=sys.stderr);return 1
    return 0


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
