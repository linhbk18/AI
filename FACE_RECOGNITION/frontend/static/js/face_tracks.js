/* Display tracking only. Appearance checks are NOT identity verification or cosine thresholds. */
(() => {
  'use strict';
  const overlap=(a,b)=>{const i=Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y));return i/(a.w*a.h+b.w*b.h-i||1);};
  // Compensate small crop jitter and exposure changes, not identity differences.
  // These are display continuity checks, never biometric acceptance thresholds.
  const difference=(a,b)=>{
    if(!a||!b)return Infinity;
    let best=Infinity;
    for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){
      let delta=0,n=0;
      for(let y=1;y<11;y++)for(let x=1;x<11;x++)for(let c=0;c<3;c++){delta+=a[(y*12+x)*3+c]-b[((y+dy)*12+x+dx)*3+c];n++;}
      const exposure=Math.max(-.08,Math.min(.08,delta/n));let error=0;
      for(let y=1;y<11;y++)for(let x=1;x<11;x++)for(let c=0;c<3;c++)error+=Math.abs(a[(y*12+x)*3+c]-b[((y+dy)*12+x+dx)*3+c]-exposure);
      best=Math.min(best,error/n+Math.abs(exposure)*.25);
    }
    return best;
  };
  const normalized=(box,frame)=>({x:box.x/frame.width,y:box.y/frame.height,w:box.w/frame.width,h:box.h/frame.height});
  class FaceTracks {
    static project(box,videoWidth,videoHeight,width,height,mirror=false){
      const scale=Math.min(width/videoWidth,height/videoHeight),ox=(width-videoWidth*scale)/2,oy=(height-videoHeight*scale)/2;
      return {x:ox+(mirror?1-box.x-box.w:box.x)*videoWidth*scale,y:oy+box.y*videoHeight*scale,w:box.w*videoWidth*scale,h:box.h*videoHeight*scale};
    }
    constructor(){this.nextId=1;this.tracks=[];this.patch=document.createElement('canvas');this.patch.width=this.patch.height=12;}
    reset(){this.tracks=[];this.aspect=null;}
    signature(canvas,b){
      if(b.w<=0||b.h<=0||b.x<0||b.y<0||b.x+b.w>1.01||b.y+b.h>1.01)return null;
      const ctx=this.patch.getContext('2d',{willReadFrequently:true});
      ctx.drawImage(canvas,(b.x+b.w*.1)*canvas.width,(b.y+b.h*.1)*canvas.height,b.w*.8*canvas.width,b.h*.8*canvas.height,0,0,12,12);
      return Array.from(ctx.getImageData(0,0,12,12).data).filter((_,i)=>i%4!==3).map(v=>v/255);
    }
    update(faces,frame,canvas,at){
      const aspect=frame.width/frame.height;if(this.aspect&&Math.abs(aspect-this.aspect)>.01)this.reset();this.aspect=aspect;
      const observations=faces.map(f=>{const box=normalized(f.box,frame);return {box,serverId:f.server_track_id??null,signature:this.signature(canvas,box)};});
      const possible=observations.map(o=>this.tracks.filter(t=>!t.invalid&&(!o.serverId||o.serverId===t.serverId)&&at-t.at<=3000&&overlap(o.box,t.box)>=.6&&difference(o.signature,t.signature)<.065&&difference(o.signature,t.anchor)<.1));
      this.tracks=observations.map((o,i)=>{const matches=possible[i];const old=matches.length===1&&possible.filter(list=>list.includes(matches[0])).length===1?matches[0]:null;
        return {...o,id:old?.id??this.nextId++,firstSeen:old?.firstSeen??at,anchor:old?.anchor??o.signature,at,candidate:old?.candidate??null,unknown:old?.unknown??false,expires:old?.expires??0,removeAt:old?.removeAt??0,stale:old?.stale??false};});
    }
    ticket(canvas,at){if(this.aspect&&Math.abs(canvas.width/canvas.height-this.aspect)>.01)return [];return this.tracks.filter(t=>!t.invalid&&at-t.at<2500).map(t=>({id:t.id,box:{...t.box},signature:this.signature(canvas,t.box),at}));}
    apply(faces,frame,ticket,now){
      this.lastApply={tickets:ticket.length,faces:faces.length,linked:0};
      const matches=faces.map(f=>ticket.filter(t=>overlap(normalized(f.box,frame),t.box)>=.5));
      for(let i=0;i<faces.length;i++){
        const list=matches[i];if(list.length!==1||matches.filter(m=>m.includes(list[0])).length!==1)continue;
        const saved=list[0],track=this.tracks.find(t=>t.id===saved.id),face=faces[i];
        if(!track||track.invalid||now-saved.at>30000||now-track.at>2500||difference(saved.signature,track.signature)>=.065)continue;
        // Backend-classified face (IN_CLASS / UNKNOWN / VERIFIED). PROCESSING keeps the box grey.
        track.candidate=face.status&&face.status!=='PROCESSING'?face:null;
        track.unknown=false;track.stale=false;
        // Display lease only (same continuous face). Writes are decided on the server with a 2 s limit.
        track.expires=Math.min(now+FaceTracks.displayLeaseMs,saved.at+FaceTracks.displayLeaseMs+4000);track.removeAt=track.expires+1000;
        this.lastApply.linked++;
      }
    }
    visible(canvas,now){
      for(const t of this.tracks){t.stale=now>t.expires;if(now>t.removeAt||now-t.at>2500){t.candidate=null;t.unknown=false;}}
      return this.tracks.filter(t=>now-t.at<=2500).map(t=>{
      if(difference(this.signature(canvas,t.box),t.signature)>=.065){t.candidate=null;t.expires=0;t.unknown=false;t.invalid=true;}
      return t;
    });}
  }
  FaceTracks.displayLeaseMs=8000;
  window.SafeFaceTracks=FaceTracks;
})();
