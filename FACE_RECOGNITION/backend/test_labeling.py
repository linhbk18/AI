"""Human ground truth for existing group photos. Never writes the gallery or attendance DB."""
import csv
from datetime import datetime,timezone
import hashlib
import json
import re
from pathlib import Path
import threading
import time

import cv2
import numpy as np
from PIL import Image,ImageOps,ImageDraw,ImageFont
from flask import Blueprint,jsonify,request,render_template,send_file

from .config import ROOT
from .database.db import DomainError
from .face_recognition.verification_workflow import write,upper95
from .attendance.continuity import iou

BASE=ROOT/'outputs/test_labeling'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provenance():
    """Exact references are evidence of exposure, not proof of threshold fitting."""
    evidence=[]
    for folder in (ROOT/'tests',ROOT/'backend/face_recognition',ROOT/'outputs/camera_optimization',ROOT/'outputs/realtime_camera_ui'):
        for p in folder.rglob('*'):
            if p.suffix not in ('.py','.json','.csv','.md') or p.stat().st_size>8_000_000:continue
            text=p.read_text(encoding='utf-8',errors='replace')
            normalized=text.replace('\\','/').replace('//','/')
            names=[q.name for q in (ROOT/'DATASET/Test').rglob('*') if q.is_file() and 'DATASET/Test/'+q.name in normalized]
            if names:evidence.append(dict(file=p.relative_to(ROOT).as_posix(),images=names))
    prior=ROOT/'outputs/test_report.csv';human=0
    if prior.exists():
        with prior.open(encoding='utf-8-sig',newline='') as f:human=sum(bool(row.get('ground_truth','').strip()) for row in csv.DictReader(f))
    return dict(prior_all_image_prediction_report='outputs/test_report.csv',prior_human_ground_truth_rows=human,
        known_pipeline_probe_images=['Group01.jpg','Group10.jpg'],references=evidence,
        threshold_fitting_with_human_test_labels_proven=False,
        independence='not_established; keep Test for calibration/diagnostic, not official holdout',
        reason='All Test images already had predictions inspected; some were repeatedly used in pipeline benchmarking. Human labels are currently absent.')


class GroupLabeling:
    def __init__(self,engine,store,base=BASE):
        self.engine=engine;self.store=store;self.base=Path(base);self.lock=threading.RLock()
        self.job=dict(state='idle',processed=0,total=0,error=None)
        self.base.mkdir(parents=True,exist_ok=True)

    def read(self,name,default):
        p=self.base/name
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default

    def people(self):return [dict(student_code=p['student_code'],full_name=p['full_name']) for p in self.store.list_entities('students')]

    def state(self):
        with self.lock:
            manifest=self.read('manifest.json',dict(images=[]))
            labels=self.read('labels.json',dict(revision=0,faces={}))
            counts={key:sum(v['label']==key for v in labels['faces'].values()) for key in ('UNLABELED','UNKNOWN','IGNORE')}
            counts['KNOWN']=sum(v['label'] not in ('UNLABELED','UNKNOWN','IGNORE') for v in labels['faces'].values())
            report=self.read('evaluation.json',{})
            summary={k:report.get(k) for k in ('known_faces','unknown_faces','correct_known','known_closed_set_accuracy','known_detection_misses','unknown_detection_misses','official_threshold_created')} if report.get('label_revision')==labels['revision'] else None
            return dict(manifest=manifest,labels=labels,people=self.people(),counts=counts,job=dict(self.job),evaluation_summary=summary)

    def start(self,operation,force=False):
        with self.lock:
            if self.job['state']=='running':raise DomainError('Đang có tác vụ chuẩn bị/đánh giá.',429)
            if not self.engine.health()['ready']:raise DomainError('Mô hình đang tải.',503)
            if operation=='prepare' and (self.base/'manifest.json').exists():raise DomainError('Đã có bộ ảnh gắn nhãn; không tạo lại để tránh mất nhãn.',409)
            if operation=='evaluate' and not any(r['label'] not in ('UNLABELED','IGNORE') for r in self.read('labels.json',{'faces':{}})['faces'].values()):raise DomainError('Chưa có nhãn thật; cần lưu mã sinh viên/UNKNOWN trước khi đánh giá.',422)
            self.job=dict(state='running',operation=operation,force_recompute=bool(force),processed=0,total=len([p for p in (ROOT/'DATASET/Test').rglob('*') if p.is_file()]),error=None)
            threading.Thread(target=self.run,args=(operation,),daemon=True).start()
            return dict(self.job)

    def run(self,operation):
        acquired=False
        try:
            acquired=self.engine._guard.acquire(blocking=False)
            if not acquired:raise DomainError('AI đang bận; hãy tắt camera/video rồi thử lại.',429)
            self.engine._refresh_gallery_locked()
            if operation=='prepare':self.prepare()
            else:self.evaluate()
            with self.lock:self.job['state']='completed'
        except Exception as exc:
            with self.lock:self.job.update(state='failed',error=str(exc))
        finally:
            if acquired:self.engine._guard.release()

    def prepare(self):
        source=ROOT/'DATASET/Test';paths=sorted(p for p in source.rglob('*') if p.is_file())
        images=[];labels=dict(revision=0,faces={});assets=self.base/'assets';assets.mkdir(exist_ok=True)
        history=self.read('../test_summary.json',{})
        previous={r['image']:r['detected_faces'] for r in history.get('images',[])}
        for p in paths:
            with Image.open(p) as im:im=ImageOps.exif_transpose(im).convert('RGB');original_size=im.size;im.thumbnail((1280,1280));rgb=np.asarray(im).copy()
            bgr=np.ascontiguousarray(rgb[:,:,::-1]);h,w=bgr.shape[:2]
            regions=self.engine.pipeline.native_retina_faces(bgr,max_dimension=1280)
            scale=min(1,640/w,480/h);camera=cv2.resize(bgr,(round(w*scale),round(h*scale))) if scale<1 else bgr
            camera_regions=self.engine.pipeline.native_retina_faces(camera,max_dimension=480)
            key=sha(p)[:20];plain=key+'.jpg';numbered=key+'-numbered.jpg';im=Image.fromarray(rgb);im.save(assets/plain,quality=95)
            draw=ImageDraw.Draw(im);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',max(16,round(w/60)))
            faces=[]
            for n,area in enumerate(sorted(regions,key=lambda a:(a.y,a.x)),1):
                x,y=max(0,int(area.x)),max(0,int(area.y));right,bottom=min(w,int(area.x+area.w)),min(h,int(area.y+area.h))
                if right<=x or bottom<=y:continue
                fid=key+'-'+str(n);box=dict(x=x,y=y,w=right-x,h=bottom-y)
                faces.append(dict(face_id=fid,number=n,box=box,origin='retinaface'))
                labels['faces'][fid]=dict(label='UNLABELED',unknown_person_id='',note='',label_source='human_required')
                draw.rectangle((x,y,right,bottom),outline='#2176AE',width=3);draw.rectangle((x,max(0,y-26),x+44,max(0,y-26)+26),fill='#142D4E');draw.text((x+4,max(0,y-26)),str(n),fill='white',font=font)
            im.save(assets/numbered,quality=95)
            images.append(dict(image_id=key,source=p.relative_to(ROOT).as_posix(),name=p.name,sha256=sha(p),bytes=p.stat().st_size,
                original_size=original_size,frame=dict(width=w,height=h),asset=plain,numbered_asset=numbered,faces=faces,
                annotation_detected_faces=len(faces),camera_detected_faces=len(camera_regions),historical_detected_faces=previous.get(p.name),
                official_holdout_eligible=False,split='calibration_or_diagnostic_only'))
            with self.lock:self.job.update(processed=len(images),total=len(paths))
        manifest=dict(schema_version=1,source_directory='DATASET/Test',annotation_detector='retinaface_native_experimental',annotation_max_dimension=1280,
            camera_profile='retinaface_scaled_experimental',camera_max_input=[640,480],images=images,provenance=provenance())
        with self.lock:
            write(self.base/'manifest.json',manifest);write(self.base/'labels.json',labels);self.export_csv(manifest,labels)

    def export_csv(self,manifest,labels):
        p=self.base/'labels.csv';temp=p.with_suffix('.csv.tmp')
        with temp.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['source','source_sha256','face_id','face_number','x','y','w','h','label','unknown_person_id','note','label_source']);writer.writeheader()
            for image in manifest['images']:
                for face in image['faces']:
                    row=labels['faces'][face['face_id']]
                    writer.writerow(dict(source=image['source'],source_sha256=image['sha256'],face_id=face['face_id'],face_number=face['number'],**face['box'],**{k:row.get(k,'') for k in ('label','unknown_person_id','note','label_source')}))
        temp.replace(p)

    def save_labels(self,data):
        with self.lock:
            if self.job['state']=='running':raise DomainError('Chờ tác vụ kết thúc trước khi sửa nhãn.',409)
            labels=self.read('labels.json',dict(revision=0,faces={}))
            if not isinstance(data,dict) or data.get('revision')!=labels['revision']:raise DomainError('Nhãn đã thay đổi ở cửa sổ khác; tải lại trước khi lưu.',409)
            changes=data.get('faces')
            if not isinstance(changes,dict) or len(changes)>2000:raise DomainError('Danh sách nhãn không hợp lệ.')
            allowed={p['student_code'] for p in self.people()}|{'UNLABELED','UNKNOWN','IGNORE'}
            for fid,row in changes.items():
                if fid not in labels['faces'] or not isinstance(row,dict) or row.get('label') not in allowed:raise DomainError('Face ID hoặc nhãn không hợp lệ.')
                if any(not isinstance(row.get(k,''),str) or len(row.get(k,''))>300 for k in ('unknown_person_id','note')):raise DomainError('Ghi chú hoặc mã người lạ không hợp lệ.')
                code=row.get('unknown_person_id','').strip()
                if code and (row['label']!='UNKNOWN' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',code)):
                    raise DomainError('Mã ẩn danh chỉ dành cho UNKNOWN, gồm 1–64 ký tự chữ/số, dấu chấm, gạch dưới hoặc gạch ngang.')
            for fid,row in changes.items():labels['faces'][fid]=dict(label=row['label'],unknown_person_id=row.get('unknown_person_id','').strip(),note=row.get('note','').strip(),label_source='human',updated_at=datetime.now(timezone.utc).isoformat())
            labels['revision']+=1;write(self.base/'labels.json',labels);self.export_csv(self.read('manifest.json',{}),labels)
            return labels

    def add_box(self,data):
        with self.lock:
            if self.job['state']=='running':raise DomainError('Tác vụ đang chạy.',409)
            manifest=self.read('manifest.json',{});labels=self.read('labels.json',{})
            if data.get('revision')!=labels.get('revision'):raise DomainError('Nhãn đã thay đổi; tải lại.',409)
            image=next((i for i in manifest['images'] if i['image_id']==data.get('image_id')),None)
            b=data.get('box',{})
            if not image or any(isinstance(b.get(k),bool) or not isinstance(b.get(k),(int,float)) or not np.isfinite(b[k]) for k in ('x','y','w','h')):raise DomainError('Khung không hợp lệ.')
            b={k:round(b[k]) for k in ('x','y','w','h')}
            if b['x']<0 or b['y']<0 or b['w']<5 or b['h']<5 or b['x']+b['w']>image['frame']['width'] or b['y']+b['h']>image['frame']['height']:raise DomainError('Khung phải nằm trong ảnh và có kích thước hợp lệ.')
            n=max((f['number'] for f in image['faces']),default=0)+1;fid=image['image_id']+'-manual-'+str(n)
            image['faces'].append(dict(face_id=fid,number=n,box=b,origin='human_added'))
            labels['faces'][fid]=dict(label='UNLABELED',unknown_person_id='',note='',label_source='human_required');labels['revision']+=1
            write(self.base/'manifest.json',manifest);write(self.base/'labels.json',labels);self.export_csv(manifest,labels)
            with Image.open(self.base/'assets'/image['asset']) as original:
                numbered=original.convert('RGB');draw=ImageDraw.Draw(numbered);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18)
                for face in image['faces']:
                    rect=face['box'];x,y=rect['x'],rect['y'];draw.rectangle((x,y,x+rect['w'],y+rect['h']),outline='#2176AE',width=3);draw.rectangle((x,max(0,y-26),x+44,max(0,y-26)+26),fill='#142D4E');draw.text((x+4,max(0,y-26)),str(face['number']),fill='white',font=font)
                numbered.save(self.base/'assets'/image['numbered_asset'],quality=95)
            return self.state()

    def evaluate(self):
        manifest=self.read('manifest.json',{});labels=self.read('labels.json',dict(faces={}))
        valid=[(im,face,labels['faces'][face['face_id']]) for im in manifest.get('images',[]) for face in im['faces'] if labels['faces'][face['face_id']]['label'] not in ('UNLABELED','IGNORE')]
        if not valid:raise DomainError('Chưa có nhãn người dùng. Chọn đúng mã sinh viên/UNKNOWN trước; không dùng dự đoán AI làm nhãn.')
        with self.lock:self.job['total']=len({im['image_id'] for im,_,_ in valid})
        cache=self.read('score_cache.json',{});signature=self.engine.matcher.signature;rows=[]
        for image in manifest['images']:
            if not any(im['image_id']==image['image_id'] for im,_,_ in valid):continue
            path=ROOT/image['source']
            if sha(path)!=image['sha256']:raise DomainError('Ảnh nguồn đã đổi; cần đối chiếu lại nhãn.',409)
            key=sha(path)+signature+manifest['camera_profile']
            if key not in cache or self.job.get('force_recompute'):
                with Image.open(path) as im:im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((640,480));bgr=np.asarray(im)[:,:,::-1].copy()
                result,_=self.engine.pipeline.run(bgr,manifest['camera_profile'])
                detections=[]
                for face in result:
                    vector=np.asarray(face['embedding'],np.float64);vector/=np.linalg.norm(vector);scores=np.clip(self.engine.matcher.matrix@vector,-1,1)
                    per={}
                    for name in self.engine.matcher.students:
                        idx=[i for i,r in enumerate(self.engine.matcher.records) if r['student_id']==name]
                        code=self.engine._public_identities[name]['student_code'];per[code]=float(max(scores[idx]))
                    ranked=sorted(per,key=lambda code:(-per[code],code));area=face['facial_area']
                    detections.append(dict(box={k:float(area[k])/(bgr.shape[1] if k in ('x','w') else bgr.shape[0]) for k in ('x','y','w','h')},prediction=ranked[0],similarity=per[ranked[0]],second=ranked[1],second_similarity=per[ranked[1]],margin=per[ranked[0]]-per[ranked[1]],per_student_similarity=per))
                cache[key]=detections;write(self.base/'score_cache.json',cache)
            detected=cache[key]
            ground=[(face,label) for im,face,label in valid if im['image_id']==image['image_id']]
            possibilities=[]
            for face,label in ground:
                b=face['box'];size=image['frame'];norm=[b['x']/size['width'],b['y']/size['height'],b['w']/size['width'],b['h']/size['height']]
                possibilities.append([j for j,d in enumerate(detected) if iou(norm,[d['box'][k] for k in ('x','y','w','h')])>=.35])
            for (face,label),matches in zip(ground,possibilities):
                j=matches[0] if len(matches)==1 and sum(matches[0] in group for group in possibilities)==1 else None
                row=dict(source=image['source'],face_id=face['face_id'],ground_truth=label['label'],unknown_person_id=label.get('unknown_person_id',''),detected=j is not None,
                    historical_holdout_eligible=False)
                if j is not None:row.update(detected[j])
                rows.append(row)
            with self.lock:self.job['processed']+=1
        known=[r for r in rows if r['ground_truth']!='UNKNOWN'];unknown=[r for r in rows if r['ground_truth']=='UNKNOWN']
        report=dict(status='diagnostic_only_not_independent_holdout',pipeline_signature=signature,camera_profile=manifest['camera_profile'],label_revision=labels['revision'],rows=rows,
            known_faces=len(known),unknown_faces=len(unknown),correct_known=sum(r.get('prediction')==r['ground_truth'] for r in known),
            known_detection_misses=sum(not r['detected'] for r in known),unknown_detection_misses=sum(not r['detected'] for r in unknown),
            official_threshold_created=False,reason='Test was already exposed in prior reports/pipeline experiments; no independent evaluation set is established.')
        report['label_file_sha256']=sha(self.base/'labels.json');report['manifest_sha256']=sha(self.base/'manifest.json')
        report['known_closed_set_accuracy']=report['correct_known']/len(known) if known else None
        report['known_misidentified_faces']=sum(r['detected'] and r.get('prediction')!=r['ground_truth'] for r in known)
        report['unmatched_includes_ambiguous_box_association']=True
        report['box_matching_rule']='unique one-to-one IoU >= 0.35; ambiguous associations are not guessed'
        genuine=[r['per_student_similarity'].get(r['ground_truth']) for r in known if r['detected'] and r['ground_truth'] in r['per_student_similarity']]
        impostor=[s for r in known if r['detected'] for code,s in r['per_student_similarity'].items() if code!=r['ground_truth']]
        summary=lambda values:dict(count=len(values),minimum=min(values) if values else None,median=float(np.median(values)) if values else None,maximum=max(values) if values else None)
        report['genuine_similarity']=summary(genuine);report['different_person_similarity']=summary(impostor)
        matched=[r for r in rows if r['detected']]
        candidates=[]
        for score in sorted({r['similarity'] for r in matched}):
            for margin in sorted({r['margin'] for r in matched}):
                accept=lambda r:r['detected'] and r['similarity']>=score and r['margin']>=margin
                fa=sum(accept(r) for r in unknown);fr=sum(not accept(r) for r in known);wrong=sum(accept(r) and r['prediction']!=r['ground_truth'] for r in known)
                candidates.append(dict(min_similarity=score,min_margin=margin,known_faces=len(known),unknown_faces=len(unknown),known_correct_accepted=sum(accept(r) and r['prediction']==r['ground_truth'] for r in known),
                    false_accepts=fa,false_rejections=fr,wrong_known=wrong,far=fa/len(unknown) if unknown else None,frr=fr/len(known) if known else None))
        write(self.base/'evaluation.json',report)
        with (self.base/'evaluation.csv').open('w',encoding='utf-8-sig',newline='') as f:
            fields=['source','face_id','ground_truth','unknown_person_id','detected','prediction','similarity','second','second_similarity','margin','historical_holdout_eligible']
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
        if candidates:
            with (self.base/'threshold_sweep.csv').open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(candidates[0]));writer.writeheader();writer.writerows(candidates)
        if self.base.resolve()==BASE.resolve():
            from .face_recognition.labelled_verification_report import analyse
            analyse()  # Refresh diagnostic proposal only. Never publish/activate a policy.


def labeling_blueprint(engine,store,token):
    manager=GroupLabeling(engine,store);bp=Blueprint('test_labels',__name__)
    @bp.get('/test-labeling')
    def page():return render_template('test_labeling.html',csrf_token=token)
    @bp.get('/api/test-labels')
    def state():return jsonify(manager.state())
    @bp.post('/api/test-labels/prepare')
    def prepare():return jsonify(manager.start('prepare')),202
    @bp.post('/api/test-labels/evaluate')
    def evaluate():
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict):raise DomainError('Nội dung phải là JSON object.')
        return jsonify(manager.start('evaluate',force=data.get('force') is True)),202
    @bp.put('/api/test-labels')
    def save():return jsonify(manager.save_labels(request.get_json(silent=True)))
    @bp.post('/api/test-labels/boxes')
    def add():return jsonify(manager.add_box(request.get_json(silent=True) or {}))
    @bp.get('/api/test-labels/assets/<name>')
    def asset(name):
        allowed={im[k] for im in manager.read('manifest.json',{}).get('images',[]) for k in ('asset','numbered_asset')}
        if name not in allowed:raise DomainError('Không tìm thấy ảnh.',404)
        return send_file(manager.base/'assets'/name,mimetype='image/jpeg',max_age=0)
    return bp,manager
