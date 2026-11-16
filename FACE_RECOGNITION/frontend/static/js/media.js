/* All static-media confirmations are explicit human review, never live-presence verification. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),mode=$('input-source');if(!mode)return;
  const config=JSON.parse($('app-config').textContent),video=$('media-video'),canvas=$('media-canvas');
  let generation=0,frameGeneration=0,job=null,file=null,url=null,stream=null,timer=null,original=null,lastSession=null,records=[];
  const headers=()=>({'X-Camera-Token':config.csrfToken});
  const status=(message,error=false)=>{$('media-status').textContent=message;$('media-status').dataset.level=error?'error':'info';};
  async function api(path,options={}){const r=await fetch(path,{...options,headers:{...headers(),...options.headers}});const data=await r.json();if(!r.ok)throw new Error(data.error?.message||'Không xử lý được yêu cầu.');return data;}
  function closeStream(){stream?.getTracks().forEach(t=>t.stop());stream=null;video.srcObject=null;}
  async function cancel(){generation++;clearTimeout(timer);const old=job;job=null;closeStream();$('cancel-media').disabled=true;if(old&&['queued','running','cancelling'].includes(old.state)){try{await api('/api/media/jobs/'+old.id,{method:'DELETE'});}catch{}}}
  function reset(){file=null;original=null;$('media-file').value='';$('media-file-info').textContent='';if(url)URL.revokeObjectURL(url);url=null;video.removeAttribute('src');video.hidden=true;canvas.hidden=true;$('media-review').hidden=true;$('media-students').replaceChildren();$('media-frame-label').hidden=true;$('media-progress').hidden=true;$('analyze-media').disabled=true;$('capture-source').disabled=true;}
  mode.addEventListener('change',async()=>{await window.cameraControls?.stop();await cancel();reset();
    const kind=mode.value;$('media-panel').hidden=kind==='live_camera';document.querySelector('.camera-card').hidden=kind!=='live_camera';
    const details=document.querySelector('.system-details');if(details)details.hidden=kind!=='live_camera';
    $('open-source-camera').hidden=kind!=='snapshot';$('capture-source').hidden=kind!=='snapshot';$('media-file-label').hidden=kind==='snapshot';
    $('media-file').accept=kind==='video'?'.mp4,.mov,.webm,.avi':'image/jpeg,image/png,image/webp';
    $('media-title').textContent={snapshot:'Chụp ảnh để nhận diện',image:'Nhận diện từ ảnh',video:'Nhận diện từ video'}[kind]||'Nhận diện';
    status(window.attendanceBridge?.canStart()?'Chọn nguồn để phân tích.':'Chọn lớp và bắt đầu buổi điểm danh trước.');
  });
  async function choose(selected){await cancel();reset();file=selected;if(!file)return;
    const limit=(mode.value==='video'?64:8)*1024*1024;if(file.size>limit){status('Tệp vượt giới hạn dung lượng.',true);file=null;return;}
    url=URL.createObjectURL(file);$('analyze-media').disabled=false;
    $('media-file-info').textContent=file.name+' · '+(file.size/1024/1024).toFixed(2)+' MB';
    if(mode.value==='video'){video.controls=true;video.src=url;video.hidden=false;status(file.name+' · '+(file.size/1024/1024).toFixed(1)+' MB · video chỉ tạo đề xuất.');}
    else {const epoch=generation;original=new Image();original.src=url;try{await original.decode();if(epoch!==generation)return;const s=Math.min(1,640/original.naturalWidth,480/original.naturalHeight);canvas.width=Math.round(original.naturalWidth*s);canvas.height=Math.round(original.naturalHeight*s);canvas.getContext('2d').drawImage(original,0,0,canvas.width,canvas.height);canvas.hidden=false;status(file.name+' · ảnh chỉ tạo đề xuất.');}catch{if(epoch===generation){status('Không đọc được ảnh đã chọn.',true);$('analyze-media').disabled=true;}}}
  }
  $('media-file').addEventListener('change',()=>choose($('media-file').files[0]));
  $('open-source-camera').addEventListener('click',async()=>{if(!window.attendanceBridge?.canStart()){status('Hãy bắt đầu buổi điểm danh trước.',true);return;}await cancel();reset();try{
    const epoch=generation,media=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480}},audio:false});if(epoch!==generation||mode.value!=='snapshot'){media.getTracks().forEach(t=>t.stop());return;}stream=media;video.srcObject=stream;video.controls=false;video.hidden=false;await video.play();$('capture-source').disabled=false;status('Camera chỉ xem trước. Nhấn Chụp ảnh để phân tích.');
  }catch(e){status(e.name==='NotAllowedError'?'Quyền camera bị từ chối.':'Không mở được camera.',true);}});
  $('capture-source').addEventListener('click',async()=>{if(!video.videoWidth)return;const c=document.createElement('canvas'),s=Math.min(1,640/video.videoWidth,480/video.videoHeight);c.width=Math.round(video.videoWidth*s);c.height=Math.round(video.videoHeight*s);c.getContext('2d').drawImage(video,0,0,c.width,c.height);
    const blob=await new Promise(resolve=>c.toBlob(resolve,'image/jpeg',.9));closeStream();if(blob){await choose(new File([blob],'snapshot.jpg',{type:'image/jpeg'}));await analyze();}});
  async function analyze(){if(!file||!window.attendanceBridge?.canStart()){status('Cần chọn tệp và bắt đầu buổi điểm danh.',true);return;}
    await cancel();const epoch=generation;const sid=window.attendanceBridge.getSessionId(),data=new FormData();data.append('file',file);data.append('source_type',mode.value);data.append('session_id',sid);data.append('profile',$('camera-profile').value);
    $('analyze-media').disabled=true;$('cancel-media').disabled=false;$('media-review').hidden=true;$('media-students').replaceChildren();$('media-progress').hidden=false;$('media-progress').removeAttribute('value');status('Đang gửi và kiểm tra tệp…');
    try{const created=await api('/api/media/jobs',{method:'POST',body:data});if(epoch!==generation){await api('/api/media/jobs/'+created.id,{method:'DELETE'});return;}job=created;await poll(epoch,sid);}catch(e){status(e.message,true);$('analyze-media').disabled=false;$('cancel-media').disabled=true;}}
  async function poll(epoch,sid){try{const data=await api('/api/media/jobs/'+job.id);if(epoch!==generation||sid!==window.attendanceBridge?.getSessionId())return;job=data;
    if(data.progress===null)$('media-progress').removeAttribute('value');else $('media-progress').value=data.progress;
    $('media-file-info').textContent=data.filename+' · '+(data.bytes/1024/1024).toFixed(2)+' MB'+(data.duration_seconds?' · '+data.duration_seconds.toFixed(2)+' giây · '+data.fps.toFixed(2)+' fps':'');
    status(data.state==='running'?'Đã phân tích '+data.processed_frames+'/'+(data.total_frames??'?')+' frame.':data.state==='completed'?'Đã phân tích xong. Hãy duyệt danh sách trước khi ghi Có mặt.':data.error||'Đang kiểm tra tệp…');
    if(['queued','running','cancelling'].includes(data.state)){timer=setTimeout(()=>poll(epoch,sid),800);return;}
    $('cancel-media').disabled=true;$('analyze-media').disabled=false;
    if(data.state==='completed'){if(data.face_errors)status('Đã phân tích nhưng có '+data.face_errors+' khuôn mặt bị lỗi; hãy kiểm tra thủ công.',true);review();$('media-frame').replaceChildren(...data.frames.map((f,i)=>new Option('Khung '+(i+1)+(f.timestamp_seconds===null?'':' · '+(f.timestamp_estimated?'≈':'')+f.timestamp_seconds.toFixed(2)+'s'),i)));$('media-frame-label').hidden=data.frames.length<=1;await showFrame(0);}
  }catch(e){if(epoch===generation){status(e.message,true);$('analyze-media').disabled=false;}}}
  function review(){if(!job)return;$('media-review').hidden=false;const list=$('media-students'),selected=new Set(Array.from(list.querySelectorAll('input:checked')).map(x=>Number(x.value)));list.replaceChildren();
    for(const p of job.students){const label=document.createElement('label');label.className='media-proposal';const input=document.createElement('input');input.type='checkbox';input.value=p.student_id;input.disabled=records.some(r=>r.student_id===p.student_id&&r.attendance_status==='PRESENT');
      input.checked=!input.disabled&&selected.has(p.student_id);const text=document.createElement('span');text.textContent=p.student_code+' · '+p.student_name+' · '+p.similarity.toFixed(4)+(input.disabled?' · Đã điểm danh':' · Ứng viên chưa xác nhận')+(p.first_seen_seconds===null?'':' · '+p.first_seen_seconds.toFixed(2)+'–'+p.last_seen_seconds.toFixed(2)+'s');label.append(input,text);list.append(label);}
    if(!job.students.length)list.textContent='Không có ứng viên đủ thông tin thuộc lớp. Có thể điểm danh thủ công từ danh sách lớp.';
    $('confirm-media').disabled=!job.students.some(p=>!records.some(r=>r.student_id===p.student_id&&r.attendance_status==='PRESENT'));
  }
  async function showFrame(index){const frame=job?.frames[index],epoch=generation,paint=++frameGeneration,expectedJob=job?.id,kind=mode.value;if(!frame)return;
    if(kind==='video'){try{await new Promise(resolve=>{const target=frame.timestamp_seconds||0;if(video.readyState>=2&&!video.seeking&&Math.abs(video.currentTime-target)<.01){resolve();return;}video.addEventListener('seeked',resolve,{once:true});video.currentTime=target;setTimeout(resolve,2000);});}catch{}}
    if(epoch!==generation||paint!==frameGeneration||expectedJob!==job?.id||kind!==mode.value)return;
    const source=kind==='video'?video:original;if(!source)return;if(kind==='video'&&video.readyState<2){status('Backend đã phân tích nhưng trình duyệt chưa giải mã được video để xem khung; hãy dùng MP4 có codec tương thích.',true);return;}canvas.width=frame.frame.width;canvas.height=frame.frame.height;const ctx=canvas.getContext('2d');ctx.drawImage(source,0,0,canvas.width,canvas.height);canvas.hidden=false;canvas.dataset.frameIndex=String(index);canvas.dataset.sourceJob=expectedJob;
    ctx.font='600 12px Segoe UI';ctx.lineWidth=2;
    for(const face of frame.faces){const b=face.bounding_box;ctx.lineWidth=3;ctx.strokeStyle=face.student_name?'#16a34a':'#dc2626';ctx.strokeRect(b.x,b.y,b.w,b.h);const label=face.student_name?face.student_name+' · '+face.student_code+' · Chờ xác nhận': 'Không xác định';const w=Math.min(canvas.width,ctx.measureText(label).width+12),x=Math.max(0,Math.min(b.x,canvas.width-w)),y=Math.max(0,b.y-25);ctx.fillStyle='#142D4E';ctx.fillRect(x,y,w,23);ctx.fillStyle='#fff';ctx.fillText(label,x+6,y+16,w-12);}}
  $('media-frame').addEventListener('change',()=>showFrame(Number($('media-frame').value)));
  $('analyze-media').addEventListener('click',analyze);$('cancel-media').addEventListener('click',async()=>{await cancel();status('Đã yêu cầu hủy. Tác vụ native sẽ giải phóng tài nguyên khi kết thúc.');$('analyze-media').disabled=!file;});
  $('confirm-media').addEventListener('click',async()=>{const ids=Array.from($('media-students').querySelectorAll('input:checked')).map(x=>Number(x.value));if(!ids.length){status('Hãy chọn sinh viên sau khi đối chiếu ảnh/video.',true);return;}if(!confirm('Bạn đã đối chiếu và xác nhận '+ids.length+' sinh viên thực sự CÓ MẶT? Ảnh/video không tự chứng minh hiện diện.'))return;
    const id=job?.id,sessionId=window.attendanceBridge?.getSessionId();$('confirm-media').disabled=true;try{const result=await api('/api/media/jobs/'+id+'/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({student_ids:ids,confirmed:true})});if(sessionId!==window.attendanceBridge?.getSessionId()||id!==job?.id)return;window.dispatchEvent(new CustomEvent('attendance:auto',{detail:result.session}));status('Đã lưu '+result.changed+' sinh viên Có mặt từ danh sách đã duyệt.');}catch(e){status(e.message,true);$('confirm-media').disabled=false;}});
  window.addEventListener('attendance:state',async e=>{records=e.detail.records||[];const id=e.detail.sessionId;if(id!==lastSession||!window.attendanceBridge?.canStart()){lastSession=id;await cancel();reset();}else if(job?.state==='completed')review();});
  window.addEventListener('pagehide',()=>{cancel();if(url)URL.revokeObjectURL(url);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&stream)closeStream();});
})();
