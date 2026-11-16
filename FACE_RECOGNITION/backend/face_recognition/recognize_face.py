"""Test all faces in DATASET/Test without asserting an identity or threshold.

Each face is compared independently with the immutable registered gallery.
Outputs contain nearest candidates, NOT verified identities or attendance.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys

import numpy as np

from convert_images import ROOT, DATASET, OUTPUTS, sha256, protect_originals, verify_originals
from create_embeddings import atomic_json, load_manifest, validate_vector
from validate import rank_identities, SETTING_KEYS

TEST = DATASET / 'Test'
VISUALIZATIONS = OUTPUTS / 'test_visualizations'
CACHE = OUTPUTS / 'test_face_cache.json'
UNCONFIRMED = 'cannot_conclude_identity_without_validated_rejection_threshold'


def gallery_preflight():
    config = json.loads((OUTPUTS/'face_config.json').read_text(encoding='utf-8'))
    settings = {k:config[k] for k in SETTING_KEYS}
    if (settings['model_name'] != 'ArcFace' or settings['detector_backend'] != 'retinaface'
            or settings['align'] is not True or settings['normalization'] != 'base'
            or settings['enforce_detection'] is not True or settings['max_faces'] is not None):
        raise ValueError('Expected ArcFace/RetinaFace/alignment/base with no face count limit')
    versions = {name:importlib.metadata.version(name) for name in config['packages']}
    if versions != config['packages']:
        raise ValueError('Installed packages differ from the enrollment environment')
    manifest = Path(config['manifest'])
    if sha256(manifest) != config['manifest_sha256']:
        raise ValueError('Registered manifest changed since enrollment')
    inputs = {r['source']:r for r in load_manifest(manifest)}
    records = json.loads((OUTPUTS/'face_embeddings.json').read_text(encoding='utf-8'))
    if not isinstance(records,list) or len(records)!=92 or len(inputs)!=92:
        raise ValueError('Expected exactly 92 registered embeddings')
    seen = set()
    for record in records:
        validate_vector(record['embedding'])
        source = record['source']
        if source in seen or source not in inputs:
            raise ValueError(f'Duplicate or unregistered gallery source: {source}')
        expected = inputs[source]
        if (record['student_id'] != expected['student']
                or Path(record['image_path']).resolve()!=Path(expected['image_path']).resolve()
                or record['source_sha256']!=expected['source_sha256']
                or record['image_sha256']!=expected['image_sha256']
                or record['embedding_dimension']!=512 or record['face_count']!=1):
            raise ValueError(f'Gallery label/path/checksum mismatch: {source}')
        for key in ('model_name','detector_backend','normalization','align','enforce_detection'):
            if record[key]!=settings[key]:
                raise ValueError(f'Gallery settings mismatch: {source}: {key}')
        seen.add(source)
    if seen != set(inputs) or config['status']!='complete':
        raise ValueError('Registered gallery coverage is incomplete')
    students = sorted({r['student_id'] for r in records})
    if len(students)!=5:
        raise ValueError('Expected five registered identities')
    for name,info in config['weights'].items():
        if sha256(OUTPUTS/'.deepface'/'weights'/name)!=info['sha256']:
            raise ValueError(f'Pretrained weight checksum changed: {name}')
    paths = sorted(p for p in TEST.rglob('*') if p.is_file())
    if len(paths)!=9:
        raise ValueError(f'Expected nine Test images, found {len(paths)}')
    print('Registered gallery verified:',dict(Counter(r['student_id'] for r in records)),flush=True)
    print('Pipeline:',settings,flush=True)
    return config,settings,records,students,paths


def box_coordinates(area,width,height):
    raw = {key:int(area[key]) for key in ('x','y','w','h')}
    if raw['w']<=0 or raw['h']<=0:
        raise ValueError('Invalid detected face dimensions')
    left=max(0,min(width,raw['x']))
    top=max(0,min(height,raw['y']))
    right=max(0,min(width,raw['x']+raw['w']))
    bottom=max(0,min(height,raw['y']+raw['h']))
    if right<=left or bottom<=top:
        raise ValueError('Face bounding box lies outside the image')
    return raw,dict(x=left,y=top,w=right-left,h=bottom-top)


def inference_geometry(original_size,max_dimension):
    scale=min(1.,max_dimension/max(original_size))
    return tuple(max(1,round(d*scale)) for d in original_size)


def project_box(area,original_size,inference_size):
    sx,sy=original_size[0]/inference_size[0],original_size[1]/inference_size[1]
    return dict(x=round(area['x']*sx),y=round(area['y']*sy),
                w=round(area['w']*sx),h=round(area['h']*sy))


def draw_visualization(image,rows,target,image_status,image_error):
    """Full-resolution EXIF-oriented image, numbered boxes and separate legend."""
    from PIL import Image,ImageDraw,ImageFont
    width,height=image.size
    canvas_width=max(width,1100)
    size=max(18,min(48,round(canvas_width/55)))
    font_path=Path('C:/Windows/Fonts/arial.ttf')
    if not font_path.is_file():
        raise FileNotFoundError('Unicode annotation font unavailable: C:/Windows/Fonts/arial.ttf')
    font=ImageFont.truetype(str(font_path),size)
    small=ImageFont.truetype(str(font_path),max(16,round(size*0.85)))
    line=size+10
    header=4*line+16
    footer=max(2,len(rows)+1)*line+16
    canvas=Image.new('RGB',(canvas_width,header+height+footer),(22,27,35))
    canvas.paste(image,(0,header))
    draw=ImageDraw.Draw(canvas)
    draw.text((12,8),f'{target.stem} | {image_status} | faces: {sum(bool(r["face_id"]) for r in rows)}',font=font,fill='white')
    draw.text((12,8+line),'UNG VIEN GAN NHAT / NEAREST CANDIDATE — CHUA XAC NHAN',font=font,fill='#ffc857')
    draw.text((12,8+2*line),'Chưa thể kết luận danh tính: chưa có ngưỡng từ chối người lạ được kiểm chứng.',font=small,fill='white')
    draw.text((12,8+3*line),'Số trên khung tương ứng với chú giải bên dưới. Cosine similarity không phải xác suất.',font=small,fill='#c4cbd4')
    for row in rows:
        if not row['face_id'] or row['box_x']=='':
            continue
        x,y,w,h=[row[k] for k in ('box_x','box_y','box_w','box_h')]
        color='#ffc857' if row['processing_status']=='ok' else '#ff6868'
        draw.rectangle((x,y+header,x+w-1,y+header+h-1),outline=color,width=max(2,round(width/650)))
        text=f"#{row['face_id']}"
        text_box=draw.textbbox((0,0),text,font=font)
        tw,th=text_box[2]-text_box[0]+10,line
        label_y=max(header,y+header-th)
        draw.rectangle((x,label_y,min(canvas_width-1,x+tw),label_y+th),fill=(22,27,35))
        draw.text((x+3,label_y),text,font=font,fill=color)
        row['visualization_offset_y']=header
    y=header+height+8
    if image_error:
        draw.text((12,y),f'IMAGE ERROR: {image_error[:120]}',font=small,fill='#ff6868')
        y+=line
    for row in rows:
        if row['processing_status']=='ok':
            text=(f"#{row['face_id']}  Gần nhất: {row['nearest_candidate']}  |  cosine={row['cosine_similarity']:.4f}"
                  '  |  CHƯA XÁC NHẬN')
        elif row['face_id']:
            text=f"#{row['face_id']}  LỖI XỬ LÝ: {row['error'][:100]}"
        else:
            text=f"Không có kết quả khuôn mặt: {row['processing_status']}"
        draw.text((12,y),text,font=small,fill='white')
        y+=line
    # Only generated visualization paths are written; originals are read-only.
    canvas.save(target,format='JPEG',quality=95,subsampling=0)
    with Image.open(target) as check:
        check.load()
    return header


def run(args):
    for filename in ('test_report.csv','test_summary.json'):
        if (OUTPUTS/filename).exists() and not args.overwrite:
            raise FileExistsError(f'{filename} already exists; use --overwrite to regenerate Test outputs only')
    if VISUALIZATIONS.exists() and any(VISUALIZATIONS.iterdir()) and not args.overwrite:
        raise FileExistsError('Test visualizations already exist; refusing overwrite without --overwrite')
    before=protect_originals()
    # Preserve every pre-existing top-level output, including validation embeddings.
    protected={p:sha256(p) for p in OUTPUTS.iterdir() if p.is_file()
               and p.name not in ('test_report.csv','test_summary.json','test_face_cache.json')}
    config,settings,records,students,paths=gallery_preflight()
    signature=hashlib.sha256(json.dumps(dict(settings=settings,packages=config['packages'],
        weights=config['weights'],preprocessing=config['input_preprocessing']),sort_keys=True).encode()).hexdigest()
    cache=dict(schema_version=1,split='Test',pipeline_signature=signature,images={})
    if CACHE.exists() and not args.refresh_cache:
        old=json.loads(CACHE.read_text(encoding='utf-8'))
        if old.get('pipeline_signature')==signature and old.get('split')=='Test':
            cache=old
    VISUALIZATIONS.mkdir(exist_ok=True)
    os.environ['DEEPFACE_HOME']=str(OUTPUTS)
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL','2')
    from PIL import Image,ImageOps
    details,images=[],[]
    model=None
    startup_error=None
    try:
        for index,path in enumerate(paths,1):
            source=path.relative_to(ROOT).as_posix()
            checksum=sha256(path)
            target=VISUALIZATIONS/(path.stem+'_annotated.jpg')
            image=None
            rows=[]
            image_error=''
            status='error'
            detected=None
            cache_used=False
            offset=''
            inference_size=None
            error_stage='load_image'
            try:
                with Image.open(path) as original:
                    image=ImageOps.exif_transpose(original).convert('RGB')
                    image.load()
                width,height=image.size
                inference_size=inference_geometry(image.size,args.max_inference_dimension)
                cached=cache['images'].get(source)
                if (cached and cached.get('sha256')==checksum and cached.get('size')==[width,height]
                        and cached.get('inference_size',cached['size'])==list(inference_size)):
                    faces=cached['faces']
                    cache_used=True
                else:
                    if startup_error:
                        raise RuntimeError(startup_error)
                    if model is None:
                        try:
                            import tensorflow as tf
                            import cv2
                            tf.config.threading.set_intra_op_parallelism_threads(args.threads)
                            tf.config.threading.set_inter_op_parallelism_threads(1)
                            cv2.setNumThreads(args.threads)
                            from deepface import DeepFace
                            from deepface.modules import modeling
                            print('Loading pretrained ArcFace and RetinaFace; max_faces=None...',flush=True)
                            DeepFace.build_model(model_name=settings['model_name'])
                            modeling.build_model(task='face_detector',model_name=settings['detector_backend'])
                            model=DeepFace
                        except Exception as exc:
                            startup_error=f'{type(exc).__name__}: {exc}'
                            raise
                    # Only an in-memory inference copy is resized. Preserve the full
                    # original for visualization and project boxes back to its coordinates.
                    if inference_size!=image.size:
                        print(f'  Memory-bounded inference: {path.name} {image.size} -> {inference_size}; original unchanged.',flush=True)
                        inference_image=image.resize(inference_size,Image.Resampling.LANCZOS)
                    else:
                        inference_image=image
                    bgr=cv2.cvtColor(np.asarray(inference_image),cv2.COLOR_RGB2BGR)
                    if inference_image is not image:
                        inference_image.close()
                    error_stage='DeepFace.represent'
                    try:
                        raw=model.represent(img_path=bgr,**settings)
                    except ValueError as exc:
                        if 'Face could not be detected' in str(exc):
                            raw=[]
                        else:
                            raise
                    faces=[]
                    # DeepFace.represent returns a distinct embedding for every detected face.
                    # Validate and later classify each result independently; never cap at one face.
                    for face in raw:
                        entry=dict(facial_area={},embedding=None,face_confidence=None,error='')
                        try:
                            input_area,_=box_coordinates(face['facial_area'],*inference_size)
                            area,clipped=box_coordinates(project_box(input_area,image.size,inference_size),width,height)
                            entry['facial_area']=area
                            entry['embedding']=validate_vector(face['embedding'])
                            confidence=float(face['face_confidence'])
                            if not math.isfinite(confidence):
                                raise ValueError('Non-finite detector confidence')
                            entry['face_confidence']=confidence
                        except Exception as exc:
                            entry['error']=f'{type(exc).__name__}: {exc}'
                        faces.append(entry)
                    faces.sort(key=lambda r:(r['facial_area'].get('y',height),r['facial_area'].get('x',width)))
                    cache['images'][source]=dict(sha256=checksum,size=[width,height],
                                                inference_size=list(inference_size),faces=faces)
                    atomic_json(CACHE,cache)
                    del bgr
                error_stage='per_face_matching'
                detected=len(faces)
                for face_index,face in enumerate(faces,1):
                    row=dict(image=path.name,source=source,face_id=face_index,face_count=detected,
                             image_width=width,image_height=height,
                             inference_width=inference_size[0],inference_height=inference_size[1],
                             resized_for_memory=inference_size!=image.size,box_x='',box_y='',box_w='',box_h='',
                             raw_box='',face_detection_confidence='',nearest_candidate='',cosine_similarity='',
                             second_candidate='',second_similarity='',top1_top2_margin='',nearest_registered_source='',
                             identity_status=UNCONFIRMED,ground_truth='',manual_review_required=True,
                             processing_status='error',error='',visualization=str(target.resolve()),
                             visualization_offset_y='',cache_used=cache_used)
                    try:
                        raw_box,box=box_coordinates(face['facial_area'],width,height)
                        row.update(box_x=box['x'],box_y=box['y'],box_w=box['w'],box_h=box['h'],
                                   raw_box=json.dumps(raw_box),face_detection_confidence=face['face_confidence'])
                        if face['error']:
                            raise ValueError(face['error'])
                        vector=validate_vector(face['embedding'])
                        ranked=rank_identities(vector,records,students)
                        best,second=ranked[:2]
                        row.update(processing_status='ok',nearest_candidate=best['student'],
                                   cosine_similarity=best['score'],second_candidate=second['student'],
                                   second_similarity=second['score'],top1_top2_margin=best['score']-second['score'],
                                   nearest_registered_source=best['source'])
                    except Exception as exc:
                        row['error']=f'{type(exc).__name__}: {exc}'
                    rows.append(row)
                status='ok' if all(r['processing_status']=='ok' for r in rows) else 'partial'
                if not faces:
                    status='no_faces'
            except Exception as exc:
                image_error=f'{error_stage}: {type(exc).__name__}: {exc}'
            if not rows:
                rows=[dict(image=path.name,source=source,face_id='',face_count=detected if detected is not None else '',
                           image_width=image.width if image else '',image_height=image.height if image else '',
                           inference_width=inference_size[0] if inference_size else '',
                           inference_height=inference_size[1] if inference_size else '',
                           resized_for_memory=bool(image and inference_size and inference_size!=image.size),
                           box_x='',box_y='',box_w='',box_h='',raw_box='',face_detection_confidence='',
                           nearest_candidate='',cosine_similarity='',second_candidate='',second_similarity='',
                           top1_top2_margin='',nearest_registered_source='',identity_status=UNCONFIRMED,
                           ground_truth='',manual_review_required=True,processing_status=status,error=image_error,
                           visualization=str(target.resolve()) if image else '',visualization_offset_y='',cache_used=cache_used)]
            if image is not None:
                try:
                    offset=draw_visualization(image,rows,target,status,image_error)
                except Exception as exc:
                    status='error'
                    image_error+=f' Visualization error: {type(exc).__name__}: {exc}'
                    for row in rows:
                        row['visualization']=''
                finally:
                    image.close()
            images.append(dict(image=path.name,source=source,detected_faces=detected,status=status,error=image_error,
                               valid_face_embeddings=sum(r['processing_status']=='ok' for r in rows),
                               visualization=str(target.resolve()) if target.exists() and not image_error else None,
                               visualization_offset_y=offset,cache_used=cache_used))
            images[-1].update(inference_size=list(inference_size) if inference_size else None,
                              resized_for_memory=bool(inference_size and rows[0]['resized_for_memory']))
            details.extend(rows)
            print(f'[{index}/{len(paths)}] {path.name}: faces={detected}, status={status}, error={image_error}',flush=True)
            for row in rows:
                if row['processing_status']=='ok':
                    print(f"  #{row['face_id']} nearest={row['nearest_candidate']} cosine={row['cosine_similarity']:.6f} | NOT CONFIRMED",flush=True)
                elif row['face_id']:
                    print(f"  #{row['face_id']} ERROR: {row['error']}",flush=True)
        verify_originals(before)
        for path,checksum in protected.items():
            if sha256(path)!=checksum:
                raise RuntimeError(f'Pre-existing output changed: {path}')
        valid=[r for r in details if r['processing_status']=='ok']
        summary=dict(schema_version=1,settings=settings,registered_embeddings=len(records),registered_students=students,
                     embedding_gallery_sha256=protected[OUTPUTS/'face_embeddings.json'],
                     face_config_sha256=protected[OUTPUTS/'face_config.json'],
                     images_total=len(images),images_successful=sum(r['status'] in ('ok','no_faces') for r in images),
                     images_failed=sum(r['status']=='error' for r in images),
                     images_partial=sum(r['status']=='partial' for r in images),
                     faces_detected=sum(r['detected_faces'] or 0 for r in images),
                     faces_compared=len(valid),faces_failed=sum(bool(r['face_id']) and r['processing_status']!='ok' for r in details),
                     images=images,threshold=None,identity_confirmation_applied=False,accuracy=None,
                     ground_truth_available=False,unknown_person_rejection_evaluated=False,
                     identity_status=UNCONFIRMED,manual_review_required_for_all_faces=True,
                     manual_review_reasons=['No true identity labels for individual Test faces.',
                         'No validated rejection threshold: nearest enrolled student may actually be an unknown person.',
                         'Inspect detection boxes for missed faces and false positives.'],
                     lowest_top1_top2_margins=sorted(valid,key=lambda r:r['top1_top2_margin'])[:5],
                     bounding_box_coordinates='EXIF-corrected original image, before visualization header offset',
                     max_inference_dimension=args.max_inference_dimension,
                     memory_handling='Downsize only the in-memory inference copy above the maximum dimension; project boxes to original resolution. Originals unchanged.',
                     visualization_format='Numbered amber boxes; candidate legend below image; header says identity unconfirmed.',
                     dataset_unchanged=True,preexisting_outputs_unchanged=True,
                     completed_at_utc=datetime.now(timezone.utc).isoformat())
        with (OUTPUTS/'test_report.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(details[0]))
            writer.writeheader()
            writer.writerows(details)
        atomic_json(OUTPUTS/'test_summary.json',summary)
        print('\nIMAGE SUMMARY\nImage           | Faces | Status   | Visualization')
        for row in images:
            print(f"{row['image']:15} | {str(row['detected_faces']):5} | {row['status']:8} | {row['visualization']}")
        print(f"TOTAL: {summary['faces_detected']} faces; {summary['images_successful']}/{len(images)} images successful; "
              f"failed={summary['images_failed']}; partial={summary['images_partial']}")
        print('All identities UNCONFIRMED. No threshold, no accuracy, no attendance decision.',flush=True)
        return 1 if summary['images_failed'] or summary['images_partial'] else 0
    finally:
        verify_originals(before)
        for path,checksum in protected.items():
            if sha256(path)!=checksum:
                raise RuntimeError(f'Pre-existing output changed: {path}')
        print('PASS: all pre-existing outputs, including registered and validation embeddings, unchanged.',flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threads',type=int,choices=range(1,17),default=2)
    parser.add_argument('--max-inference-dimension',type=int,choices=range(640,4097),default=2048)
    parser.add_argument('--overwrite',action='store_true',help='Regenerate Test reports/visualizations only')
    parser.add_argument('--refresh-cache',action='store_true',help='Recompute Test-only face embeddings')
    args=parser.parse_args()
    try:
        sys.exit(run(args))
    except Exception as exc:
        print(f'TEST ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        sys.exit(2)
