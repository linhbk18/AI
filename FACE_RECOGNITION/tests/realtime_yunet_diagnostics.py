"""Investigate the two previous YuNet identity failures without adopting it."""
import json
from pathlib import Path
import re
import sys

import requests

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tests'))
from benchmark_camera_optimization import frame
URL='http://127.0.0.1:5055'


def run():
    html=requests.get(URL+'/').text;cfg=json.loads(re.search(r'<script id="app-config" type="application/json">(.*?)</script>',html).group(1))
    headers={'X-Camera-Token':cfg['csrfToken']}
    labels=json.loads((ROOT/'backend/database/legacy_student_roster.json').read_text(encoding='utf-8'))
    cases=[ROOT/'DATASET/validation'/labels[0]['dataset_label']/(labels[0]['dataset_label']+'_02.jpg'),ROOT/'DATASET/validation/Nhi/Nhi_15.jpg']
    rows=[]
    for path in cases:
        row=dict(source=path.relative_to(ROOT).as_posix(),true_dataset_label=path.parent.name)
        for profile in ('retinaface_native_experimental','yunet_recall_experimental'):
            r=requests.post(URL+'/api/camera/sessions',headers=headers,json={'profile':profile});r.raise_for_status();sid=r.json()['session_id']
            try:
                response=requests.post(URL+'/api/recognize',headers=dict(headers,**{'X-Camera-Session':sid,'Content-Type':'image/jpeg'}),data=frame(path,(640,480)),timeout=150)
                response.raise_for_status();row[profile]=response.json()['faces']
            finally:requests.delete(URL+'/api/camera/sessions/'+sid,headers=headers)
        a,b=(row[p][0]['box'] for p in ('retinaface_native_experimental','yunet_recall_experimental'))
        intersection=max(0,min(a['x']+a['w'],b['x']+b['w'])-max(a['x'],b['x']))*max(0,min(a['y']+a['h'],b['y']+b['h'])-max(a['y'],b['y']))
        row['box_iou']=intersection/(a['w']*a['h']+b['w']*b['h']-intersection)
        rows.append(row)
    output=dict(cases=rows,interpretation='Different detector boxes/landmarks yield different aligned crops. These observations do not isolate alignment versus crop versus detector effects. YuNet is NOT the identity default; no similarity threshold changed.')
    (ROOT/'outputs/realtime_camera_ui/yunet_diagnostics.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:print(row['source'],row['box_iou'],{p:row[p][0]['top1']['candidate'] for p in ('retinaface_native_experimental','yunet_recall_experimental')})


if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');run()
