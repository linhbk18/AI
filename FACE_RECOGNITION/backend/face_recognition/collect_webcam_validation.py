"""Operator-controlled webcam capture for NEW labelled data; never recognises or enrols a person."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import re
import subprocess
import sys
import uuid
import sqlite3
from contextlib import closing

import cv2
from ..config import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity',required=True,help='Exact SQLite student code, or UNKNOWN:new-person-code')
    parser.add_argument('--split',choices=['calibration','evaluation'],required=True)
    parser.add_argument('--capture-session',required=True,help='A genuinely independent visit/session, not a new ID for adjacent frames')
    parser.add_argument('--camera',type=int,default=0)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_:.-]{1,100}',args.identity):raise SystemExit('Use a student code or anonymous UNKNOWN:code.')
    with closing(sqlite3.connect(ROOT/'outputs/management.sqlite3')) as db:
        codes={r[0] for r in db.execute('SELECT student_code FROM students WHERE deleted_at IS NULL')}
    if args.identity not in codes and not re.fullmatch(r'UNKNOWN:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',args.identity):
        raise SystemExit('Choose an existing student code, or UNKNOWN:U01 with a nonempty anonymous person code. Camera not opened.')
    kind='unknown' if args.identity.startswith('UNKNOWN:') else 'known'
    target=ROOT/'outputs/verification_data'/kind/args.split;target.mkdir(parents=True,exist_ok=True)
    cap=cv2.VideoCapture(args.camera)
    if not cap.isOpened():raise SystemExit('Cannot open webcam. Close other camera applications or choose --camera 1.')
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,640);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
    saved=None
    try:
        print('SPACE: save ONE image for this labelled visit; Q/ESC: cancel. No AI labels are used.',flush=True)
        while True:
            ok,frame=cap.read()
            if not ok:raise RuntimeError('Webcam disconnected or could not read a frame.')
            scale=min(1,640/frame.shape[1],480/frame.shape[0])
            if scale<1:frame=cv2.resize(frame,(round(frame.shape[1]*scale),round(frame.shape[0]*scale)))
            display=frame.copy();cv2.putText(display,'SPACE: capture | Q: cancel | one person per image',(10,25),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
            cv2.imshow('Independent verification capture - no recognition',display)
            key=cv2.waitKey(20)&255
            if key in (27,ord('q')):break
            if key==32:
                saved=target/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:12]+'.jpg')
                if not cv2.imwrite(str(saved),frame,[cv2.IMWRITE_JPEG_QUALITY,85]):raise RuntimeError('Could not save camera image.')
                break
    finally:cap.release();cv2.destroyAllWindows()
    if saved:
        result=subprocess.run([sys.executable,'-m','backend.face_recognition.verification_workflow','add-sample',
            '--source',str(saved.relative_to(ROOT)),'--identity',args.identity,'--split',args.split,'--capture-session',args.capture_session],cwd=ROOT)
        if result.returncode:raise SystemExit('Photo kept at '+str(saved)+'; manifest validation failed. Correct the label/session; do not retune on holdout.')
        print('Saved real labelled image: '+str(saved))


if __name__=='__main__':main()
