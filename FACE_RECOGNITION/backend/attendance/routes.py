"""Attendance API: AI results never write attendance records."""
import csv
from io import StringIO
from flask import Blueprint,jsonify,request,render_template,Response
from ..database.db import DomainError


def attendance_blueprint(service,token,client_config):
    bp=Blueprint('attendance',__name__)
    @bp.errorhandler(DomainError)
    def error(exc):return jsonify(error=dict(code='ATTENDANCE_ERROR',message=str(exc))),exc.status
    def class_filter():
        value=request.args.get('class_id')
        if value in (None,''):return None
        try:identity=int(value)
        except ValueError:raise DomainError('ID lớp lọc không hợp lệ.')
        return service.identity(identity,'Lớp')
    @bp.get('/attendance')
    def camera():return render_template('index.html',client_config=client_config(),attendance_mode=True)
    @bp.get('/history')
    @bp.get('/history/<int:identity>')
    @bp.get('/reports')
    def page(identity=None):return render_template('attendance_pages.html',csrf_token=token)
    @bp.route('/api/attendance/sessions',methods=['GET','POST'])
    def sessions():
        if request.method=='POST':return jsonify(service.create(request.get_json(silent=True))),201
        return jsonify(items=service.history(class_filter(),request.args.get('date')))
    @bp.get('/api/attendance/sessions/<int:identity>')
    def detail(identity):return jsonify(service.detail(identity))
    @bp.post('/api/attendance/sessions/<int:identity>/confirm')
    def confirm(identity):
        data=request.get_json(silent=True)
        return jsonify(service.confirm(identity,data.get('student_id') if isinstance(data,dict) else None,data))
    @bp.post('/api/attendance/sessions/<int:identity>/finish')
    def finish(identity):return jsonify(service.finish(identity))
    @bp.put('/api/attendance/sessions/<int:identity>/records/<int:student_id>')
    def edit(identity,student_id):return jsonify(service.edit(identity,student_id,request.get_json(silent=True)))
    @bp.get('/api/attendance/reports')
    def reports():return jsonify(service.reports(class_filter()))
    @bp.get('/api/attendance/reports.csv')
    def export():
        report=service.reports(class_filter());stream=StringIO();writer=csv.writer(stream)
        writer.writerow(['Mã sinh viên','Họ tên (snapshot)','Số buổi','Có mặt','Vắng mặt','Tỷ lệ'])
        def safe(v):
            value=str(v)
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        for r in report['by_student']:writer.writerow([safe(r['student_code']),safe(r['full_name']),r['total'],r['present'],r['absent'],f"{r['attendance_rate']*100:.2f}%"])
        return Response('\ufeff'+stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=attendance.csv'})
    return bp
