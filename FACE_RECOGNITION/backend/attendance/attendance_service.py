"""Attendance transactions independent of the recognition model or detector."""
from datetime import date,datetime
from zoneinfo import ZoneInfo
import time

from ..database.db import DomainError,now,text


class AttendanceService:
    def __init__(self,store):
        self.store=store
        with store.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS attendance_sessions (
                id INTEGER PRIMARY KEY,class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE RESTRICT,
                class_code_snapshot TEXT NOT NULL,class_name_snapshot TEXT NOT NULL,
                session_date TEXT NOT NULL,session_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('IN_PROGRESS','COMPLETED')),
                started_at TEXT NOT NULL,ended_at TEXT,created_at TEXT NOT NULL,
                request_key TEXT NOT NULL UNIQUE);
            CREATE UNIQUE INDEX IF NOT EXISTS ix_one_open_session ON attendance_sessions(class_id) WHERE status='IN_PROGRESS';
            CREATE TABLE IF NOT EXISTS attendance_records (
                id INTEGER PRIMARY KEY,session_id INTEGER NOT NULL REFERENCES attendance_sessions(id) ON DELETE RESTRICT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
                student_code_snapshot TEXT NOT NULL,full_name_snapshot TEXT NOT NULL,
                attendance_status TEXT CHECK(attendance_status IN ('PRESENT','ABSENT')),
                recognition_source TEXT CHECK(recognition_source IN ('MANUAL','AI_VERIFIED')),
                confirmed_at TEXT,updated_at TEXT NOT NULL,UNIQUE(session_id,student_id));
            CREATE TABLE IF NOT EXISTS attendance_edits (
                id INTEGER PRIMARY KEY,record_id INTEGER NOT NULL REFERENCES attendance_records(id) ON DELETE RESTRICT,
                old_status TEXT NOT NULL,new_status TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_attendance_date ON attendance_sessions(session_date,class_id);
            ''')

    @staticmethod
    def identity(value,label='ID'):
        if isinstance(value,bool) or not isinstance(value,int) or value<=0:raise DomainError(f'{label} không hợp lệ.')
        return value

    @staticmethod
    def session(db,identity,require_open=False):
        row=db.execute('SELECT * FROM attendance_sessions WHERE id=?',(identity,)).fetchone()
        if not row:raise DomainError('Không tìm thấy buổi điểm danh.',404)
        if require_open and row['status']!='IN_PROGRESS':raise DomainError('Buổi điểm danh đã kết thúc; không nhận xác nhận mới.',409)
        return dict(row)

    def context(self,identity):
        with self.store.connection() as db:
            session=self.session(db,self.identity(identity),True)
            session['student_ids']=[r[0] for r in db.execute('SELECT student_id FROM attendance_records WHERE session_id=?',(identity,))]
            return session

    def create(self,data):
        if not isinstance(data,dict):raise DomainError('Nội dung phải là JSON object.')
        class_id=self.identity(data.get('class_id'),'Lớp')
        name=text(data.get('session_name'),'Tên buổi',160)
        key=text(data.get('request_key'),'Mã yêu cầu',120)
        session_date=data.get('session_date',datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat())
        try:
            if not isinstance(session_date,str) or date.fromisoformat(session_date).isoformat()!=session_date:raise ValueError()
        except ValueError:raise DomainError('Ngày buổi học phải có dạng YYYY-MM-DD.')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT * FROM attendance_sessions WHERE request_key=?',(key,)).fetchone()
            if existing:
                if existing['class_id']!=class_id or existing['session_name']!=name or existing['session_date']!=session_date:raise DomainError('Mã yêu cầu đã dùng với nội dung khác.',409)
                sid=existing['id']
            else:
                classroom=self.store.require(db,'classes',class_id)
                if db.execute("SELECT 1 FROM attendance_sessions WHERE class_id=? AND status='IN_PROGRESS'",(class_id,)).fetchone():raise DomainError('Lớp đang có buổi chưa kết thúc. Mở lại từ lịch sử để tiếp tục.',409)
                roster=db.execute('SELECT s.* FROM students s JOIN class_students m ON m.student_id=s.id WHERE m.class_id=? AND s.deleted_at IS NULL ORDER BY s.id',(class_id,)).fetchall()
                if not roster:raise DomainError('Lớp chưa có sinh viên. Hãy phân lớp trước khi bắt đầu.',422)
                stamp=now()
                sid=db.execute("INSERT INTO attendance_sessions(class_id,class_code_snapshot,class_name_snapshot,session_date,session_name,status,started_at,created_at,request_key) VALUES (?,?,?,?,?,'IN_PROGRESS',?,?,?)",(class_id,classroom['class_code'],classroom['class_name'],session_date,name,stamp,stamp,key)).lastrowid
                db.executemany('INSERT INTO attendance_records(session_id,student_id,student_code_snapshot,full_name_snapshot,updated_at) VALUES (?,?,?,?,?)',[(sid,s['id'],s['student_code'],s['full_name'],stamp) for s in roster])
        return self.detail(sid)

    def detail(self,identity):
        with self.store.connection() as db:
            db.execute('BEGIN')
            result=self.session(db,identity)
            result['records']=[dict(r) for r in db.execute('SELECT r.*,(SELECT count(*) FROM face_embeddings f WHERE f.student_id=r.student_id) AS face_count FROM attendance_records r WHERE session_id=? ORDER BY student_code_snapshot',(identity,))]
            result['stats']=self.counts(result['records'])
            result['edits']=[dict(r) for r in db.execute('SELECT e.* FROM attendance_edits e JOIN attendance_records r ON e.record_id=r.id WHERE r.session_id=? ORDER BY e.id DESC',(identity,))]
            return result

    @staticmethod
    def counts(records):
        return dict(total=len(records),present=sum(r['attendance_status']=='PRESENT' for r in records),absent=sum(r['attendance_status']=='ABSENT' for r in records),pending=sum(r['attendance_status'] is None for r in records))

    def confirm(self,identity,student_id,data):
        self.identity(student_id,'Sinh viên')
        if not isinstance(data,dict) or data.get('confirmed') is not True or data.get('recognition_source','MANUAL')!='MANUAL':raise DomainError('Cần con người xác nhận rõ ràng; chỉ nhận nguồn MANUAL.')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.session(db,identity,True)
            record=db.execute('SELECT * FROM attendance_records WHERE session_id=? AND student_id=?',(identity,student_id)).fetchone()
            if not record:raise DomainError('Sinh viên không thuộc danh sách của buổi này.',422)
            changed=record['attendance_status']!='PRESENT'
            if changed:
                stamp=now();db.execute("UPDATE attendance_records SET attendance_status='PRESENT',recognition_source='MANUAL',confirmed_at=?,updated_at=? WHERE id=?",(stamp,stamp,record['id']))
        return dict(changed=changed,session=self.detail(identity))

    def finish(self,identity):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');session=self.session(db,identity)
            if session['status']=='IN_PROGRESS':
                stamp=now()
                db.execute("UPDATE attendance_records SET attendance_status='ABSENT',recognition_source='MANUAL',updated_at=? WHERE session_id=? AND attendance_status IS NULL",(stamp,identity))
                db.execute("UPDATE attendance_sessions SET status='COMPLETED',ended_at=? WHERE id=?",(stamp,identity))
        return self.detail(identity)

    def confirm_reviewed(self,identity,student_ids):
        """Human-approved static-media proposal, atomically deduplicated."""
        changed=0
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.session(db,self.identity(identity),True)
            records=[]
            for sid in set(student_ids):
                self.identity(sid,'Sinh viên')
                r=db.execute('SELECT * FROM attendance_records WHERE session_id=? AND student_id=?',(identity,sid)).fetchone()
                if not r:raise DomainError('Sinh viên không thuộc danh sách buổi này.',422)
                records.append(r)
            for r in records:
                if r['attendance_status']!='PRESENT':
                    stamp=now();db.execute("UPDATE attendance_records SET attendance_status='PRESENT',recognition_source='MANUAL',confirmed_at=?,updated_at=? WHERE id=?",(stamp,stamp,r['id']));changed+=1
        return dict(changed=changed,session=self.detail(identity))

    def confirm_verified(self,identity,student_ids,cancelled,received_at,continuity_check=None):
        """Internal only: no HTTP route accepts a caller's identity_confirmed flag."""
        self.identity(identity)
        changed=0
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.session(db,identity,True)
            fresh=lambda:bool(continuity_check and continuity_check())
            if cancelled() or not fresh():
                return dict(enabled=True,changed=False,reason='current_track_proof_missing_lost_or_camera_stopped')
            # Check all membership before any write; transaction rolls back on an outsider.
            records=[]
            for sid in set(student_ids):
                self.identity(sid,'Sinh viên')
                r=db.execute('SELECT * FROM attendance_records WHERE session_id=? AND student_id=?',(identity,sid)).fetchone()
                if not r:raise DomainError('Sinh viên không thuộc buổi này.',422)
                records.append(r)
            for r in records:
                if cancelled() or not fresh():raise DomainError('Liên kết khuôn mặt đã mất hoặc camera đã tắt.',409)
                if r['attendance_status']!='PRESENT':
                    stamp=now();db.execute("UPDATE attendance_records SET attendance_status='PRESENT',recognition_source='AI_VERIFIED',confirmed_at=?,updated_at=? WHERE id=?",(stamp,stamp,r['id']));changed+=1
        return dict(enabled=True,changed=bool(changed),session=self.detail(identity))

    def edit(self,identity,student_id,data):
        if not isinstance(data,dict) or data.get('attendance_status') not in ('PRESENT','ABSENT') or data.get('confirmed') is not True:raise DomainError('Cần xác nhận trạng thái Có mặt/Vắng mặt hợp lệ.')
        reason=text(data.get('reason'),'Lý do sửa',500)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');session=self.session(db,identity)
            if session['status']!='COMPLETED':raise DomainError('Chỉ sửa lịch sử khi buổi đã kết thúc.',409)
            r=db.execute('SELECT * FROM attendance_records WHERE session_id=? AND student_id=?',(identity,student_id)).fetchone()
            if not r:raise DomainError('Sinh viên không thuộc buổi này.',422)
            if r['attendance_status']!=data['attendance_status']:
                stamp=now()
                db.execute('INSERT INTO attendance_edits(record_id,old_status,new_status,reason,created_at) VALUES (?,?,?,?,?)',(r['id'],r['attendance_status'],data['attendance_status'],reason,stamp))
                db.execute("UPDATE attendance_records SET attendance_status=?,recognition_source='MANUAL',confirmed_at=?,updated_at=? WHERE id=?",(data['attendance_status'],stamp,stamp,r['id']))
        return self.detail(identity)

    def history(self,class_id=None,day=None):
        clauses=[];args=[]
        if class_id is not None:clauses.append('s.class_id=?');args.append(class_id)
        if day:
            try:date.fromisoformat(day)
            except ValueError:raise DomainError('Ngày lọc không hợp lệ.')
            clauses.append('s.session_date=?');args.append(day)
        where='WHERE '+' AND '.join(clauses) if clauses else ''
        with self.store.connection() as db:
            return [dict(r) for r in db.execute(f'''SELECT s.*,count(r.id) AS total,
                coalesce(sum(r.attendance_status='PRESENT'),0) AS present,coalesce(sum(r.attendance_status='ABSENT'),0) AS absent
                FROM attendance_sessions s LEFT JOIN attendance_records r ON r.session_id=s.id {where} GROUP BY s.id ORDER BY s.id DESC''',args)]

    def reports(self,class_id=None):
        clauses="s.status='COMPLETED'";args=[]
        if class_id is not None:clauses+=' AND s.class_id=?';args.append(class_id)
        with self.store.connection() as db:
            db.execute('BEGIN')
            sessions=[dict(r) for r in db.execute('SELECT s.* FROM attendance_sessions s WHERE '+clauses,args)]
            students=[dict(r) for r in db.execute(f'''SELECT r.student_id, max(r.student_code_snapshot) AS student_code,
                max(r.full_name_snapshot) AS full_name,count(*) AS total,sum(r.attendance_status='PRESENT') AS present,
                sum(r.attendance_status='ABSENT') AS absent FROM attendance_records r JOIN attendance_sessions s ON s.id=r.session_id
                WHERE {clauses} GROUP BY r.student_id ORDER BY student_code''',args)]
            classes=[dict(r) for r in db.execute(f'''SELECT s.class_id,max(s.class_code_snapshot) AS class_code,max(s.class_name_snapshot) AS class_name,
                count(*) AS total,sum(r.attendance_status='PRESENT') AS present,sum(r.attendance_status='ABSENT') AS absent
                FROM attendance_records r JOIN attendance_sessions s ON s.id=r.session_id WHERE {clauses} GROUP BY s.class_id''',args)]
            for row in students+classes:row['attendance_rate']=row['present']/row['total'] if row['total'] else None
            overview=dict(classes=db.execute('SELECT count(*) FROM classes WHERE deleted_at IS NULL').fetchone()[0],students=db.execute('SELECT count(*) FROM students WHERE deleted_at IS NULL').fetchone()[0],registered_students=db.execute('SELECT count(DISTINCT f.student_id) FROM face_embeddings f JOIN students s ON s.id=f.student_id WHERE s.deleted_at IS NULL').fetchone()[0])
            return dict(**overview,completed_sessions=len(sessions),present=sum(r['present'] for r in students),absent=sum(r['absent'] for r in students),by_student=students,by_class=classes)
