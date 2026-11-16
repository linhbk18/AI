"""Reproducible before/after stage and validation measurements; no camera storage."""
import argparse
import csv
from io import BytesIO
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image,ImageOps

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.face_recognition.runtime_service import RecognitionEngine,file_hash

OUT=ROOT/'outputs'/'camera_optimization'


def frame(path=None,size=(640,480)):
    if path is None:
        image=Image.new('RGB',size,'#101827')
    else:
        with Image.open(path) as src: image=ImageOps.exif_transpose(src).convert('RGB')
        # Letterbox, never stretch. The requested canvas is part of the experiment.
        image.thumbnail(size,Image.Resampling.LANCZOS)
        canvas=Image.new('RGB',size,(0,0,0))
        canvas.paste(image,((size[0]-image.width)//2,(size[1]-image.height)//2))
        image.close();image=canvas
    buffer=BytesIO();image.save(buffer,format='JPEG',quality=85);image.close()
    return buffer.getvalue()


def decode(raw):
    start=time.perf_counter()
    with Image.open(BytesIO(raw)) as image:
        bgr=np.ascontiguousarray(np.asarray(ImageOps.exif_transpose(image).convert('RGB'))[:,:,::-1])
    return bgr,(time.perf_counter()-start)*1000


def main(args):
    OUT.mkdir(exist_ok=True)
    engine=RecognitionEngine(threads=args.threads)
    engine._load()
    if engine.state!='ready': raise RuntimeError('Engine initialization failed')
    startup=dict(startup_ms=engine.startup_ms,model_load_count=engine.model_load_count,threads=args.threads,
                 arcface_warmup_ms=engine.arcface_warmup_ms)
    (OUT/'startup.json').write_text(json.dumps(startup,indent=2),encoding='utf-8')
    (OUT/f'startup_{args.tag}.json').write_text(json.dumps(startup,indent=2),encoding='utf-8')
    gallery=engine.matcher.records
    students=engine.matcher.students
    first=Path(next(r for r in gallery if r['student_id']==students[0])['image_path'])
    paths=dict(one_face=first,two_faces=ROOT/'DATASET/Test/Group10.jpg',
               no_face=None,many_faces=ROOT/'DATASET/Test/Group01.jpg')
    profiles=args.profiles.split(',')
    sizes=[tuple(map(int,x.split('x'))) for x in args.sizes.split(',')]
    results=[]
    if not args.validation_only:
        for size in sizes:
            for case,path in paths.items():
                raw=frame(path,size)
                bgr,decode_ms=decode(raw)
                baseline=None
                for profile in profiles:
                    for repeat in range(args.repeats):
                        start=time.perf_counter()
                        faces,timings=engine.pipeline.run(bgr,profile)
                        stage_end=time.perf_counter()
                        matching=time.perf_counter()
                        candidates=[engine.matcher.match(f['embedding'])['best_candidate'] for f in faces]
                        match_ms=(time.perf_counter()-matching)*1000
                        item=dict(profile=profile,case=case,width=size[0],height=size[1],repeat=repeat,
                            decode_ms=decode_ms,**timings,matching_ms=match_ms,
                            total_ms=decode_ms+(stage_end-start)*1000+match_ms,faces=len(faces),
                            candidates=candidates,warmup_sample=repeat==0)
                        if profile=='retinaface_original' and repeat==0: baseline=faces
                        if profile=='retinaface_reuse' and baseline is not None:
                            assert len(faces)==len(baseline)
                            similarities=[];diff=[]
                            for left,right in zip(baseline,faces):
                                a=np.asarray(left['embedding']);b=np.asarray(right['embedding'])
                                similarities.append(float(a@b/np.linalg.norm(a)/np.linalg.norm(b)))
                                diff.append(float(np.max(np.abs(a-b))))
                                assert all(left['facial_area'][k]==right['facial_area'][k] for k in ('x','y','w','h'))
                                assert np.allclose(a,b,atol=1e-5,rtol=1e-5)
                            item.update(equivalence_min_cosine=min(similarities) if similarities else None,
                                        equivalence_max_component_difference=max(diff) if diff else None)
                        results.append(item)
                        (OUT/f'benchmark_{args.tag}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
                        print(f"{profile:22} {size} {case:10} r{repeat} faces={len(faces):2} total={item['total_ms']:.1f} detect={timings['detection_ms']:.1f} align={timings['alignment_ms']:.1f} embed={timings['embedding_ms']:.1f} match={match_ms:.2f}",flush=True)
    if args.validation:
        validation=[]
        for size in sizes:
            for path in sorted((ROOT/'DATASET/validation').rglob('*')):
                if not path.is_file():continue
                bgr,_=decode(frame(path,size))
                for profile in profiles:
                    start=time.perf_counter()
                    faces,timings=engine.pipeline.run(bgr,profile)
                    expected=path.parent.name
                    predicted=engine.matcher.match(faces[0]['embedding'])['best_candidate'] if len(faces)==1 else None
                    validation.append(dict(profile=profile,width=size[0],height=size[1],source=path.relative_to(ROOT).as_posix(),
                        true_student=expected,predicted_student=predicted,faces=len(faces),correct=predicted==expected,
                        elapsed_ms=(time.perf_counter()-start)*1000,**timings))
                    (OUT/f'validation_{args.tag}.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(f"VALIDATION {profile:22} {size} {path.name} faces={len(faces)} correct={predicted==expected}",flush=True)
    print('DONE. Model loaded once; gallery/dataset not modified.',flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles',default='retinaface_original,retinaface_reuse,yunet_experimental')
    parser.add_argument('--sizes',default='640x360,640x480,320x240')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--threads',type=int,default=2)
    parser.add_argument('--validation',action='store_true')
    parser.add_argument('--validation-only',action='store_true')
    parser.add_argument('--tag',default='all')
    main(parser.parse_args())
