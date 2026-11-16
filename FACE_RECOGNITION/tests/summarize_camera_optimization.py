"""Summarize measured results without inventing ground truth or rejection cutoffs."""
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/camera_optimization'


def stats(values):
    return dict(n=len(values),mean_ms=float(np.mean(values)),p95_ms=float(np.percentile(values,95)),
                min_ms=float(min(values)),max_ms=float(max(values)),
                serialized_recognition_fps=1000/(float(np.mean(values))+150))


def run():
    benchmarks=[]
    for file in sorted(OUT.glob('benchmark_*.json')):
        if file.stem.endswith('yunet_probe'): continue
        for row in json.loads(file.read_text(encoding='utf-8')):
            benchmarks.append(dict(tag=file.stem.removeprefix('benchmark_'),**row))
    validations=[]
    for file in sorted(OUT.glob('validation_*.json')):
        for row in json.loads(file.read_text(encoding='utf-8')):
            validations.append(dict(tag=file.stem.removeprefix('validation_'),**row))
    grouped=defaultdict(list)
    for row in benchmarks: grouped[(row['profile'],row['width'],row['height'])].append(row)
    performance=[]
    for (profile,width,height),rows in sorted(grouped.items()):
        aggregate=dict(profile=profile,width=width,height=height,**stats([r['total_ms'] for r in rows]),
                       face_counts=[dict(case=r['case'],faces=r['faces']) for r in rows])
        for stage in ('decode_ms','detection_ms','alignment_ms','embedding_ms','matching_ms'):
            aggregate[stage+'_mean']=float(np.mean([r[stage] for r in rows]))
        performance.append(aggregate)
    quality=[]
    group=defaultdict(list)
    for row in validations: group[(row['tag'],row['profile'],row['width'],row['height'])].append(row)
    for (tag,profile,width,height),rows in sorted(group.items()):
        n=len(rows)
        quality.append(dict(tag=tag,profile=profile,width=width,height=height,input_images=n,
            correct=sum(r['correct'] for r in rows),accuracy=sum(r['correct'] for r in rows)/n,
            exactly_one_face=sum(r['faces']==1 for r in rows),
            one_face_detection_rate=sum(r['faces']==1 for r in rows)/n,
            no_faces=sum(r['faces']==0 for r in rows),multiple_faces=sum(r['faces']>1 for r in rows),
            failures=[r for r in rows if not r['correct']]))
    eq=[r for r in benchmarks if r['profile']=='retinaface_reuse' and r.get('equivalence_min_cosine') is not None]
    summary=dict(schema_version=1,performance=performance,validation=quality,
        startup=json.loads((OUT/'startup.json').read_text(encoding='utf-8')),
        machine=json.loads((OUT/'machine.json').read_text(encoding='utf-8')),
        retinaface_equivalence=dict(face_containing_frame_comparisons=len(eq),
            minimum_embedding_cosine=min(r['equivalence_min_cosine'] for r in eq),
            maximum_component_difference=max(r['equivalence_max_component_difference'] for r in eq)),
        profile_default='retinaface_reuse',experimental_detectors_do_not_inherit_enrollment_thresholds=True,
        identity_confirmation=False,attendance=False,port=5055,
        metric_notes=['FPS is measured mean inference plus the configured 150 ms scheduling gap; it is not video FPS.',
            'P95 is an estimate on a small local mixed-case sample; inspect n and per-case timings.',
            'alignment_ms includes padding/crop/normalization allocation. embedding_ms includes preprocessing and ArcFace.forward.',
            'Camera-sized validation is evaluated anew; prior full-resolution 25/25 is not inherited.',
            'Group-image detector counts are outputs/reference comparisons, not true person recall without manual annotation.'])
    baseline=json.loads((ROOT/'.venv/optimization_originals.json').read_text(encoding='utf-8'))
    for path,digest in baseline.items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest,path
    summary['all_original_data_unchanged']=True
    summary['verified_original_file_count']=len(baseline)
    summary['startup_runs']={p.stem:json.loads(p.read_text(encoding='utf-8')) for p in OUT.glob('startup*.json')}
    http=json.loads((OUT/'http_qa.json').read_text(encoding='utf-8'))
    summary['actual_http_qa']=http
    summary['warm_http_single_face']={profile:stats([r['http_roundtrip_ms'] for r in http['warm_records'] if r['profile']==profile]) for profile in {r['profile'] for r in http['warm_records']}}
    summary['metric_notes'].append('Offline totals and native HTTP-inclusive totals are marked in raw rows; do not attribute all differences to code under variable memory pressure. Original UI gap was 750 ms; derived FPS above standardizes all profiles to the new 150 ms gap.')
    for name,rows in (('performance_benchmark.csv',benchmarks),('validation_comparison.csv',validations)):
        fields=list(dict.fromkeys(k for row in rows for k in row))
        with (OUT/name).open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    (OUT/'performance_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PERFORMANCE (milliseconds; mixed one/two/zero/many-face cases)')
    for p in performance:
        print(p['profile'],f"{p['width']}x{p['height']}",f"n={p['n']} mean={p['mean_ms']:.1f} P95={p['p95_ms']:.1f} FPS={p['serialized_recognition_fps']:.2f}")
    print('VALIDATION')
    for p in quality:
        print(p['tag'],p['profile'],f"{p['width']}x{p['height']}",f"correct={p['correct']}/{p['input_images']}; one-face={p['exactly_one_face']}/{p['input_images']}")
    print('RetinaFace equivalence:',summary['retinaface_equivalence'])
    print('PASS: original DATASET and all existing outputs unchanged.')


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');run()
