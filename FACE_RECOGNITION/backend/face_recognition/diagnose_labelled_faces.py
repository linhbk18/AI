"""Real-model diagnostic experiments on exposed Test calibration; never changes runtime policy."""
import csv
import html
import json
import time
from collections import Counter

import cv2
import numpy as np
from PIL import Image,ImageOps

from ..config import ROOT
from ..attendance.continuity import iou
from .verification_workflow import digest,write,load_engine
from .labelled_verification_report import audit

BASE=ROOT/'outputs/test_labeling'
OUT=BASE/'verification_analysis/diagnostics'
VARIANTS=[('live_640','retinaface_scaled_experimental',(640,480)),
          ('native_640','retinaface_native_experimental',(640,480)),
          ('larger_crop_1280','retinaface_scaled_experimental',(1280,960))]


def run():
    check=audit()
    if check['errors']:raise ValueError('Resolve label audit before diagnostics.')
    labels=json.loads((BASE/'labels.json').read_text(encoding='utf-8'))
    manifest=json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
    analysis=json.loads((BASE/'verification_analysis/analysis.json').read_text(encoding='utf-8'))
    if analysis['audit']['label_sha256']!=check['label_sha256']:raise ValueError('Re-evaluate new human labels first.')
    point=analysis['proposed_calibration_point']
    protected=[ROOT/'outputs/face_embeddings.json',ROOT/'outputs/management.sqlite3',BASE/'labels.json',ROOT/'outputs/recognition_threshold.json']
    before={str(p):digest(p) for p in protected}
    app,engine=load_engine();OUT.mkdir(parents=True,exist_ok=True);rows=[];summaries=[]
    matrix=engine.matcher.matrix
    indices={engine._public_identities[name]['student_code']:[i for i,r in enumerate(engine.matcher.records) if r['student_id']==name] for name in engine.matcher.students}
    for variant,profile,size in VARIANTS:
        started=time.perf_counter();variant_rows=[];image_times=[]
        for im in manifest['images']:
            ground=[f for f in im['faces'] if labels['faces'][f['face_id']]['label'] not in ('IGNORE','UNLABELED')]
            if not ground:continue
            with Image.open(ROOT/im['source']) as src:
                src=ImageOps.exif_transpose(src).convert('RGB');src.thumbnail(size);bgr=np.asarray(src)[:,:,::-1].copy()
            captures=[];start=time.perf_counter()
            results,timings=engine.pipeline.run(bgr,profile,diagnostic=captures.append)
            image_times.append(dict(source=im['source'],ms=(time.perf_counter()-start)*1000,faces=len(results),timings=timings))
            h,w=bgr.shape[:2];matches=[]
            for f in ground:
                b=f['box'];a=[b['x']/im['frame']['width'],b['y']/im['frame']['height'],b['w']/im['frame']['width'],b['h']/im['frame']['height']]
                overlaps=[iou(a,[r['facial_area'][k]/(w if k in ('x','w') else h) for k in ('x','y','w','h')]) for r in results]
                matches.append([(j,v) for j,v in enumerate(overlaps) if v>=.35])
            for face,choices in zip(ground,matches):
                fid=face['face_id'];truth=labels['faces'][fid]['label']
                row=dict(variant=variant,source=im['source'],face_id=fid,number=face['number'],ground_truth=truth,
                    detected=False,prediction=None,similarity=None,margin=None,reasons=[],crop=None)
                j=choices[0][0] if len(choices)==1 and sum(any(k==choices[0][0] for k,_ in group) for group in matches)==1 else None
                if j is None:
                    row['reasons']=['no_overlap_match' if not choices else 'ambiguous_box_match'];row['candidate_box_count']=len(choices)
                else:
                    capture=captures[j];vector=np.asarray(results[j]['embedding'],np.float64);norm=np.linalg.norm(vector)
                    if norm==0 or not np.isfinite(vector).all():raise ValueError('Invalid diagnostic embedding')
                    scores=matrix@(vector/norm);per={code:float(max(scores[ix])) for code,ix in indices.items()};ranked=sorted(per,key=lambda c:(-per[c],c))
                    top=ranked[0];margin=per[top]-per[ranked[1]]
                    crop=capture['crop'];grey=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
                    reasons=[]
                    if per[top]<point['min_similarity']:reasons.append('similarity_below_calibration_proposal')
                    if margin<point['min_margin']:reasons.append('margin_below_calibration_proposal')
                    if truth!='UNKNOWN' and top!=truth:reasons.append('wrong_top1_against_human_label')
                    row.update(detected=True,prediction=top,similarity=per[top],second=ranked[1],margin=margin,
                        per_student_similarity=per,match_iou=choices[0][1],box=results[j]['facial_area'],angle=capture['angle'],
                        left_eye=capture['left_eye'],right_eye=capture['right_eye'],crop_size=[crop.shape[1],crop.shape[0]],
                        laplacian_variance=float(cv2.Laplacian(grey,cv2.CV_64F).var()),brightness=float(grey.mean()),
                        embedding_dimension=int(vector.size),embedding_norm=float(norm),tensor_shape=list(capture['tensor'].shape),
                        tensor_min=float(capture['tensor'].min()),tensor_max=float(capture['tensor'].max()),reasons=reasons,
                        accepted_calibration_only=not any(reason.startswith(('similarity_','margin_')) for reason in reasons))
                    best=int(indices[top][int(np.argmax(scores[indices[top]]))]);row['nearest_registered_source']=engine.matcher.records[best]['image_path']
                    mean3={code:float(np.mean(sorted(scores[ix],reverse=True)[:3])) for code,ix in indices.items()}
                    row['mean_best3_prediction']=max(mean3,key=mean3.get)
                    if reasons or truth=='UNKNOWN':
                        filename=variant+'-'+fid+'.jpg';cv2.imwrite(str(OUT/filename),crop,[cv2.IMWRITE_JPEG_QUALITY,95]);row['crop']=filename
                row.setdefault('accepted_calibration_only',False);variant_rows.append(row)
            print(variant,im['name'],len(results),'faces',flush=True)
        known=[r for r in variant_rows if r['ground_truth']!='UNKNOWN'];unknown=[r for r in variant_rows if r['ground_truth']=='UNKNOWN']
        summaries.append(dict(variant=variant,profile=profile,input_max=size,calibration_only=True,
            known=len(known),known_detected=sum(r['detected'] for r in known),top1_correct=sum(r['prediction']==r['ground_truth'] for r in known),
            accepted_correct=sum(r['accepted_calibration_only'] and r['prediction']==r['ground_truth'] for r in known),
            accepted_wrong=sum(r['accepted_calibration_only'] and r['prediction']!=r['ground_truth'] for r in known),
            rejected_known=sum(not r['accepted_calibration_only'] for r in known),unknown=len(unknown),
            accepted_unknown=sum(r['accepted_calibration_only'] for r in unknown),
            mean_best3_correct=sum(r.get('mean_best3_prediction')==r['ground_truth'] for r in known),
            mean_image_ms=float(np.mean([t['ms'] for t in image_times])),elapsed_seconds=time.perf_counter()-started,image_timings=image_times))
        rows.extend(variant_rows)
    if before!={str(p):digest(p) for p in protected}:raise ValueError('Protected files changed during diagnostic run.')
    report=dict(label_revision=labels['revision'],label_sha256=check['label_sha256'],pipeline_signature=engine.matcher.signature,
        proposed_point_used_for_comparison_only=point,variants=summaries,rows=rows,protected_unchanged=True,
        warning='Calibration experiments, not independent accuracy. Blur/brightness are measurements, not proven causes. No runtime or policy change.')
    write(OUT/'diagnostics.json',report)
    fields=['variant','source','number','face_id','ground_truth','detected','prediction','similarity','margin','accepted_calibration_only','reasons','crop_size','angle','laplacian_variance','nearest_registered_source','crop']
    with (OUT/'cases.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for row in rows:writer.writerow(dict(row,reasons='; '.join(row['reasons'])))
    cards=[]
    for r in rows:
        if r['ground_truth']=='UNKNOWN' or not r['reasons']:continue
        esc=lambda value:html.escape(str(value))
        cards.append('<article><h2>'+esc(r['variant']+' '+r['source']+' #'+str(r['number']))+'</h2>'+('<img width="180" src="'+esc(r['crop'])+'">' if r['crop'] else '')+'<pre>'+esc(json.dumps(r,ensure_ascii=False,indent=2))+'</pre></article>')
    (OUT/'cases.html').write_text('<!doctype html><meta charset="utf-8"><title>Ca lỗi — chỉ calibration</title><style>body{font:15px Segoe UI;color:#142D4E;max-width:1100px;margin:auto}article{border:1px solid #2176AE;padding:16px;margin:12px}pre{white-space:pre-wrap}</style><h1>Ca sai và bị từ chối — nhãn thật của người vận hành</h1><p>Crop, góc xoay và số đo không tự chứng minh nguyên nhân. Không dùng làm holdout.</p>'+''.join(cards),encoding='utf-8')
    print(json.dumps([{k:v for k,v in s.items() if k!='image_timings'} for s in summaries],indent=2))


if __name__=='__main__':run()
