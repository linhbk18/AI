"""RAM-only display continuity, independent of biometric score thresholds."""
import secrets
import numpy as np
import cv2


def iou(a,b):
    area=max(0,min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0]))*max(0,min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1]))
    return area/(a[2]*a[3]+b[2]*b[3]-area or 1)


def box(face,frame):
    b=face['box'];return (b['x']/frame['width'],b['y']/frame['height'],b['w']/frame['width'],b['h']/frame['height'])


class Continuity:
    def __init__(self):self.tracks=[];self.aspect=None;self.last_at=-1

    @staticmethod
    def signature(image,b):
        h,w=image.shape[:2];x,y,bw,bh=b
        x1,y1=max(0,round((x+.1*bw)*w)),max(0,round((y+.1*bh)*h))
        x2,y2=min(w,round((x+.9*bw)*w)),min(h,round((y+.9*bh)*h))
        crop=image[y1:y2,x1:x2]
        return cv2.resize(crop,(12,12)).astype(np.float32)/255 if crop.size else None

    @staticmethod
    def difference(a,b):
        if a is None or b is None:return float('inf')
        best=float('inf')
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                d=a[1:11,1:11]-b[1+dy:11+dy,1+dx:11+dx]
                exposure=np.clip(d.mean(),-.08,.08)
                best=min(best,float(np.abs(d-exposure).mean()+abs(exposure)*.25))
        return best

    def update(self,faces,frame,image,at):
        if at<=self.last_at:return
        self.last_at=at
        aspect=frame['width']/frame['height']
        if self.aspect and abs(aspect-self.aspect)>.01:self.tracks=[]
        self.aspect=aspect
        observations=[dict(box=box(f,frame)) for f in faces]
        for o in observations:o['signature']=self.signature(image,o['box'])
        matches=[[t for t in self.tracks if 0<=at-t['at']<3 and iou(o['box'],t['box'])>=.6
            and self.difference(o['signature'],t['signature'])<.065 and self.difference(o['signature'],t['anchor'])<.1] for o in observations]
        new=[]
        for o,m in zip(observations,matches):
            old=m[0] if len(m)==1 and sum(any(item is m[0] for item in row) for row in matches)==1 else None
            new.append(dict(**o,id=old['id'] if old else secrets.token_urlsafe(16),at=at,anchor=old['anchor'] if old else o['signature']))
        self.tracks=new
        for f,t in zip(faces,new):f['server_track_id']=t['id']

    def snapshot(self,image,at):
        if not self.aspect or abs(image.shape[1]/image.shape[0]-self.aspect)>.01:return []
        return [dict(id=t['id'],box=t['box'],signature=self.signature(image,t['box']),captured_at=at)
            for t in self.tracks if 0<=at-t['at']<2.5 and self.difference(self.signature(image,t['box']),t['signature'])<.065]

    def valid(self,saved,now):
        current=next((t for t in self.tracks if t['id']==saved['id']),None)
        return bool(current and 0<=now-current['at']<2.5 and 0<=now-saved['captured_at']<=30
            and self.difference(saved['signature'],current['signature'])<.065)

    def bind(self,faces,frame,snapshot,now):
        matches=[[s for s in snapshot if iou(box(f,frame),s['box'])>=.5] for f in faces]
        linked=[]
        for face,m in zip(faces,matches):
            saved=m[0] if len(m)==1 and sum(any(item is m[0] for item in row) for row in matches)==1 else None
            good=bool(saved and self.valid(saved,now))
            face['tracking']=dict(valid=good,server_track_id=saved['id'] if good else None)
            linked.append(saved if good else None)
        return linked
