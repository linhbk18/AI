"""Persistent local management; original biometric files are read-only."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unicodedata
import numpy as np


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status=status


def text(value,label,maximum=160,required=True):
    if not isinstance(value,str): raise DomainError(f'{label} phải là chuỗi ký tự.')
    value=unicodedata.normalize('NFC',value).strip()
    if (required and not value) or len(value)>maximum or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise DomainError(f'{label} không hợp lệ (tối đa {maximum} ký tự).')
    return value


class ManagementStore:
    def __init__(self,path,legacy_path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.legacy=json.loads(Path(legacy_path).read_text(encoding='utf-8'))
        self.labels=sorted({r['student_id'] for r in self.legacy})
        self.initialize()

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.create_function('fold',1,lambda s:unicodedata.normalize('NFC',s or '').casefold())
        try:
            with db: yield db
        except sqlite3.IntegrityError as exc:
            raise DomainError('Mã hoặc liên kết đã tồn tại, hoặc dữ liệu còn được tham chiếu.',409) from exc
        finally: db.close()

    def initialize(self):
        with self.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY,student_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
                full_name TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY,class_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
                class_name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS class_students (
                id INTEGER PRIMARY KEY,class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE RESTRICT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,UNIQUE(class_id,student_id));
            CREATE TABLE IF NOT EXISTS face_embeddings (
                id INTEGER PRIMARY KEY,student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
                embedding_reference TEXT UNIQUE,embedding_json TEXT,pipeline_json TEXT NOT NULL,
                image_sha256 TEXT UNIQUE,created_at TEXT NOT NULL,attribution_confirmed_at TEXT NOT NULL,
                CHECK ((embedding_reference IS NULL) != (embedding_json IS NULL)));
            CREATE TABLE IF NOT EXISTS legacy_mappings (
                dataset_label TEXT PRIMARY KEY,student_id INTEGER NOT NULL UNIQUE REFERENCES students(id) ON DELETE RESTRICT);
            CREATE TABLE IF NOT EXISTS management_meta (key TEXT PRIMARY KEY,value INTEGER NOT NULL);
            INSERT OR IGNORE INTO management_meta VALUES ('gallery_revision',0);
            INSERT OR IGNORE INTO management_meta VALUES ('schema_version',1);
            CREATE INDEX IF NOT EXISTS ix_face_student ON face_embeddings(student_id);
            CREATE INDEX IF NOT EXISTS ix_members_student ON class_students(student_id);
            ''')
            # Additive migration: existing codes/names/references are never replaced.
            if 'student_code_is_temporary' not in {r['name'] for r in db.execute('PRAGMA table_info(students)')}:
                db.execute('ALTER TABLE students ADD COLUMN student_code_is_temporary INTEGER NOT NULL DEFAULT 0 CHECK(student_code_is_temporary IN (0,1))')
            db.execute("UPDATE management_meta SET value=2 WHERE key='schema_version' AND value<2")

    @staticmethod
    def bump(db):
        db.execute("UPDATE management_meta SET value=value+1 WHERE key='gallery_revision'")

    @staticmethod
    def require(db,kind,identity):
        row=db.execute(f'SELECT * FROM {kind} WHERE id=? AND deleted_at IS NULL',(identity,)).fetchone()
        if row is None: raise DomainError('Không tìm thấy hồ sơ đang hoạt động.',404)
        return dict(row)

    def overview(self):
        with self.connection() as db:
            return dict(classes=db.execute('SELECT count(*) FROM classes WHERE deleted_at IS NULL').fetchone()[0],
                students=db.execute('SELECT count(*) FROM students WHERE deleted_at IS NULL').fetchone()[0],
                registered_students=db.execute('SELECT count(DISTINCT f.student_id) FROM face_embeddings f JOIN students s ON s.id=f.student_id WHERE s.deleted_at IS NULL').fetchone()[0])

    def list_entities(self,kind,query=''):
        code,name=('student_code','full_name') if kind=='students' else ('class_code','class_name')
        query=text(query,'Từ khóa',160,False).casefold()
        with self.connection() as db:
            rows=db.execute(f'SELECT * FROM {kind} WHERE deleted_at IS NULL AND (instr(fold({code}),?)>0 OR instr(fold({name}),?)>0) ORDER BY id DESC',(query,query))
            result=[]
            for row in rows:
                r=dict(row)
                if kind=='students':
                    r['face_count']=db.execute('SELECT count(*) FROM face_embeddings WHERE student_id=?',(r['id'],)).fetchone()[0]
                    r['classes']=[dict(c) for c in db.execute('SELECT c.id,c.class_code,c.class_name FROM classes c JOIN class_students m ON m.class_id=c.id WHERE m.student_id=? AND c.deleted_at IS NULL ORDER BY c.class_code',(r['id'],))]
                else: r['student_count']=db.execute('SELECT count(*) FROM class_students WHERE class_id=?',(r['id'],)).fetchone()[0]
                result.append(r)
            return result

    def detail(self,kind,identity):
        with self.connection() as db: self.require(db,kind,identity)
        r=next(row for row in self.list_entities(kind) if row['id']==identity)
        if kind=='classes':
            r['students']=[s for s in self.list_entities('students') if any(c['id']==identity for c in s['classes'])]
        else:
            with self.connection() as db:
                r['legacy_label']=(db.execute('SELECT dataset_label FROM legacy_mappings WHERE student_id=?',(identity,)).fetchone() or [None])[0]
                r['faces']=[dict(f) for f in db.execute('SELECT id,embedding_reference,created_at FROM face_embeddings WHERE student_id=? ORDER BY id DESC',(identity,))]
        return r

    def save(self,kind,data,identity=None):
        if not isinstance(data,dict): raise DomainError('Nội dung phải là JSON object.')
        fields=(dict(student_code=text(data.get('student_code'),'Mã sinh viên',64).upper(),full_name=text(data.get('full_name'),'Họ tên')) if kind=='students' else
                dict(class_code=text(data.get('class_code'),'Mã lớp',64).upper(),class_name=text(data.get('class_name'),'Tên lớp'),description=text(data.get('description',''),'Mô tả',1000,False)))
        stamp=now()
        with self.connection() as db:
            if identity is None:
                identity=db.execute(f'INSERT INTO {kind} ({",".join(fields)},created_at,updated_at) VALUES ({",".join("?" for _ in range(len(fields)+2))})',(*fields.values(),stamp,stamp)).lastrowid
            else:
                previous=self.require(db,kind,identity)
                if kind=='students' and previous['student_code']!=fields['student_code']:
                    fields['student_code_is_temporary']=int(fields['student_code'].startswith('DEMO-'))
                db.execute(f'UPDATE {kind} SET {",".join(k+"=?" for k in fields)},updated_at=? WHERE id=?',(*fields.values(),stamp,identity))
                if kind=='students': self.bump(db)
        return self.detail(kind,identity)

    def delete(self,kind,identity):
        with self.connection() as db:
            self.require(db,kind,identity)
            column='student_id' if kind=='students' else 'class_id'
            if db.execute(f'SELECT 1 FROM class_students WHERE {column}=?',(identity,)).fetchone():
                raise DomainError('Hãy gỡ các liên kết lớp–sinh viên trước khi xóa hồ sơ.',409)
            if kind=='students' and db.execute('SELECT 1 FROM face_embeddings WHERE student_id=?',(identity,)).fetchone():
                raise DomainError('Sinh viên đã có dữ liệu khuôn mặt; không cho phép xóa hồ sơ.',409)
            # Archive instead of physically deleting future historical references.
            db.execute(f'UPDATE {kind} SET deleted_at=?,updated_at=? WHERE id=?',(now(),now(),identity))

    def membership(self,class_id,student_id,remove=False):
        if isinstance(student_id,bool) or not isinstance(student_id,int) or student_id<=0: raise DomainError('Mã hồ sơ sinh viên không hợp lệ.')
        with self.connection() as db:
            self.require(db,'classes',class_id);self.require(db,'students',student_id)
            if remove: db.execute('DELETE FROM class_students WHERE class_id=? AND student_id=?',(class_id,student_id))
            else: db.execute('INSERT INTO class_students(class_id,student_id) VALUES (?,?)',(class_id,student_id))

    def legacy_options(self):
        with self.connection() as db: mapped={r['dataset_label']:r['student_id'] for r in db.execute('SELECT * FROM legacy_mappings')}
        return [dict(dataset_label=label,embedding_count=sum(r['student_id']==label for r in self.legacy),student_id=mapped.get(label)) for label in self.labels]

    def map_legacy(self,student_id,label,confirmed):
        if confirmed is not True: raise DomainError('Cần xác nhận thủ công tên dataset đúng với hồ sơ sinh viên.')
        if not isinstance(label,str) or label not in self.labels: raise DomainError('Tên dataset không hợp lệ.')
        with self.connection() as db:
            if self.link_legacy_in_transaction(db,student_id,label): self.bump(db)

    def link_legacy_in_transaction(self,db,student_id,label):
        """Shared idempotent linking; membership is decided by record.student_id, not index.

        The index is only the existing reference format. Never infer the student
        from order, names, recognition scores or vectors.
        """
        self.require(db,'students',student_id)
        mapping=db.execute('SELECT student_id FROM legacy_mappings WHERE dataset_label=?',(label,)).fetchone()
        changed=0
        if mapping:
            if mapping['student_id']!=student_id: raise DomainError('Nhãn dataset đã liên kết với hồ sơ khác.',409)
        else:
            db.execute('INSERT INTO legacy_mappings(dataset_label,student_id) VALUES (?,?)',(label,student_id));changed+=1
        stamp=now()
        for index,record in enumerate(self.legacy):
            if record['student_id']!=label: continue
            reference=f'legacy:{index}'
            linked=db.execute('SELECT student_id,embedding_json FROM face_embeddings WHERE embedding_reference=?',(reference,)).fetchone()
            if linked:
                if linked['student_id']!=student_id or linked['embedding_json'] is not None:
                    raise DomainError('Tham chiếu vector gốc bị liên kết sai hồ sơ; không tự ghi đè.',409)
                continue
            pipeline={k:record[k] for k in ('model_name','detector_backend','align','normalization')}
            db.execute('INSERT INTO face_embeddings(student_id,embedding_reference,pipeline_json,created_at,attribution_confirmed_at) VALUES (?,?,?,?,?)',
                (student_id,reference,json.dumps(pipeline),stamp,stamp));changed+=1
        return changed

    def add_embedding(self,student_id,embedding,pipeline,image_sha256):
        vector=np.asarray(embedding,dtype=float)
        if vector.shape!=(512,) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0: raise DomainError('Embedding ArcFace không hợp lệ.',422)
        stamp=now()
        with self.connection() as db:
            self.require(db,'students',student_id)
            identity=db.execute('INSERT INTO face_embeddings(student_id,embedding_json,pipeline_json,image_sha256,created_at,attribution_confirmed_at) VALUES (?,?,?,?,?,?)',
                (student_id,json.dumps(vector.tolist(),allow_nan=False),json.dumps(pipeline),image_sha256,stamp,stamp)).lastrowid
            self.bump(db)
            return identity

    def gallery_revision(self):
        with self.connection() as db: return db.execute("SELECT value FROM management_meta WHERE key='gallery_revision'").fetchone()[0]

    def gallery_snapshot(self):
        with self.connection() as db:
            db.execute('BEGIN')
            revision=db.execute("SELECT value FROM management_meta WHERE key='gallery_revision'").fetchone()[0]
            names={r['id']:f"{r['student_code']} · {r['full_name']}" for r in db.execute('SELECT * FROM students WHERE deleted_at IS NULL')}
            mappings={r['dataset_label']:names[r['student_id']] for r in db.execute('SELECT * FROM legacy_mappings') if r['student_id'] in names}
            records=[dict(r,student_id=mappings.get(r['student_id'],r['student_id'])) for r in self.legacy]
            for r in db.execute('SELECT * FROM face_embeddings WHERE embedding_json IS NOT NULL'):
                if r['student_id'] in names: records.append(dict(student_id=names[r['student_id']],embedding=json.loads(r['embedding_json']),source=f"sqlite/face_embeddings/{r['id']}"))
            return records,revision
