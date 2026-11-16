"""Audit human group labels and analyse empirical operating points; never activates attendance."""
import argparse
from collections import Counter
from contextlib import closing
import csv
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import numpy as np
from ..config import ROOT
from ..attendance.continuity import iou
from .verification_workflow import digest,write,upper95

BASE=ROOT/'outputs/test_labeling'
OUT=BASE/'verification_analysis'


def audit():
    manifest=json.loads((BASE/'manifest.json').read_text(encoding='utf-8'));labels=json.loads((BASE/'labels.json').read_text(encoding='utf-8'))
    with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as db:
        people={code:dict(student_id=sid,student_name=name) for sid,code,name in db.execute('SELECT id,student_code,full_name FROM students WHERE deleted_at IS NULL')}
    valid=set(people)|{'UNKNOWN','IGNORE','UNLABELED'};seen=set();errors=[];overlaps=[];source_hashes={};image_rows=[]
    for image in manifest['images']:
        source=ROOT/image['source'];actual=digest(source)
        if actual!=image['sha256']:errors.append(dict(source=image['source'],reason='source_hash_mismatch'))
        if actual in source_hashes:errors.append(dict(source=image['source'],reason='duplicate_source_content',other=source_hashes[actual]))
        source_hashes[actual]=image['source'];counts=Counter()
        for face in image['faces']:
            fid=face['face_id']
            if fid in seen:errors.append(dict(face_id=fid,reason='duplicate_face_id'))
            seen.add(fid);row=labels['faces'].get(fid)
            if not isinstance(row,dict) or row.get('label') not in valid:
                errors.append(dict(face_id=fid,reason='missing_or_invalid_label'));continue
            if row['label'] not in ('UNLABELED',) and row.get('label_source')!='human':errors.append(dict(face_id=fid,reason='label_not_explicitly_human'))
            counts[row['label']]+=1
            b=face['box']
            if any(not isinstance(b.get(k),(int,float)) or not math.isfinite(b[k]) for k in ('x','y','w','h')):errors.append(dict(face_id=fid,reason='invalid_geometry'))
        for n,a in enumerate(image['faces']):
            for b in image['faces'][n+1:]:
                overlap=iou([a['box'][k] for k in ('x','y','w','h')],[b['box'][k] for k in ('x','y','w','h')])
                if overlap>=.65:overlaps.append(dict(source=image['source'],first=a['face_id'],second=b['face_id'],iou=overlap))
        image_rows.append(dict(source=image['source'],labels=dict(counts)))
    orphans=set(labels['faces'])-seen
    if orphans:errors.append(dict(reason='orphan_label_ids',ids=sorted(orphans)))
    counts=Counter(r.get('label') for r in labels['faces'].values());unknown=[r for r in labels['faces'].values() if r.get('label')=='UNKNOWN']
    known={code:n for code,n in counts.items() if code in people}
    csv_rows=list(csv.DictReader((BASE/'labels.csv').open(encoding='utf-8-sig')))
    if len(csv_rows)!=len(seen) or any(r['face_id'] not in labels['faces'] or r['label']!=labels['faces'][r['face_id']]['label'] for r in csv_rows):errors.append(dict(reason='json_csv_label_mismatch'))
    gallery=json.loads((ROOT/'outputs/face_embeddings.json').read_text(encoding='utf-8'))
    vectors=np.asarray([r['embedding'] for r in gallery],dtype=np.float64)
    gallery_valid=vectors.shape==(92,512) and np.isfinite(vectors).all() and bool(np.all(np.linalg.norm(vectors,axis=1)>0)) and len({r['student_id'] for r in gallery})==5
    if not gallery_valid:errors.append(dict(reason='invalid_registered_gallery'))
    provenance=dict(manifest['provenance']);provenance['historical_note']=provenance['reason'];provenance['reason']='Human labels now exist; prior prediction exposure and pipeline benchmarking still prevent claiming an independent holdout.'
    result=dict(label_revision=labels['revision'],label_sha256=digest(BASE/'labels.json'),manifest_sha256=digest(BASE/'manifest.json'),
        counts=dict(VALID=sum(known.values()),UNKNOWN=counts['UNKNOWN'],IGNORE=counts['IGNORE'],UNLABELED=counts['UNLABELED'],total=len(seen)),
        known_counts=known,known_distinct_identities=len(known),unknown_distinct_identities=None if any(not r.get('unknown_person_id') for r in unknown) else len({r['unknown_person_id'] for r in unknown}),
        unknown_faces_without_person_ids=sum(not r.get('unknown_person_id') for r in unknown),errors=errors,geometric_duplicate_candidates=overlaps,
        images=image_rows,gallery_valid=bool(gallery_valid),registered_embeddings=len(gallery),registered_identity_count=5,
        provenance=provenance,official_holdout_faces=0)
    write(OUT/'label_audit.json',result);return result


def decision_counts(rows,score,margin):
    counts=Counter();accepted=lambda r:r['detected'] and r['similarity']>=score and r['margin']>=margin
    for row in rows:
        yes=accepted(row)
        if row['ground_truth']=='UNKNOWN':counts['unknown_false_accepts' if yes else 'unknown_correct_rejects']+=1
        elif not yes:counts['known_false_rejects']+=1
        elif row['prediction']==row['ground_truth']:counts['known_correct_accepts']+=1
        else:counts['known_wrong_accepts']+=1
    return {k:counts[k] for k in ('known_correct_accepts','known_wrong_accepts','known_false_rejects','unknown_false_accepts','unknown_correct_rejects')}


def distribution(values):
    return dict(count=len(values),min=min(values) if values else None,median=float(np.median(values)) if values else None,
        p05=float(np.percentile(values,5)) if values else None,p95=float(np.percentile(values,95)) if values else None,max=max(values) if values else None)


def analyse():
    a=audit()
    if a['errors']:raise ValueError('Resolve label audit errors before evaluation analysis.')
    e=json.loads((BASE/'evaluation.json').read_text(encoding='utf-8'))
    if e.get('label_revision')!=a['label_revision'] or e.get('label_file_sha256')!=a['label_sha256'] or e.get('manifest_sha256')!=a['manifest_sha256']:raise ValueError('Scores are stale for current human labels; run force evaluation first.')
    rows=e['rows'];known=[r for r in rows if r['ground_truth']!='UNKNOWN'];unknown=[r for r in rows if r['ground_truth']=='UNKNOWN'];matched=[r for r in rows if r['detected']]
    candidates=[]
    score_values=sorted({r['similarity'] for r in matched});margin_values=sorted({r['margin'] for r in matched})
    # Midpoints represent observed decision regions without fragile 1-ULP cutoffs.
    scores=[-1.]+[(a+b)/2 for a,b in zip(score_values,score_values[1:])]+[1.]
    margins=[0.]+[(a+b)/2 for a,b in zip(margin_values,margin_values[1:])]+[2.]
    for score in scores:
        for margin in margins:candidates.append(dict(min_similarity=score,min_margin=margin,**decision_counts(rows,score,margin)))
    safe=[r for r in candidates if r['unknown_false_accepts']==0 and r['known_wrong_accepts']==0]
    safe.sort(key=lambda r:(-r['known_correct_accepts'],-r['min_similarity'],-r['min_margin']))
    selected=safe[0] if safe and safe[0]['known_correct_accepts'] else None
    tradeoffs={}
    for r in candidates:
        if r['known_wrong_accepts']:continue
        n=r['unknown_false_accepts'];rank=(-r['known_correct_accepts'],-r['min_similarity'],-r['min_margin'])
        if n not in tradeoffs or rank<tradeoffs[n][0]:tradeoffs[n]=(rank,r)
    genuine=[r['per_student_similarity'][r['ground_truth']] for r in known if r['detected'] and r['ground_truth'] in r['per_student_similarity']]
    impostor=[v for r in known if r['detected'] for code,v in r['per_student_similarity'].items() if code!=r['ground_truth']]
    unknown_scores=[r['similarity'] for r in unknown if r['detected']]
    report=dict(status='calibration_only_not_official_verification',label_revision=a['label_revision'],pipeline_signature=e['pipeline_signature'],camera_profile=e['camera_profile'],audit=a,
        raw_known_correct=sum(r.get('prediction')==r['ground_truth'] for r in known),raw_known_wrong=sum(r['detected'] and r['prediction']!=r['ground_truth'] for r in known),
        unmatched_known=sum(not r['detected'] for r in known),unmatched_unknown=sum(not r['detected'] for r in unknown),
        distributions=dict(genuine=distribution(genuine),different_identity=distribution(impostor),unknown_top1=distribution(unknown_scores),known_margin=distribution([r['margin'] for r in known if r['detected']]),unknown_margin=distribution([r['margin'] for r in unknown if r['detected']])),
        proposed_calibration_point=selected,selection_rule='Maximise correct known acceptances subject to ZERO observed unknown/wrong-known acceptances; ties use stricter observed boundaries. Not an official target or holdout result.',
        threshold_candidates='midpoints between distinct observed scores/margins plus valid range endpoints',tradeoffs=[tradeoffs[k][1] for k in sorted(tradeoffs)],official_policy_created=False,automatic_enabled=False,
        missing=dict(unlabelled_faces=a['counts']['UNLABELED'],unknown_person_ids=a['unknown_faces_without_person_ids'],independent_known_holdout=0,independent_unknown_holdout=0))
    if selected:
        report['far']=selected['unknown_false_accepts']/len(unknown) if unknown else None
        report['frr']=selected['known_false_rejects']/len(known) if known else None
        report['binomial_far_upper95_IF_independent']=upper95(selected['unknown_false_accepts'],len(unknown))
        report['independence_warning']='Unknown identities are not grouped and photos were already exposed; the binomial bound is illustrative, not certified.'
    write(OUT/'proposed_threshold.json',dict(enabled=False,official=False,label_revision=a['label_revision'],
        label_sha256=a['label_sha256'],pipeline_signature=e['pipeline_signature'],camera_profile=e['camera_profile'],
        proposed_calibration_point=selected,reason='Calibration-only diagnostic. Independent holdout and acceptable error rates required; never loaded as official runtime policy.'))
    write(OUT/'analysis.json',report)
    with (OUT/'operating_points.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(candidates[0]));writer.writeheader();writer.writerows(candidates)
    with (OUT/'per_face_decisions.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['source','face_id','ground_truth','detected','prediction','similarity','margin','decision'];writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for row in rows:
            accepted=bool(selected and row['detected'] and row['similarity']>=selected['min_similarity'] and row['margin']>=selected['min_margin'])
            writer.writerow(dict(row,decision='accepted_calibration_only' if accepted else 'rejected_or_unmatched'))
    try:
        import matplotlib;matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(9,5));bins=np.linspace(-1,1,41)
        for values,label in ((genuine,'Known genuine identity'),(impostor,'Known other identities'),(unknown_scores,'Unknown nearest identity')):
            if values:ax.hist(values,bins=bins,alpha=.5,label=label)
        ax.set(xlabel='Cosine similarity',ylabel='Face/identity comparisons',title='Human labels: calibration data only');ax.legend();fig.tight_layout();fig.savefig(OUT/'similarity_distribution.png',dpi=160);plt.close(fig)
    except ImportError:report['plot']='matplotlib unavailable; actual quantiles/counts recorded in JSON; no plotting package installed'
    write(OUT/'analysis.json',report)
    print(json.dumps({k:report[k] for k in ('raw_known_correct','raw_known_wrong','unmatched_known','unmatched_unknown','proposed_calibration_point','far','frr') if k in report},indent=2));return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['audit','analyse']);args=p.parse_args()
    if args.command=='audit':print(json.dumps(audit(),ensure_ascii=True,indent=2))
    else:analyse()


if __name__=='__main__':main()
