"""Labelled calibration/holdout workflow. Never manufactures images or enrols samples."""
import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path
import time

from ..config import ROOT

OUT=ROOT/'outputs'
WORK=OUT/'verification_data'
PROFILE='retinaface_scaled_experimental'


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


def reserve_holdout(path,holdout,trial):
    path=Path(path);registry=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    hashes=sorted(r['sha256'] for r in holdout)
    key=hashlib.sha256(json.dumps(sorted((r['sha256'],r['identity']) for r in holdout)).encode()).hexdigest()
    for old_key,entry in registry.items():
        if old_key==key or set(hashes)&set(entry.get('sample_hashes',[])):
            if entry['trial']!=trial or old_key!=key:
                raise ValueError('Holdout images already exposed: do not change configuration, labels, or reuse a subset. Collect NEW independent data.')
    registry[key]=dict(registry.get(key,{}),trial=trial,sample_hashes=hashes)
    write(path,registry);return key,registry


def manifest_rows(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=['source','identity','split','capture_session']:raise ValueError('Expected source,identity,split,capture_session columns, exactly.')
        rows=list(reader)
    seen=set();sessions={};evaluation_trials=set();unknown={s:set() for s in ('calibration','evaluation')}
    registry_path=OUT/'verification_holdout_registry.json'
    registry=json.loads(registry_path.read_text(encoding='utf-8')) if registry_path.exists() else {}
    exposed_hashes={sha for entry in registry.values() for sha in entry.get('sample_hashes',[])}
    old_hashes={digest(p) for p in (ROOT/'DATASET').rglob('*') if p.is_file()}
    old_hashes.update(digest(p) for p in (OUT/'converted_registered').rglob('*') if p.is_file())
    registered_hashes={digest(p) for p in (ROOT/'DATASET/registered').rglob('*') if p.is_file()}
    registered_hashes.update(digest(p) for p in (OUT/'converted_registered').rglob('*') if p.is_file())
    for r in rows:
        if r['split'] not in unknown or not r['identity'] or not r['capture_session']:raise ValueError('Every row needs an identity, split and capture_session.')
        if r['identity'].startswith('UNKNOWN:') and not re.fullmatch(r'UNKNOWN:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',r['identity']):
            raise ValueError('UNKNOWN requires a stable anonymous person code, e.g. UNKNOWN:U01.')
        p=Path(r['source']);p=p if p.is_absolute() else ROOT/p
        if not p.is_file():raise ValueError(f'Missing real photo: {p}')
        sha=digest(p)
        if r['split']=='calibration' and sha in exposed_hashes:
            raise ValueError('An exposed holdout image cannot be reassigned to calibration in this verification workflow.')
        if sha in registered_hashes:raise ValueError('Registered/enrolment photos cannot calibrate or evaluate thresholds.')
        if sha in seen:raise ValueError('Duplicate image content across manifest rows/splits.')
        if r['split']=='evaluation' and sha in old_hashes:raise ValueError('Holdout must be NEW independent data, not existing registered/validation/Test photos.')
        seen.add(sha);r['path']=str(p);r['sha256']=sha
        session=r['capture_session']
        if session in sessions and sessions[session]!=r['split']:raise ValueError('A capture session cannot occur in both splits.')
        sessions[session]=r['split']
        if r['split']=='evaluation':
            trial=(r['identity'],session)
            if trial in evaluation_trials:raise ValueError('Holdout requires one image per person per independent capture visit, not many adjacent frames.')
            evaluation_trials.add(trial)
        if r['identity'].startswith('UNKNOWN:'):unknown[r['split']].add(r['identity'])
    if unknown['calibration']&unknown['evaluation']:raise ValueError('Use different unknown PERSONS in calibration and evaluation.')
    return rows


def load_engine():
    from ..app import create_app
    app=create_app();engine=app.extensions['face_engine']
    while engine.state not in ('ready','error'):time.sleep(1)
    if engine.state!='ready':raise RuntimeError('Models failed to load; see terminal.')
    with engine._guard:engine._refresh_gallery_locked()
    return app,engine


def group_calibration(path):
    """Reuse HUMAN-labelled Test scores for calibration only, never as official holdout."""
    if not path:return [],None
    report=json.loads(Path(path).read_text(encoding='utf-8'))
    base=Path(path).parent;labels=json.loads((base/'labels.json').read_text(encoding='utf-8'));manifest=json.loads((base/'manifest.json').read_text(encoding='utf-8'))
    if report.get('label_file_sha256')!=digest(base/'labels.json') or report.get('manifest_sha256')!=digest(base/'manifest.json'):raise ValueError('Group labels changed after scoring; evaluate the saved labels again.')
    sources={im['source']:im for im in manifest['images']};rows=[]
    for row in report['rows']:
        label=labels['faces'][row['face_id']]
        if label.get('label_source')!='human' or label['label']!=row['ground_truth']:raise ValueError('Group calibration requires explicit, matching human labels.')
        if row['ground_truth'] in ('IGNORE','UNLABELED'):continue
        unknown=row['ground_truth']=='UNKNOWN'
        if unknown and not label.get('unknown_person_id'):raise ValueError('Assign anonymous person IDs to UNKNOWN labels before mixing with independent unknown holdout people.')
        im=sources[row['source']]
        if digest(ROOT/im['source'])!=im['sha256']:raise ValueError('Group source photo changed.')
        rows.append(dict(source=im['source'],path=str(ROOT/im['source']),sha256=im['sha256'],face_id=row['face_id'],
            identity='UNKNOWN:'+label['unknown_person_id'] if unknown else row['ground_truth'],split='calibration',capture_session='test-group-'+im['image_id'],
            detected=row['detected'],score=row.get('similarity'),margin=row.get('margin'),prediction=row.get('prediction'),precomputed_group=True))
    return rows,report


def upper95(k,n):
    """Exact one-sided binomial upper confidence bound; repeated frames aren't independent trials."""
    if not n or k==n:return 1.
    lo,hi=k/n,1.
    for _ in range(55):
        p=(lo+hi)/2
        terms=[math.lgamma(n+1)-math.lgamma(j+1)-math.lgamma(n-j+1)+j*math.log(p)+(n-j)*math.log1p(-p) for j in range(k+1)]
        m=max(terms);cdf=math.exp(m)*sum(math.exp(t-m) for t in terms)
        if cdf>.05:lo=p
        else:hi=p
    return (lo+hi)/2


def metrics(rows,score,margin):
    known=[r for r in rows if not r['identity'].startswith('UNKNOWN:')];unknown=[r for r in rows if r['identity'].startswith('UNKNOWN:')]
    accepted=lambda r:r.get('detected',True) and r['score']>=score and r['margin']>=margin
    fa=sum(accepted(r) for r in unknown);fr=sum(not accepted(r) for r in known);wrong=sum(accepted(r) and r['prediction']!=r['identity'] for r in known)
    return dict(known_samples=len(known),unknown_samples=len(unknown),
        unknown_false_accept_rate=fa/len(unknown) if unknown else 1.,known_false_rejection_rate=fr/len(known) if known else 1.,
        known_wrong_identity_rate=wrong/len(known) if known else 1.,
        upper95=dict(unknown_false_accept_rate=upper95(fa,len(unknown)),known_false_rejection_rate=upper95(fr,len(known)),known_wrong_identity_rate=upper95(wrong,len(known))))


def publish_report(report,engine):
    if report['status']!='passed' or report['pipeline_signature']!=engine.matcher.signature:raise ValueError('Evaluation not passed or runtime gallery signature changed.')
    registry=json.loads((OUT/'verification_holdout_registry.json').read_text(encoding='utf-8'))
    if not any(entry.get('report_sha256')==digest(OUT/'verification_workflow_report.json') for entry in registry.values()):raise ValueError('Evaluation artifact changed after scoring; cannot publish edited metrics.')
    if any(digest(r['path'])!=r['sha256'] for r in report['samples']):raise ValueError('Evaluation images changed after scoring.')
    approved=dict(report,approved_for_automatic_attendance=True)
    write(OUT/'automatic_attendance_evaluation.json',approved)
    policy=dict(status='official',enabled=True,min_similarity=report['min_similarity'],min_margin=report['min_margin'],pipeline_signature=engine.matcher.signature,
        evidence=dict(labelled_known_validation_samples=report['known_samples'],labelled_unknown_validation_samples=report['unknown_samples'],
            independent_evaluation_completed=True,test_used_for_threshold_optimization=False,
            automatic_evaluation_sha256=digest(OUT/'automatic_attendance_evaluation.json')))
    write(OUT/'recognition_threshold.json',policy)


def evaluate(args):
    rows=manifest_rows(args.manifest);groups,group_report=group_calibration(args.group_calibration);rows.extend(groups)
    group_unknown={r['identity'] for r in groups if r['identity'].startswith('UNKNOWN:')}
    if group_unknown&{r['identity'] for r in rows if r['split']=='evaluation' and r['identity'].startswith('UNKNOWN:')}:raise ValueError('Unknown people from Test calibration must not reappear in the holdout.')
    counts={s:{'known':sum(r['split']==s and not r['identity'].startswith('UNKNOWN:') for r in rows),
        'unknown':sum(r['split']==s and r['identity'].startswith('UNKNOWN:') for r in rows)} for s in ('calibration','evaluation')}
    if any(not n for group in counts.values() for n in group.values()):
        write(OUT/'verification_workflow_report.json',dict(status='needs_real_labelled_data',counts=counts,official_policy_created=False))
        print(json.dumps(counts));raise ValueError('Need real known AND unknown samples in BOTH splits; no official thresholds were written.')
    targets=dict(unknown_false_accept_rate=args.max_far,known_false_rejection_rate=args.max_frr,known_wrong_identity_rate=args.max_wrong)
    if not all(0<v<1 for v in targets.values()):raise ValueError('Acceptance targets must be explicitly chosen rates between 0 and 1.')
    app,engine=load_engine();people={engine._public_identities[label]['student_code'] for label in engine.matcher.students}
    if group_report and (group_report['pipeline_signature']!=engine.matcher.signature or group_report['camera_profile']!=args.profile):raise ValueError('Group scores must match the EXACT live gallery signature and camera profile.')
    for split in counts:
        if {r['identity'] for r in rows if r['split']==split and not r['identity'].startswith('UNKNOWN:')}!=people:raise ValueError('Both splits must cover all registered students using exact SQLite student codes.')
    import cv2
    from PIL import Image,ImageOps
    import numpy as np
    def score_rows(samples):
        for row in samples:
            if row.get('precomputed_group'):continue
            with Image.open(row['path']) as im:im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((640,480));bgr=np.asarray(im)[:,:,::-1].copy()
            result=engine.analyze(bgr,profile=args.profile)
            if len(result['faces'])!=1 or result['face_errors']:raise ValueError(f'Exactly one processable face required: {row["source"]}')
            f=result['faces'][0]
            if f['top1']['candidate']==f['top2']['candidate']:raise ValueError('Top two must be different identities.')
            row.update(score=f['top1']['similarity'],margin=f['margin'],prediction=f['top1']['student']['student_code'])
            print(row['split'],row['identity'],row['prediction'],flush=True)
    calib=[r for r in rows if r['split']=='calibration'];holdout=[r for r in rows if r['split']=='evaluation']
    score_rows(calib)
    best=None
    for score in sorted({r['score'] for r in calib if r.get('detected',True)}|{1.}):
        for margin in sorted({r['margin'] for r in calib if r.get('detected',True)}|{2.}):
            m=metrics(calib,score,margin)
            if any(m[k]>v for k,v in targets.items()):continue
            rank=(m['known_false_rejection_rate'],m['unknown_false_accept_rate'],m['known_wrong_identity_rate'],-score,-margin)
            if best is None or rank<best[0]:best=(rank,score,margin)
    if best is None:raise ValueError('No score/margin pair meets calibration targets. No official policy written.')
    _,score,margin=best
    registry_path=OUT/'verification_holdout_registry.json'
    trial=dict(pipeline_signature=engine.matcher.signature,profile=args.profile,min_similarity=score,min_margin=margin,targets=targets)
    # Reserve and freeze the trial BEFORE the first holdout inference/output.
    # Even a partially failed run must not expose holdout scores then allow retuning.
    holdout_id,registry=reserve_holdout(registry_path,holdout,trial)
    score_rows(holdout)
    m=metrics(holdout,score,margin)
    passed=all(m['upper95'][k]<=v for k,v in targets.items())
    report=dict(status='passed' if passed else 'holdout_failed_or_insufficient_sample_size',pipeline_signature=engine.matcher.signature,
        camera_profile=args.profile,min_similarity=score,min_margin=margin,independent_holdout=True,
        approved_for_automatic_attendance=bool(passed and args.publish),acceptance_targets=targets,**m,
        manifest_sha256=digest(args.manifest),samples=rows,configuration=engine._gallery_config,
        note='Do not retune on this holdout. Confidence bounds assume independent capture trials, not adjacent video frames.')
    if group_report:report['external_label_artifacts']=[dict(path=str(Path(args.group_calibration).parent/'labels.json'),sha256=group_report['label_file_sha256']),dict(path=str(Path(args.group_calibration).parent/'manifest.json'),sha256=group_report['manifest_sha256'])]
    write(OUT/'verification_workflow_report.json',report)
    registry[holdout_id]['report_sha256']=digest(OUT/'verification_workflow_report.json');write(registry_path,registry)
    if args.publish:
        if not passed:raise ValueError('Holdout confidence bounds did not meet targets; cannot publish.')
        publish_report(report,engine)
    print(json.dumps({k:v for k,v in report.items() if k not in ('samples','configuration')},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('seed-known-calibration');sub.add_parser('publish');audit=sub.add_parser('audit');audit.add_argument('--manifest',default=str(WORK/'manifest.csv'))
    ev=sub.add_parser('evaluate');ev.add_argument('--manifest',default=str(WORK/'manifest.csv'));ev.add_argument('--profile',default=PROFILE)
    ev.add_argument('--group-calibration',help='outputs/test_labeling/evaluation.json after human labelling and scoring')
    for key in ('far','frr','wrong'):ev.add_argument('--max-'+key,type=float,required=True)
    ev.add_argument('--publish',action='store_true')
    act=sub.add_parser('activate');act.add_argument('--profile',default=PROFILE);sub.add_parser('deactivate')
    add=sub.add_parser('add-sample');add.add_argument('--source',required=True);add.add_argument('--identity',required=True)
    add.add_argument('--split',choices=['calibration','evaluation'],required=True);add.add_argument('--capture-session',required=True)
    args=parser.parse_args()
    if args.command=='init':
        for kind in ('known','unknown'):
            for split in ('calibration','evaluation'):(WORK/kind/split).mkdir(parents=True,exist_ok=True)
        manifest=WORK/'manifest.csv'
        if not manifest.exists():manifest.write_text('source,identity,split,capture_session\n',encoding='utf-8')
        print('Created empty directories and CSV header only. Add real, consented, labelled images; no fake samples generated.')
    elif args.command=='seed-known-calibration':
        import sqlite3
        from contextlib import closing
        manifest=WORK/'manifest.csv'
        with manifest.open(encoding='utf-8-sig',newline='') as f:
            if list(csv.DictReader(f)):raise ValueError('Manifest already contains rows; refusing to overwrite user labels.')
        with closing(sqlite3.connect(OUT/'management.sqlite3')) as db:
            people={name:code for name,code in db.execute('SELECT full_name,student_code FROM students WHERE deleted_at IS NULL')}
        samples=[]
        for p in sorted((ROOT/'DATASET/validation').rglob('*')):
            if not p.is_file():continue
            if p.parent.name not in people:raise ValueError('Dataset label not mapped exactly to SQLite: '+p.parent.name)
            samples.append(dict(source=p.relative_to(ROOT).as_posix(),identity=people[p.parent.name],split='calibration',capture_session='legacy-validation'))
        with manifest.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['source','identity','split','capture_session']);writer.writeheader();writer.writerows(samples)
        print(f'Added {len(samples)} real validation references as CALIBRATION ONLY. No photos copied or modified.')
    elif args.command=='add-sample':
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(OUT/'management.sqlite3')) as db:codes={r[0] for r in db.execute('SELECT student_code FROM students WHERE deleted_at IS NULL')}
        if args.identity not in codes and not args.identity.startswith('UNKNOWN:'):raise ValueError('Identity must be an exact student code or UNKNOWN:person-code.')
        manifest=WORK/'manifest.csv'
        with manifest.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        rows.append(dict(source=args.source,identity=args.identity,split=args.split,capture_session=args.capture_session))
        temp=manifest.with_suffix('.staging.csv')
        try:
            with temp.open('w',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=['source','identity','split','capture_session']);writer.writeheader();writer.writerows(rows)
            manifest_rows(temp);temp.replace(manifest)
        finally:temp.unlink(missing_ok=True)
        print('Added one real labelled reference. Dataset and registered embeddings unchanged.')
    elif args.command=='audit':
        rows=manifest_rows(args.manifest);print(json.dumps({s:{'known':sum(r['split']==s and not r['identity'].startswith('UNKNOWN:') for r in rows),'unknown':sum(r['split']==s and r['identity'].startswith('UNKNOWN:') for r in rows)} for s in ('calibration','evaluation')},indent=2))
    elif args.command=='evaluate':evaluate(args)
    elif args.command=='publish':
        report=json.loads((OUT/'verification_workflow_report.json').read_text(encoding='utf-8'))
        if report.get('status')!='passed':raise ValueError('Cannot publish: evaluation has not passed.')
        _,engine=load_engine();publish_report(report,engine);print('Published reviewed evaluation and official thresholds without rescoring the holdout.')
    elif args.command=='deactivate':write(OUT/'automation_activation.json',dict(enabled=False));print('Automatic attendance disabled.')
    else:
        from ..attendance.automatic import AutomaticAttendance
        existing=json.loads((OUT/'recognition_threshold.json').read_text(encoding='utf-8'))
        if existing.get('status')!='official' or existing.get('enabled') is not True:raise ValueError('Cannot activate: no evaluated official policy. Add real known/unknown holdout data first.')
        _,engine=load_engine();policy=engine.matcher.policy
        check=AutomaticAttendance(None,True,OUT/'automatic_attendance_evaluation.json')
        status=check.status(policy,engine.matcher.signature,args.profile)
        if not status['ready']:raise ValueError(status['message'])
        report=json.loads((OUT/'automatic_attendance_evaluation.json').read_text(encoding='utf-8'))
        if any(digest((ROOT/r['source']) if not Path(r['source']).is_absolute() else Path(r['source']))!=r['sha256'] for r in report['samples']):raise ValueError('Evaluation photos changed since approval.')
        write(OUT/'automation_activation.json',dict(enabled=True,profile=args.profile,policy_sha256=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()))
        print('Activation saved. Restart Flask to load the published policy. No source-code edits required.')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError) as exc:raise SystemExit(str(exc))
