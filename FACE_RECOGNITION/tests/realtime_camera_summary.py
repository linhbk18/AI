"""Summarize local measurements and verify all protected data, without AI calls."""
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from statistics import mean

import requests

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/realtime_camera_ui'


def stats(values):
    ordered=sorted(values);index=.95*(len(values)-1);left=math.floor(index);right=math.ceil(index)
    return dict(n=len(values),mean_ms=mean(values),p95_ms=ordered[left]+(ordered[right]-ordered[left])*(index-left),max_ms=max(values))


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(64*1024),b''):digest.update(block)
    return digest.hexdigest()


def run():
    results={tag:json.loads((OUT/(tag+'.json')).read_text(encoding='utf-8')) for tag in ('before','after','scaled')}
    summary=dict(benchmarks=[],validation=[],memory={tag:dict(before=r['memory_before'],after=r['memory_after']) for tag,r in results.items()},
        anonymous_preview_is_not_identity=True,identity_confirmation=False,attendance=False,unknown_evaluation_available=False)
    flat=[]
    for tag,data in results.items():
        groups=defaultdict(list)
        for row in data['records']:
            groups[(row['profile'],row['case'])].append(row)
            flat.append(dict(tag=tag,**{k:v for k,v in row.items() if k!='timings_ms'},**row['timings_ms']))
        for (profile,case),rows in groups.items():
            item=dict(tag=tag,profile=profile,case=case,**stats([r['http_ms'] for r in rows]),face_counts=[r['faces'] for r in rows])
            item['stages_mean_ms']={key:mean([r['timings_ms'].get(key,0) for r in rows]) for key in ('decode_ms','detection_ms','alignment_ms','embedding_ms','matching_ms','serialization_ms','server_total_ms')}
            summary['benchmarks'].append(item)
        if data['validation']:
            summary['validation'].append(dict(tag=tag,n=len(data['validation']),correct=sum(r['correct'] for r in data['validation']),one_face=sum(r['faces']==1 for r in data['validation']),preview_one_face=sum(r['preview_faces']==1 for r in data['validation'])))
            preview=data['preview_during_recognition']
            summary[tag+'_concurrent_preview']=dict(**stats([r['http_ms'] for r in preview]),identity_http_ms=data['concurrent_identity_http_ms'])
    fields=list(dict.fromkeys(k for row in flat for k in row))
    with (OUT/'benchmark.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(flat)
    originals=json.loads((ROOT/'.venv/realtime_ui_originals.json').read_text(encoding='utf-8'))
    changed=[p for p,h in originals.items() if sha(ROOT/p)!=h]
    assert not changed,changed
    with sqlite3.connect(ROOT/'outputs/management.sqlite3') as db:
        dump='\n'.join(db.iterdump())
        assert dump==(ROOT/'.venv/realtime_db_before.sql').read_text(encoding='utf-8'),'SQLite changed'
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    summary['protected_file_count']=len(originals);summary['protected_file_changes']=changed;summary['sqlite_logically_unchanged']=True
    health=requests.get('http://127.0.0.1:5055/api/health',timeout=10).json()
    assert health['ready'] and health['registered_embeddings']==92 and health['registered_students']==5
    assert not health['attendance_enabled'] and not health['identity_confirmation_enabled']
    summary['final_health']=health
    summary['replay']=json.loads((OUT/'replay.json').read_text(encoding='utf-8'))
    summary['notes']=['Small local samples; P95 is descriptive, not a deployment guarantee.',
        'Before and after are different time windows under changing memory pressure; keep all slow samples.',
        'First measured request is distinguished from process/model startup; before process was already running.',
        'Preview box counts without bounding-box truth do not prove detection recall for arbitrary groups.',
        'Replay is real HTTP AI over dataset video, not staged physical people or a new accuracy benchmark.']
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PERFORMANCE: case mean / P95 milliseconds')
    for r in summary['benchmarks']:print(r['tag'],r['profile'],r['case'],round(r['mean_ms']),round(r['p95_ms']))
    print('VALIDATION',summary['validation'])
    print('DATA PASS:',len(originals),'unchanged files; SQLite exact logical equality, 92 vectors, 5 students.')


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');run()
