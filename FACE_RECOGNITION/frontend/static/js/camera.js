/* Anonymous preview and serialized identity requests. Camera frames never leave localhost storage in RAM. */
"use strict";
(() => {
  const config=JSON.parse(document.getElementById("app-config").textContent),$=id=>document.getElementById(id);
  const video=$("camera-video"),live=$("live-overlay"),processed=$("processed-frame"),profile=$("camera-profile"),size=$("processing-size");
  let running=false,starting=false,generation=0,session=null,stream=null,ready=false,healthPending=false;
  let previewTimer,identityTimer,animation,previewController,identityController,startController;
  let previewPending=false,identityPending=false,lastPreview=null,scene=0,completed=0,attemptedAt=0,snapshotAt=0,shownAt=0;
  let failures=0,previewFailures=0,lastErrorAt=0;
  let lastPaint=0;
  let noFaceSince=0;
  let lastServerRequestId=0,needsVerification=false;
  const tracks=new window.SafeFaceTracks(),view=document.createElement('canvas');
  let attendanceState={sessionId:null,records:[]};
  const diagnostics=window.cameraDiagnostics={preview_records:[],identity_records:[],identity_active:0,max_identity_active:0};
  function record(kind,value){const rows=diagnostics[kind+"_records"];rows.push(value);if(rows.length>60)rows.shift();}
  const token=()=>({"X-Camera-Token":config.csrfToken});
  const score=(value,digits=4)=>Number.isFinite(value)?value.toFixed(digits):'—';
  function message(text,level="info"){$("message").textContent=text;$("message").dataset.level=level;}
  function text(id,value){if($(id))$(id).textContent=value;}
  function pill(id,value,state=""){if($(id)){$(id).textContent=value;$(id).dataset.state=state;}}
  function empty(text="Chưa có ứng viên."){if(!$("candidate-list"))return;$("candidate-list").replaceChildren(Object.assign(document.createElement("div"),{className:"empty-results",textContent:text}));$("candidate-count").textContent="0";}
  function clearIdentity(message){empty(message);shownAt=0;if(processed){processed.getContext("2d").clearRect(0,0,processed.width,processed.height);processed.hidden=true;$("processed-placeholder").hidden=false;}text("frame-age","Chưa có");text("result-context","Ứng viên từ lần phân tích camera, chưa xác nhận danh tính.");}
  function clearAll(){tracks.reset();lastServerRequestId=0;needsVerification=false;diagnostics.live_labels=[];window.dispatchEvent(new CustomEvent('camera:tracks',{detail:{sessionId:null,studentIds:[]}}));lastPreview=null;scene++;$("face-count").textContent="0";live.getContext("2d").clearRect(0,0,live.width,live.height);clearIdentity();}
  async function closeSession(id){if(!id)return;try{await fetch("/api/camera/sessions/"+encodeURIComponent(id),{method:"DELETE",headers:token(),keepalive:true,signal:AbortSignal.timeout(5000)});}catch{}}
  async function stop(text="Camera đã tắt. Đã dừng cả hai luồng xử lý.",level="info"){
    generation++;running=false;starting=false;clearTimeout(previewTimer);clearTimeout(identityTimer);cancelAnimationFrame(animation);
    previewController?.abort();identityController?.abort();startController?.abort();
    const old=session;session=null;stream?.getTracks().forEach(t=>{t.onended=null;t.stop();});stream=null;video.srcObject=null;
    $("camera-placeholder").hidden=false;$("live-dot").classList.remove("active");pill("camera-state","Đã tắt");
    $("live-stage").textContent="Chưa bật camera";$("recognition-state").textContent="Đã dừng";$("capture-state").textContent="Đã dừng gửi ảnh";
    $("stop-camera").disabled=true;$("start-camera").disabled=!ready||(window.attendanceBridge&&!window.attendanceBridge.canStart());profile.disabled=false;size.disabled=false;clearAll();message(text,level);await closeSession(old);
  }
  async function start(){
    if(running||starting)return;
    noFaceSince=0;
    if(window.attendanceBridge&&!window.attendanceBridge.canStart()){message('Hãy chọn lớp và bắt đầu buổi điểm danh trước.','error');return;}
    const epoch=++generation;starting=true;$("start-camera").disabled=true;$("stop-camera").disabled=false;profile.disabled=true;size.disabled=true;clearAll();
    message("Đang mở camera và khởi tạo phiên xử lý…");
    try{
      startController=new AbortController();const controller=startController;const timer=setTimeout(()=>controller.abort(),10000);
      const options={profile:profile.value};if(window.attendanceBridge){options.attendance_session_id=window.attendanceBridge.getSessionId();options.class_id=window.attendanceBridge.getClassId();}
      let response;try{response=await fetch("/api/camera/sessions",{method:"POST",headers:{...token(),"Content-Type":"application/json"},body:JSON.stringify(options),signal:controller.signal});}finally{clearTimeout(timer);startController=null;}
      const data=await response.json();if(!response.ok)throw new Error(data.error?.message||"Không tạo được phiên camera.");
      if(epoch!==generation){await closeSession(data.session_id);return;}session=data.session_id;
      if(!navigator.mediaDevices?.getUserMedia)throw new Error("Trình duyệt không hỗ trợ camera trên địa chỉ này.");
      const media=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480},aspectRatio:{ideal:4/3},facingMode:"user"},audio:false});
      if(epoch!==generation){media.getTracks().forEach(t=>t.stop());return;}
      stream=media;video.srcObject=media;await video.play();if(epoch!==generation)return;
      media.getVideoTracks().forEach(t=>t.onended=()=>stop("Camera đã ngắt kết nối.","error"));
      running=true;starting=false;completed=0;attemptedAt=performance.now();failures=0;previewFailures=0;
      $("frame-count").textContent="0";$("camera-placeholder").hidden=true;$("live-dot").classList.add("active");pill("camera-state","Camera đang bật","ready");
      message("Đang phát hiện vị trí khuôn mặt. Danh tính sẽ được phân tích bằng RetinaFace + ArcFace.");
      animate();schedulePreview(epoch,0);scheduleIdentity(epoch,250);
    }catch(error){
      console.error("Chi tiết lỗi:", error);
      if(epoch!==generation)return;
      const text=error.name==="NotAllowedError"?"Quyền camera đã bị từ chối. Hãy cho phép camera trong cài đặt website.":error.name==="NotReadableError"?"Không mở được camera. Kiểm tra ứng dụng khác đang sử dụng thiết bị.":error.message;
      await stop(text||"Không thể mở camera.","error");
    }
  }
  function schedulePreview(epoch,delay=250){clearTimeout(previewTimer);if(running&&epoch===generation)previewTimer=setTimeout(()=>preview(epoch),delay);}
  function scheduleIdentity(epoch,delay=400){clearTimeout(identityTimer);if(running&&epoch===generation)identityTimer=setTimeout(()=>identify(epoch),delay);}
  async function capture(maxWidth,maxHeight){
    const began=performance.now();if(!video.videoWidth)throw new Error("Camera chưa có khung hình.");
    const canvas=document.createElement("canvas"),scale=Math.min(1,maxWidth/video.videoWidth,maxHeight/video.videoHeight);
    canvas.width=Math.max(1,Math.round(video.videoWidth*scale));canvas.height=Math.max(1,Math.round(video.videoHeight*scale));
    canvas.getContext("2d").drawImage(video,0,0,canvas.width,canvas.height);
    const trackTicket=tracks.ticket(canvas,began);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",.85));if(!blob)throw new Error("Không mã hóa được ảnh camera.");
    return {canvas,blob,began,trackTicket,capture_ms:performance.now()-began};
  }
  async function requestFrame(endpoint,captured,controller,timeoutMs){
    const timer=setTimeout(()=>controller.abort(),timeoutMs),began=performance.now();
    try{
      const headers={...token(),"Content-Type":"image/jpeg","X-Camera-Session":session,"X-Client-Captured-Ms":String(captured.began)};const classId=window.attendanceBridge?.getClassId?.();if(classId)headers["X-Class-Id"]=String(classId);
      const frameUrl=classId?endpoint+"?class_id="+encodeURIComponent(classId):endpoint;
      const response=await fetch(frameUrl,{method:"POST",body:captured.blob,headers,signal:controller.signal});
      let data;try{data=await response.json();}catch{throw new Error('Backend trả phản hồi không hợp lệ. Kiểm tra Terminal và thử lại.');}if(!response.ok){const error=new Error(data.error?.message||"Backend xử lý thất bại.");error.status=response.status;throw error;}
      if(!Array.isArray(data.faces)||data.frame?.width!==captured.canvas.width||data.frame?.height!==captured.canvas.height)throw new Error("Kết quả backend không khớp khung hình.");
      const http_ms=performance.now()-began,stages={};
      for(const part of (response.headers.get("Server-Timing")||"").split(",")){const [name,value]=part.trim().split(";dur=");if(name&&value)stages[name]=Number(value);}
      return {data,metrics:{capture_ms:captured.capture_ms,http_ms,server_ms:stages.server_total_ms??null,transport_and_client_ms:stages.server_total_ms?Math.max(0,http_ms-stages.server_total_ms):null,stages}};
    }finally{clearTimeout(timer);}
  }
  async function preview(epoch){
    if(!running||epoch!==generation)return;if(previewPending){schedulePreview(epoch);return;}
    previewPending=true;const began=performance.now();
    try{
      const captured=await capture(640,480);if(epoch!==generation)return;
      const controller=previewController=new AbortController();
      const {data,metrics}=await requestFrame("/api/detect",captured,controller,10000);if(!running||epoch!==generation)return;
      record("preview",{...metrics,observed_ms:performance.now()-captured.began,faces:data.faces.length});
      const previous=lastPreview?.faces.length;
      if(previous!==undefined&&previous!==data.faces.length){scene++;clearIdentity("Khung hình đã thay đổi, chờ nhận diện mới.");}
      lastPreview={...data,capturedAt:captured.began};previewFailures=0;
      tracks.update(data.faces,data.frame,captured.canvas,captured.began);
      if(data.faces.length)noFaceSince=0;else if(!noFaceSince)noFaceSince=captured.began;
      if(!identityPending&&tracks.tracks.some(t=>!t.candidate&&captured.began-t.firstSeen<500))scheduleIdentity(epoch,0);
      $("face-count").textContent=String(data.faces.length);$("preview-latency").textContent=metrics.http_ms.toFixed(0)+" ms";
      $("live-stage").textContent=data.faces.length?"Đã phát hiện "+data.faces.length+" khuôn mặt":"Chưa phát hiện khuôn mặt";
      if(!data.faces.length)clearIdentity("Không có khuôn mặt trong lần phát hiện gần nhất.");
      if(lastErrorAt&&failures===0&&performance.now()-lastErrorAt>5000){lastErrorAt=0;message("Đã kết nối lại. Khung nhanh tiếp tục cập nhật; mọi ứng viên chưa xác nhận.");}
    }catch(error){
      if(epoch!==generation||!running)return;
      tracks.reset();lastPreview=null;scene++;clearIdentity();$("face-count").textContent="—";$("live-stage").textContent="Lỗi phát hiện vị trí";previewFailures++;lastErrorAt=performance.now();
      message(error.name==="AbortError"?"Phát hiện vị trí quá thời gian chờ. Đang thử lại; không giữ khung cũ.":(error.message||"Mất kết nối backend. Đang thử lại."),"error");
      if(error.status===409||error.status===403){await stop("Phiên camera hết hạn. Hãy tải lại trang và bật lại.","error");return;}
    }finally{previewPending=false;previewController=null;if(epoch===generation)schedulePreview(epoch,Math.min(5000,previewFailures?1000*previewFailures:Math.max(200,(identityPending?1000:400)-(performance.now()-began))));}
  }
  function boxes(ctx,faces,scale,ox=0,oy=0,names=false,width=10000){
    ctx.font="12px Segoe UI";ctx.lineWidth=2;
    for(const face of faces){const b=face.box,unknown=face.unknown===true||face.scope_match===false||face.status==='unknown'||face.name==='Không xác định'||face.label==='Không xác định';ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue(unknown?"--danger":names?"--success":"--primary").trim();ctx.strokeRect(ox+b.x*scale,oy+b.y*scale,b.w*scale,b.h*scale);
      const label=names?(unknown?"Không xác định":face.name||face.top1.student?.full_name||"Ứng viên chưa xác nhận"):"#"+face.face_id+" · khuôn mặt";
      const tw=Math.min(width,ctx.measureText(label).width+10),x=Math.max(0,Math.min(width-tw,ox+b.x*scale)),y=Math.max(0,oy+b.y*scale-23);
      ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue("--navy").trim();ctx.fillRect(x,y,tw,21);ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue("--white").trim();ctx.fillText(label,x+5,y+15,tw-8);
    }
  }
  function renderCandidates(faces,age){
    if(!$("candidate-list"))return;
    $("candidate-count").textContent=String(faces.length);
    $("result-context").textContent=processed?"Ứng viên của ảnh đối chiếu chụp trước "+(age/1000).toFixed(1)+" giây; không phải danh tính trên video hiện tại.":"Ứng viên từ ảnh camera chụp trước "+(age/1000).toFixed(1)+" giây. Hãy đối chiếu trước khi xác nhận.";
    if(!faces.length){empty("RetinaFace không tìm thấy khuôn mặt trong ảnh đối chiếu.");return;}
    const cards=faces.map(face=>{
      const display=window.RecognitionDisplay.describe(face);
      const card=document.createElement("article");card.className="candidate-item";
      const top=document.createElement("div");top.className="candidate-top";
      for(const [text,cls] of [["#"+face.face_id,"face-number"],[display.name,"candidate-name"],[score(face.top1.similarity),"candidate-score"]]){const span=document.createElement("span");span.className=cls;span.textContent=text;top.append(span);}
      const label=document.createElement("div");label.className="candidate-label";label.textContent=display.verified?"Danh tính đã xác minh":"Gợi ý AI — chưa xác minh, không tự ghi điểm danh";
      const code=document.createElement("div");code.className="candidate-detail";code.textContent=display.code?"Mã: "+face.top1.student.student_code+(face.top1.student.code_is_temporary?" · mã tạm demo":""):"Chưa có hồ sơ SQLite được ánh xạ";
      const second=document.createElement("div");second.className="candidate-detail";second.textContent=display.verified?"Đã vượt kiểm tra xác minh.":"Đây là ứng viên gần nhất, chưa phải kết luận danh tính.";
      const margin=document.createElement("div");margin.className="candidate-detail";margin.textContent="Chênh lệch: "+score(face.margin);card.append(top,label,code,second,margin);return card;
    });$("candidate-list").replaceChildren(...cards);
  }
  async function identify(epoch){
    if(!running||epoch!==generation)return;
    if(identityPending){scheduleIdentity(epoch);return;}
    // Preview is advisory, not an identity gate. A periodic RetinaFace probe also
    // covers faces missed by YuNet, without re-embedding every preview frame.
    const now=performance.now();
    // Give a newly empty scene time before a recall fallback RetinaFace probe.
    // Otherwise an empty-frame inference can block the next arriving student.
    if(noFaceSince&&now-noFaceSince<8000){scheduleIdentity(epoch,300);return;}
    // Reuse display-only tracking. Never turn this cache into identity evidence.
    const activeTracks=tracks.tracks.filter(t=>!t.invalid&&now-t.at<2500);
    const lead=Math.min(9000,diagnostics.identity_records.at(-1)?.http_ms??1000);
    if(!needsVerification&&activeTracks.length&&activeTracks.every(t=>t.candidate&&t.expires-now>lead)){
      diagnostics.tracking_reuses=(diagnostics.tracking_reuses||0)+1;scheduleIdentity(epoch,300);return;
    }
    if((!lastPreview||!tracks.tracks.length)&&now-attemptedAt<8000){scheduleIdentity(epoch);return;}
    identityPending=true;attemptedAt=now;
    // Do not clear a stable track's candidate when starting another request.
    $("recognition-state").textContent="";$("capture-state").textContent="ArcFace đang xử lý một ảnh; video và khung nhanh vẫn chạy.";
    let delay=1200;
    try{
      const target=size.value.split("x").map(Number),captured=await capture(...target);if(epoch!==generation)return;
      const controller=identityController=new AbortController();diagnostics.identity_active++;diagnostics.max_identity_active=Math.max(diagnostics.max_identity_active,diagnostics.identity_active);
      let reply;try{reply=await requestFrame("/api/recognize",captured,controller,config.requestTimeoutMs);}finally{diagnostics.identity_active--;}
      if(!running||epoch!==generation)return;
      const {data,metrics}=reply,observed=performance.now()-captured.began;completed++;failures=0;lastErrorAt=0;
      if(!Number.isInteger(data.request_id)||data.request_id<=lastServerRequestId)return;
      lastServerRequestId=data.request_id;
      needsVerification=data.automatic_attendance?.pending_consistency===true;
      record("identity",{...metrics,observed_ms:observed,faces:data.detected_faces,profile:data.profile});
      $("frame-count").textContent=String(completed);$("latency").textContent=(metrics.http_ms/1000).toFixed(1)+"s";$("observed-latency").textContent=(observed/1000).toFixed(1)+"s";
      if(lastPreview&&lastPreview.faces.length===0){
        clearIdentity("Ảnh đã cũ hoặc khuôn mặt đã rời khung. Chờ ảnh mới.");$("recognition-state").textContent="Bỏ kết quả không còn phù hợp";return;
      }
      snapshotAt=captured.began;shownAt=performance.now();
      tracks.apply(data.faces,data.frame,captured.trackTicket,shownAt);
      diagnostics.last_binding={...tracks.lastApply};
      const grants=(data.automatic_attendance?.grants||[]).filter(g=>tracks.tracks.some(t=>!t.invalid&&!t.stale&&t.serverId===g.server_track_id&&t.candidate?.face_id===g.face_id));
      if(grants.length&&running&&epoch===generation){
        const response=await fetch('/api/camera/sessions/'+encodeURIComponent(session)+'/attendance/commit',{
          method:'POST',headers:{...token(),'Content-Type':'application/json'},
          body:JSON.stringify({grant_ids:grants.map(g=>g.grant_id)}),signal:controller.signal});
        const saved=await response.json();
        if(!running||epoch!==generation)return;
        if(response.ok&&saved.session)window.dispatchEvent(new CustomEvent('attendance:auto',{detail:saved.session}));
        else if(!response.ok){lastErrorAt=performance.now();needsVerification=true;message(saved.error?.message||'Chưa ghi được điểm danh tự động. Đang chờ xác minh mới.','error');}
        diagnostics.last_auto_commit={ok:response.ok,changed:saved.changed,session_id:saved.session?.id};
      }
      diagnostics.time_to_name_ms=tracks.tracks.filter(t=>t.candidate&&!t.stale).map(t=>({trackId:t.id,ms:shownAt-t.firstSeen}));
      if(processed){processed.width=captured.canvas.width;processed.height=captured.canvas.height;const ctx=processed.getContext("2d");ctx.drawImage(captured.canvas,0,0);boxes(ctx,data.faces,1,0,0,true,processed.width);processed.hidden=false;$("processed-placeholder").hidden=true;}
      renderCandidates(data.faces,observed);
      window.dispatchEvent(new CustomEvent('camera:candidates',{detail:{faces:data.faces,sessionId:data.attendance_session_id??null}}));
      if(data.automatic_attendance?.session)window.dispatchEvent(new CustomEvent('attendance:auto',{detail:data.automatic_attendance.session}));
      if(data.automatic_attendance?.status)showAutomationStatus(data.automatic_attendance.status);
      $("recognition-state").textContent=data.faces.some(f=>f.identity_confirmed)?"Có danh tính đã xác minh":data.faces.length?"Có gợi ý AI · cần đối chiếu":"Không tìm thấy mặt";
      $("capture-state").textContent="Đã phân tích "+completed+" ảnh · "+data.profile;
      if(data.face_errors)message("Có khuôn mặt chưa xử lý được. Xem Terminal backend.","error");
      else if(!lastErrorAt)message(data.faces.length&&!tracks.lastApply.linked?"Khuôn mặt đã thay đổi hoặc kết quả đã cũ. Đang chờ nhận diện mới.":data.automatic_attendance?.enabled?(needsVerification?'Đang kiểm tra nhận diện nhất quán trên cùng khuôn mặt.':'Tự động đang bật. Kết quả đủ điều kiện sẽ được lưu Có mặt.'):'Đã cập nhật ứng viên AI. Tự động đang khóa; bạn có thể xác nhận thủ công trong danh sách lớp.',"info");
      delay=data.faces.length&&!tracks.lastApply.linked?0:Math.max(1200,Math.min(3000,metrics.http_ms*.2));
    }catch(error){
      if(epoch!==generation||!running)return;
      tracks.reset();failures++;lastErrorAt=performance.now();clearIdentity();$("capture-state").textContent="Chưa có kết quả · đang chờ thử lại";$("recognition-state").textContent=error.name==="AbortError"?"Quá thời gian chờ":"Lỗi nhận diện";
      message(error.name==="AbortError"?"Nhận diện quá thời gian chờ. Đang đợi tác vụ cũ kết thúc; không gửi dồn request.":(error.message||"Mất kết nối backend; đang thử lại."),"error");
      delay=Math.min(10000,2000*failures);
      if(error.status===409||error.status===403){await stop("Phiên camera hết hạn. Hãy tải lại trang.","error");return;}
    }finally{identityPending=false;identityController=null;if(epoch===generation)scheduleIdentity(epoch,delay);}
  }
  function animate(){
    if(!running)return;
    const now=performance.now();
    // Video remains native/continuous. Overlay and elapsed labels need only 10 Hz.
    if(now-lastPaint<100){animation=requestAnimationFrame(animate);return;}lastPaint=now;
    const bounds=live.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
    const w=Math.round(bounds.width*dpr),h=Math.round(bounds.height*dpr);if(live.width!==w||live.height!==h){live.width=w;live.height=h;}
    const ctx=live.getContext("2d");ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,bounds.width,bounds.height);
    diagnostics.live_labels=[];
    window.dispatchEvent(new CustomEvent('camera:tracks',{detail:{sessionId:null,studentIds:[]}}));
    if(lastPreview&&now-lastPreview.capturedAt<2500&&Math.abs(lastPreview.frame.width/lastPreview.frame.height-video.videoWidth/video.videoHeight)<.01){
      if(video.videoWidth){
        const vh=Math.max(1,Math.round(320*video.videoHeight/video.videoWidth));
        if(view.width!==320||view.height!==vh){view.width=320;view.height=vh;}
        view.getContext('2d').drawImage(video,0,0,view.width,view.height);
        drawLiveTracks(ctx,tracks.visible(view,now),bounds.width,bounds.height);
      }
    }
    if(shownAt){text("frame-age","Ảnh chụp trước "+((now-snapshotAt)/1000).toFixed(1)+"s");if(now-shownAt>3500)clearIdentity("Kết quả đã hết thời gian hiển thị. Chờ kết quả mới.");}
    if(identityPending)$("recognition-state").textContent="";
    animation=requestAnimationFrame(animate);
  }
  function drawLiveTracks(ctx,items,width,height){
    const theme=getComputedStyle(document.documentElement),mirror=$('mirror-camera')?.checked===true;
    video.style.transform=mirror?'scaleX(-1)':'';
    ctx.font='600 12px Segoe UI';ctx.lineWidth=2;
    for(const t of items){
      const rect=window.SafeFaceTracks.project(t.box,video.videoWidth,video.videoHeight,width,height,mirror),{x:left,y:top,w,h}=rect;
      ctx.strokeStyle=theme.getPropertyValue(t.unknown&&!t.stale?'--danger':'--success').trim();ctx.strokeRect(left,top,w,h);
      const f=t.candidate,p=f?.top1?.student,confirmed=p&&attendanceState.sessionId===window.attendanceBridge?.getSessionId()&&attendanceState.records.some(r=>r.student_id===p.id&&r.attendance_status==='PRESENT');
      const lines=t.unknown&&!t.stale?['Không xác định']:window.RecognitionDisplay.describe(f,t.stale,confirmed).lines;
      t.displayLines=lines;t.displayBox=rect;
      if(!lines.length)continue;
      const labelWidth=Math.min(width,Math.max(...lines.map(line=>ctx.measureText(line).width))+16),labelHeight=lines.length*19+8;
      const x=Math.max(0,Math.min(left,width-labelWidth)),y=top>=labelHeight?top-labelHeight:Math.min(height-labelHeight,top+h);
      ctx.fillStyle=theme.getPropertyValue('--navy').trim();ctx.fillRect(x,Math.max(0,y),labelWidth,labelHeight);ctx.fillStyle=theme.getPropertyValue('--white').trim();lines.forEach((line,i)=>ctx.fillText(line,x+8,Math.max(0,y)+18+i*19,labelWidth-16));
      // Diagnostics contain display strings only, never an embedding or camera image.
      t.displayLines=lines;
      t.displayBox=rect;
    }
    diagnostics.live_labels=items.map(t=>({trackId:t.id,lines:t.displayLines||[],box:t.displayBox,stale:!!t.stale}));
    window.dispatchEvent(new CustomEvent('camera:tracks',{detail:{sessionId:window.attendanceBridge?.getSessionId()??null,studentIds:items.filter(t=>t.candidate?.identity_confirmed&&!t.candidate.outside_class&&!t.stale&&t.candidate.top1?.student).map(t=>t.candidate.top1.student.id)}}));
  }
  window.addEventListener('attendance:state',e=>{attendanceState=e.detail;});
  function showAutomationStatus(status){
    const note=document.querySelector('.policy-notice p');if(note)note.textContent=status.ready?'AI đang xác minh để tự ghi điểm danh.':'Bạn vẫn xem được gợi ý AI và xác nhận thủ công. Tự động ghi điểm danh chưa bật vì chưa có đánh giá độc lập đạt yêu cầu.';
    const title=document.querySelector('.policy-notice strong');if(title)title.textContent=status.ready?'Chế độ điểm danh tự động':'Chế độ xác nhận thủ công';
    diagnostics.automatic_attendance=status;
    const panel=document.querySelector('.policy-notice');
    if(panel){let button=$('automatic-mode');if(!button){button=document.createElement('button');button.id='automatic-mode';button.type='button';button.className='button secondary';panel.append(button);button.onclick=async()=>{button.disabled=true;await stop('Đổi chế độ điểm danh. Hãy bật lại camera.');try{const r=await fetch('/api/attendance/automation-mode',{method:'POST',headers:{...token(),'Content-Type':'application/json'},body:JSON.stringify({enabled:!diagnostics.automatic_attendance.requested,profile:profile.value})});const d=await r.json();if(!r.ok)throw Error(d.error?.message||'Không đổi được chế độ.');showAutomationStatus(d);}catch(e){message(e.message,'error');await health();}};}button.textContent=status.requested?'Tắt điểm danh tự động':'Bật điểm danh tự động';button.disabled=!status.requested&&!status.can_enable;button.title=status.can_enable?'Chỉ ghi khi VERIFIED, track hợp lệ và thuộc lớp.':status.message;}
  }
  async function health(){
    if(healthPending)return;healthPending=true;
    try{
      const response=await fetch("/api/health?profile="+encodeURIComponent(profile.value),{cache:"no-store",signal:AbortSignal.timeout(5000)});if(!response.ok)throw new Error();
      const data=await response.json();if(data.application!=="face_recognition_camera_v1")throw new Error();
      if(data.code_reload_required){
        ready=false;pill('backend-state','Cần khởi động lại Flask','error');$('start-camera').disabled=true;
        if(running)await stop('Mã nguồn đã thay đổi hoặc đổi nhánh Git. Khởi động lại Flask để tránh dùng lẫn hai phiên bản.','error');
        else message('Mã nguồn đã thay đổi hoặc đổi nhánh Git. Cần khởi động lại Flask.','error');
        return;
      }
      if(data.automatic_attendance)showAutomationStatus(data.automatic_attendance);
      ready=data.ready===true;pill("backend-state",ready?"Backend sẵn sàng":data.state==="error"?"Lỗi mô hình":"Đang tải mô hình",ready?"ready":data.state==="error"?"error":"busy");
      text("gallery-vectors",String(data.registered_embeddings??"—"));text("gallery-students",String(data.registered_students??"—"));
      text("gallery-count",ready?"Gallery từ backend · giữ nguyên dữ liệu đăng ký":"Đang nạp dữ liệu…");pill("gallery-state",ready?"Đã tải":"Đang tải",ready?"ready":"busy");
      $("start-camera").disabled=running||starting||!ready||(window.attendanceBridge&&!window.attendanceBridge.canStart());
      if(!running&&!starting&&$("message").dataset.level!=="error")message(ready?"Backend sẵn sàng. Bật camera để bắt đầu.":data.state==="error"?"Lỗi khởi tạo mô hình. Xem Terminal Flask.":"Đang nạp mô hình; lần khởi động đầu có thể mất một lúc.",data.state==="error"?"error":"info");
    }catch{ready=false;pill("backend-state","Mất kết nối","error");pill("gallery-state","Chưa kiểm tra","error");text("gallery-count","Mất kết nối · đang giữ số liệu lần kiểm tra gần nhất");$("start-camera").disabled=true;if(!running)message("Mất kết nối backend. Kiểm tra Flask tại cổng 5055.","error");}
    finally{healthPending=false;}
  }
  profile.addEventListener("change",()=>{$("detector-name").textContent=profile.value==="retinaface_reuse"?"RetinaFace gốc + ArcFace":profile.value==="retinaface_scaled_experimental"?"RetinaFace 480 / crop gốc + ArcFace":"RetinaFace không phóng ảnh + ArcFace";clearAll();});
  size.addEventListener("change",()=>{$("profile-note").textContent=size.value==="640x480"?"Khung nhanh YuNet; nhận diện RetinaFace + ArcFace, không xác nhận danh tính.":"Độ phân giải khảo sát: không kế thừa accuracy ở 640×480. Giảm ảnh có thể làm giảm độ đúng.";clearAll();});
  $("start-camera").addEventListener("click",start);$("stop-camera").addEventListener("click",()=>stop());
  window.cameraControls={stop,isReady:()=>ready};
  $('mirror-camera')?.addEventListener('change',()=>{video.style.transform=$('mirror-camera').checked?'scaleX(-1)':'';live.getContext('2d').clearRect(0,0,live.width,live.height);});
  window.addEventListener("pagehide",()=>stop());document.addEventListener("visibilitychange",()=>{if(document.hidden&&(running||starting))stop("Camera đã tắt khi rời tab.");});
  health();setInterval(health,5000);
})();

