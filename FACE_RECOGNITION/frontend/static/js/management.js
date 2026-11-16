(() => {
  'use strict';
  const cfg=JSON.parse(document.getElementById('management-config').textContent);
  const $=id=>document.getElementById(id), main=$('main');
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let allStudents=[],allClasses=[],detail=null,media=null,pendingImage=null,previewUrl=null,editorKind=null,editorId=null;
  let noticeTimer;
  let mediaGeneration=0;
  const path=location.pathname.split('/').filter(Boolean),section=path[0]||'overview',identity=path[1]?Number(path[1]):null;
  const titles={overview:'Tổng quan',classes:'Quản lý lớp học',students:'Quản lý sinh viên'};
  $('breadcrumb').textContent=titles[section]||'Quản lý';
  document.querySelector(`[data-nav="${section}"]`)?.classList.add('active');
  function notice(message,error=false){const n=$('notification');n.textContent=message;n.className=error?'error':'';n.hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>n.hidden=true,error?15000:7000);}
  async function api(url,options={}){
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),180000);
    try{
      const response=await fetch(url,{...options,headers:{'X-Camera-Token':cfg.csrfToken,...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers},signal:controller.signal});
      let data;try{data=await response.json();}catch{throw new Error('Backend trả kết quả không hợp lệ. Hãy kiểm tra kết nối.');}
      if(!response.ok)throw new Error(data.error?.message||'Yêu cầu thất bại.');
      return data;
    }catch(e){if(e.name==='AbortError')throw new Error('Hết thời gian chờ. Hãy tải lại hồ sơ để kiểm tra đã lưu chưa trước khi gửi lại.');if(e instanceof TypeError)throw new Error('Mất kết nối backend. Kiểm tra Flask tại cổng 5055.');throw e;}finally{clearTimeout(timeout);}
  }
  const send=(url,method,body)=>api(url,{method,body:body===undefined?undefined:JSON.stringify(body)});
  const empty=(title,message,action='')=>`<div class="empty"><div class="empty-symbol" aria-hidden="true">▤</div><h3>${esc(title)}</h3><p>${esc(message)}</p>${action}</div>`;
  const badge=n=>`<span class="badge ${n?'success':''}">${n?`Đã đăng ký · ${n} vector`:'Chưa đăng ký'}</span>`;
  const studentCode=s=>`${esc(s.student_code)}${s.student_code_is_temporary?' <span class="badge">Mã tạm · demo</span>':''}`;
  const heading=(title,subtitle,action='')=>`<div class="page-heading"><div><p class="eyebrow">QUẢN LÝ ĐÀO TẠO</p><h1>${esc(title)}</h1><p class="subtitle">${esc(subtitle)}</p></div>${action}</div>`;
  const addButton=kind=>`<button class="button primary" data-action="new" data-kind="${kind}">＋ Thêm ${kind==='classes'?'lớp học':'sinh viên'}</button>`;
  function studentRows(students,removable=false){return students.map(s=>`<tr><td class="code">${studentCode(s)}</td><td><a href="/students/${s.id}" class="name-cell"><span class="avatar">${esc(s.full_name.trim().split(/\s+/).map(x=>x[0]).slice(-2).join(''))}</span><span class="entity-name">${esc(s.full_name)}</span></a></td><td>${badge(s.face_count)}</td><td>${esc(s.classes.map(c=>c.class_code).join(', ')||'Chưa phân lớp')}</td><td><div class="actions">${removable?`<button class="text-button danger" data-action="remove-member" data-id="${s.id}">Gỡ khỏi lớp</button>`:`<a class="text-button" href="/students/${s.id}">Chi tiết</a><button class="text-button" data-action="edit" data-kind="students" data-id="${s.id}">Sửa</button><button class="text-button danger" data-action="delete" data-kind="students" data-id="${s.id}">Xóa</button>`}</div></td></tr>`).join('');}
  function studentTable(rows,remove=false){return `<div class="table-scroll"><table><thead><tr><th>MÃ SINH VIÊN</th><th>HỌ VÀ TÊN</th><th>KHUÔN MẶT</th><th>LỚP THAM GIA</th><th></th></tr></thead><tbody>${studentRows(rows,remove)}</tbody></table></div>`;}
  async function overview(){
    const data=await api('/api/overview');
    main.innerHTML=heading('Tổng quan','Một nơi để quản lý lớp học, hồ sơ sinh viên và đăng ký khuôn mặt.')+
      `<div class="metrics">${[[data.classes,'Lớp học','Lớp đang được quản lý','▤'],[data.students,'Sinh viên','Hồ sơ đang hoạt động','♙'],[data.registered_students,'Đã đăng ký khuôn mặt','Sinh viên có dữ liệu khuôn mặt','◎']].map(([n,label,caption,icon])=>`<article class="metric"><div class="metric-icon" aria-hidden="true">${icon}</div><div class="metric-label">${label}</div><div class="metric-value">${n}</div><div class="metric-caption">${caption}</div></article>`).join('')}</div>`+
      `<div class="info-strip"><span aria-hidden="true">ⓘ</span><div><strong>Bắt đầu từ hồ sơ chính xác</strong>Tạo lớp và sinh viên, sau đó đăng ký ảnh hoặc liên kết dữ liệu khuôn mặt đã có. Điểm danh chỉ được ghi sau khi con người xác nhận.</div></div>
      <section class="panel"><div class="panel-heading"><h2>Chuẩn bị dữ liệu lớp học</h2><span class="badge">Quản lý dữ liệu</span></div><div class="panel-body steps"><article class="step"><div class="step-number">01 / TỔ CHỨC</div><h3>Tạo lớp học</h3><p>Quản lý mã lớp, tên lớp và danh sách sinh viên cùng tham gia.</p><a href="/classes" class="button secondary">Quản lý lớp học →</a></article><article class="step"><div class="step-number">02 / HỒ SƠ</div><h3>Thêm sinh viên</h3><p>Nhập mã sinh viên thật, họ tên và phân vào một hoặc nhiều lớp.</p><a href="/students" class="button secondary">Quản lý sinh viên →</a></article><article class="step"><div class="step-number">03 / KHUÔN MẶT</div><h3>Đăng ký khuôn mặt</h3><p>Mở hồ sơ để tải ảnh, chụp camera hoặc liên kết tên dataset thủ công.</p><a href="/students" class="button secondary">Chọn sinh viên →</a></article></div></section>`;
  }
  async function collection(query=''){
    const kind=section,isStudent=kind==='students';
    const {items}=await api(`/api/${kind}?q=${encodeURIComponent(query)}`);
    if(isStudent)allStudents=items;else allClasses=items;
    main.innerHTML=heading(titles[kind],isStudent?'Quản lý hồ sơ, phân lớp và dữ liệu khuôn mặt của sinh viên.':'Tổ chức lớp học và quản lý sinh viên trong từng lớp.',addButton(kind))+
      `<section class="panel"><div class="panel-heading"><h2>Danh sách ${isStudent?'sinh viên':'lớp học'} <span class="count">${items.length}</span></h2><form id="search-form" class="search-form"><input id="search-query" aria-label="Tìm kiếm" placeholder="Tìm theo mã hoặc tên…" value="${esc(query)}" maxlength="160"><button class="button secondary">Tìm kiếm</button></form></div>`+
      (items.length?(isStudent?studentTable(items):`<div class="table-scroll"><table><thead><tr><th>MÃ LỚP</th><th>TÊN LỚP HỌC</th><th>SINH VIÊN</th><th></th></tr></thead><tbody>${items.map(c=>`<tr><td class="code">${esc(c.class_code)}</td><td><a class="entity-name" href="/classes/${c.id}">${esc(c.class_name)}</a><span class="entity-sub">${esc(c.description||'Chưa có mô tả')}</span></td><td><span class="badge">${c.student_count} sinh viên</span></td><td><div class="actions"><a class="text-button" href="/classes/${c.id}">Chi tiết</a><button class="text-button" data-action="edit" data-kind="classes" data-id="${c.id}">Sửa</button><button class="text-button danger" data-action="delete" data-kind="classes" data-id="${c.id}">Xóa</button></div></td></tr>`).join('')}</tbody></table></div>`):empty(query?'Không tìm thấy kết quả':`Chưa có ${isStudent?'sinh viên':'lớp học'}`,query?'Thử tìm với mã hoặc tên khác.':'Thêm hồ sơ đầu tiên để bắt đầu quản lý.',query?'':addButton(kind)))+'</section>';
    $('search-form').addEventListener('submit',e=>{e.preventDefault();collection($('search-query').value).catch(showError);});
  }
  async function classDetail(){
    const [c,students]=await Promise.all([api(`/api/classes/${identity}`),api('/api/students')]);detail=c;allStudents=students.items;
    const available=allStudents.filter(s=>!c.students.some(member=>member.id===s.id));
    main.innerHTML=`<a class="back-link" href="/classes">← Danh sách lớp học</a>`+heading(c.class_name,c.class_code,`<button class="button secondary" data-action="edit" data-kind="classes" data-id="${c.id}">Sửa thông tin</button>`)+
      `<section class="panel"><div class="panel-body"><h3>Mô tả lớp học</h3><p class="muted">${esc(c.description||'Chưa có mô tả.')}</p></div></section><section class="panel"><div class="panel-heading"><h2>Sinh viên của lớp <span class="count">${c.students.length}</span></h2></div><div class="panel-body"><form id="member-form" class="inline-form"><label class="field">Thêm sinh viên có sẵn<select id="member-select" required><option value="">Chọn sinh viên…</option>${available.map(s=>`<option value="${s.id}">${esc(s.student_code+' · '+s.full_name)}</option>`).join('')}</select></label><button class="button primary" ${available.length?'':'disabled'}>＋ Thêm vào lớp</button></form>${available.length?'':'<p class="muted">Không có sinh viên khác để thêm. Bạn có thể tạo hồ sơ tại trang Quản lý sinh viên.</p>'}</div>`+
      (c.students.length?studentTable(c.students,true):empty('Lớp chưa có sinh viên','Chọn một hồ sơ có sẵn để thêm vào lớp.'))+'</section>';
    $('member-form').addEventListener('submit',e=>mutateForm(e,async()=>{await send(`/api/classes/${identity}/students`,'POST',{student_id:Number($('member-select').value)});await classDetail();notice('Đã thêm sinh viên vào lớp.');}));
  }
  async function studentDetail(){
    stopMedia();releasePreview();pendingImage=null;
    const [s,classes,legacy]=await Promise.all([api(`/api/students/${identity}`),api('/api/classes'),api('/api/legacy-gallery')]);
    detail=s;allClasses=classes.items;
    const available=allClasses.filter(c=>!s.classes.some(member=>member.id===c.id));
    main.innerHTML=`<a class="back-link" href="/students">← Danh sách sinh viên</a>`+heading(s.full_name,s.student_code,`<button class="button secondary" data-action="edit" data-kind="students" data-id="${s.id}">Sửa hồ sơ</button>`)+
      `<div class="details-grid"><div><section class="panel"><div class="panel-heading"><h2>Hồ sơ sinh viên</h2>${badge(s.face_count)}</div><div class="panel-body"><dl><div class="detail-item"><dt>Mã sinh viên</dt><dd>${studentCode(s)}</dd></div><div class="detail-item"><dt>Họ và tên</dt><dd>${esc(s.full_name)}</dd></div><div class="detail-item"><dt>Ngày tạo hồ sơ</dt><dd>${esc(new Date(s.created_at).toLocaleString('vi-VN'))}</dd></div></dl></div></section>
      <section class="panel"><div class="panel-heading"><h2>Lớp tham gia <span class="count">${s.classes.length}</span></h2></div><div class="panel-body">${s.classes.length?s.classes.map(c=>`<div class="list-item"><a class="entity-name" href="/classes/${c.id}">${esc(c.class_code)}<span class="entity-sub">${esc(c.class_name)}</span></a><button class="text-button danger" data-action="remove-class" data-id="${c.id}">Gỡ</button></div>`).join(''):'<p class="muted">Chưa phân vào lớp học nào.</p>'}<form id="student-class-form" class="inline-form section-gap"><label class="field">Chọn lớp<select id="class-select" required><option value="">Chọn lớp học…</option>${available.map(c=>`<option value="${c.id}">${esc(c.class_code+' · '+c.class_name)}</option>`).join('')}</select></label><button class="button secondary" ${available.length?'':'disabled'}>Thêm</button></form></div></section></div>
      <div><section class="panel" id="face-registration"><div class="panel-heading"><h2>Đăng ký khuôn mặt</h2><span class="badge">ArcFace · RetinaFace</span></div><div class="panel-body"><p class="muted">Ảnh rõ mặt, chỉ có một người. JPEG, PNG hoặc WebP, tối đa 4 MB và 4096 px mỗi cạnh. Ảnh chỉ dùng để xử lý, không được lưu.</p>
      <form id="face-form"><div class="upload-zone"><label class="field">Tải ảnh từ máy<input id="face-file" type="file" accept="image/jpeg,image/png,image/webp"></label><div class="camera-controls"><button type="button" class="button secondary" id="open-enroll-camera">◎ Bật camera</button><button type="button" class="button secondary" id="capture-enroll" disabled>Chụp ảnh</button><button type="button" class="button secondary" id="stop-enroll-camera" disabled>Tắt camera</button></div><video id="enroll-video" autoplay muted playsinline class="enrollment-media" hidden></video><img id="enroll-preview" class="enrollment-media" alt="Ảnh được chọn để đăng ký" hidden></div>
      <label class="check-label"><input type="checkbox" id="face-confirm" required>Tôi xác nhận ảnh thuộc ${esc(s.student_code+' · '+s.full_name)}. AI chỉ kiểm tra và trích xuất khuôn mặt, không tự xác minh chủ thể.</label><button class="button primary" type="submit">Lưu đăng ký khuôn mặt</button><p class="muted" id="enrollment-progress" aria-live="polite">Hãy tắt camera nhận diện ở các tab khác trước khi đăng ký.</p></form></div></section>
      <section class="panel"><div class="panel-heading"><h2>Liên kết dữ liệu khuôn mặt cũ</h2></div><div class="panel-body">${s.legacy_label?`<p class="muted">Đã liên kết thủ công với <strong>${esc(s.legacy_label)}</strong>. Các vector gốc vẫn được giữ nguyên.</p>`:`<p class="muted">Chọn tên trong dataset tương ứng với hồ sơ này. Không tự gán mã sinh viên hay suy đoán từ similarity.</p><form id="mapping-form"><label class="field">Tên trong dataset<select id="legacy-select" required><option value="">Chọn tên tương ứng…</option>${legacy.items.filter(x=>!x.student_id).map(x=>`<option value="${esc(x.dataset_label)}">${esc(x.dataset_label)} · ${x.embedding_count} vector</option>`).join('')}</select></label><label class="check-label"><input id="mapping-confirm" type="checkbox" required>Tôi đã đối chiếu và xác nhận đúng sinh viên.</label><button class="button secondary">Liên kết dữ liệu cũ</button></form>`}</div></section>
      <section class="panel"><div class="panel-heading"><h2>Dữ liệu đã đăng ký <span class="count">${s.faces.length}</span></h2></div><div class="panel-body">${s.faces.length?s.faces.map(f=>`<div class="list-item"><span class="muted">${f.embedding_reference?'Liên kết vector gốc':'Ảnh đăng ký mới'} #${f.id}</span><span class="badge">${esc(new Date(f.created_at).toLocaleDateString('vi-VN'))}</span></div>`).join(''):'<p class="muted">Chưa có dữ liệu khuôn mặt cho hồ sơ này.</p>'}</div></section></div></div>`;
    $('student-class-form').addEventListener('submit',e=>mutateForm(e,async()=>{await send(`/api/classes/${$('class-select').value}/students`,'POST',{student_id:identity});await studentDetail();notice('Đã phân sinh viên vào lớp.');}));
    $('mapping-form')?.addEventListener('submit',e=>mutateForm(e,async()=>{const result=await send(`/api/students/${identity}/legacy-mapping`,'POST',{dataset_label:$('legacy-select').value,confirmed:$('mapping-confirm').checked});await studentDetail();notice(result.gallery_updated?'Đã liên kết và cập nhật gallery.':'Đã lưu liên kết; gallery sẽ cập nhật khi AI sẵn sàng hoặc xử lý khung tiếp theo.');}));
    bindEnrollment();
  }
  function releasePreview(){if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null;}}
  function stopMedia(){mediaGeneration++;media?.getTracks().forEach(t=>t.stop());media=null;const v=$('enroll-video');if(v){v.srcObject=null;v.hidden=true;}$('capture-enroll')?.setAttribute('disabled','');$('stop-enroll-camera')?.setAttribute('disabled','');}
  function chooseImage(blob){pendingImage=blob;releasePreview();previewUrl=URL.createObjectURL(blob);$('enroll-preview').src=previewUrl;$('enroll-preview').hidden=false;$('face-confirm').checked=false;}
  function bindEnrollment(){
    $('face-file').addEventListener('change',()=>{const file=$('face-file').files[0];if(file){stopMedia();chooseImage(file);}else{pendingImage=null;releasePreview();$('enroll-preview').hidden=true;}});
    $('open-enroll-camera').addEventListener('click',async()=>{const button=$('open-enroll-camera');button.disabled=true;stopMedia();const generation=mediaGeneration;$('stop-enroll-camera').disabled=false;try{const acquired=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480}},audio:false});if(generation!==mediaGeneration){acquired.getTracks().forEach(t=>t.stop());return;}media=acquired;const video=$('enroll-video');video.srcObject=media;video.hidden=false;await video.play();if(generation!==mediaGeneration)return;$('capture-enroll').disabled=false;}catch(e){stopMedia();notice(e.name==='NotAllowedError'?'Quyền camera bị từ chối. Hãy cấp quyền hoặc tải ảnh lên.':'Không mở được camera. Kiểm tra thiết bị và các ứng dụng đang sử dụng.',true);}finally{button.disabled=false;}});
    $('stop-enroll-camera').addEventListener('click',stopMedia);
    $('capture-enroll').addEventListener('click',()=>{const v=$('enroll-video');if(!v.videoWidth)return;const canvas=document.createElement('canvas');canvas.width=v.videoWidth;canvas.height=v.videoHeight;canvas.getContext('2d').drawImage(v,0,0);canvas.toBlob(blob=>{if(blob){chooseImage(blob);$('face-file').value='';stopMedia();}},'image/jpeg',.95);});
    $('face-form').addEventListener('submit',e=>mutateForm(e,async()=>{
      if(!pendingImage)throw new Error('Hãy chọn ảnh hoặc chụp một ảnh trước khi lưu.');
      if(pendingImage.size>4*1024*1024)throw new Error('Ảnh vượt quá 4 MB. Hãy chọn ảnh nhỏ hơn.');
      stopMedia();const form=new FormData();form.append('image',pendingImage,'registration.jpg');form.append('confirmed',$('face-confirm').checked?'true':'false');
      $('enrollment-progress').textContent='AI đang kiểm tra khuôn mặt và tạo embedding. Có thể mất vài chục giây; không gửi lại khi đang xử lý.';
      try{const result=await api(`/api/students/${identity}/faces`,{method:'POST',body:form});await studentDetail();notice(result.message+(result.gallery_updated?'':' Gallery sẽ cập nhật khi AI sẵn sàng.'));}catch(e){$('enrollment-progress').textContent='Chưa xác nhận lưu thành công. '+e.message;throw e;}
    }));
  }
  async function mutateForm(event,task){event.preventDefault();const buttons=[...event.currentTarget.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{await task();}catch(e){notice(e.message,true);}finally{buttons.forEach(b=>b.disabled=false);}}
  async function openEditor(kind,id=null){
    editorKind=kind;editorId=id;const s=id?await api(`/api/${kind}/${id}`):{};const student=kind==='students';
    $('editor-title').textContent=(id?'Sửa ':'Thêm ')+(student?'sinh viên':'lớp học');
    $('editor-fields').innerHTML=`<label class="field">${student?'Mã sinh viên':'Mã lớp'}<input name="${student?'student_code':'class_code'}" value="${esc(s.student_code||s.class_code||'')}" maxlength="64" required autocomplete="off"></label><label class="field">${student?'Họ và tên':'Tên lớp học'}<input name="${student?'full_name':'class_name'}" value="${esc(s.full_name||s.class_name||'')}" maxlength="160" required></label>${student?'':`<label class="field">Mô tả<textarea name="description" maxlength="1000">${esc(s.description||'')}</textarea></label>`}`;
    if(student && s.student_code_is_temporary) $('editor-fields').insertAdjacentHTML('beforeend','<p class="muted">Mã hiện tại là mã tạm phục vụ demo. Nhập mã chính thức khi có thông tin; liên kết khuôn mặt vẫn được giữ.</p>');
    $('editor-error').textContent='';$('editor').showModal();
  }
  $('editor-form').addEventListener('submit',async e=>{
    e.preventDefault();const button=e.currentTarget.querySelector('[type="submit"]');button.disabled=true;
    try{await send(`/api/${editorKind}${editorId?'/'+editorId:''}`,editorId?'PUT':'POST',Object.fromEntries(new FormData(e.currentTarget)));$('editor').close();await render();notice('Đã lưu thông tin.');}catch(error){$('editor-error').textContent=error.message;}finally{button.disabled=false;}
  });
  $('close-editor').addEventListener('click',()=>$('editor').close());$('cancel-editor').addEventListener('click',()=>$('editor').close());
  main.addEventListener('click',async e=>{
    const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action,id=Number(b.dataset.id),kind=b.dataset.kind;
    if(b.disabled)return;b.disabled=true;
    try{
      if(action==='new'||action==='edit'){await openEditor(kind,action==='edit'?id:null);return;}
      if(action==='delete'){
        if(!confirm('Xóa hồ sơ này khỏi danh sách? Hồ sơ còn liên kết hoặc dữ liệu khuôn mặt sẽ được bảo vệ.'))return;
        await send(`/api/${kind}/${id}`,'DELETE');await render();notice('Đã lưu trữ hồ sơ. Dữ liệu không bị xóa vĩnh viễn.');
      }else if(action==='remove-member'||action==='remove-class'){
        if(!confirm('Gỡ sinh viên khỏi lớp? Hồ sơ sinh viên và dữ liệu khuôn mặt vẫn được giữ.'))return;
        await send(`/api/classes/${action==='remove-class'?id:identity}/students/${action==='remove-class'?identity:id}`,'DELETE');await render();notice('Đã gỡ liên kết lớp–sinh viên.');
      }
    }catch(error){notice(error.message,true);}finally{b.disabled=false;}
  });
  function showError(e){notice(e.message,true);}
  async function render(){if(section==='overview')await overview();else if(identity)await(section==='students'?studentDetail():classDetail());else await collection($('search-query')?.value||'');}
  window.addEventListener('pagehide',()=>{stopMedia();releasePreview();});
  render().catch(e=>{main.innerHTML=empty('Không tải được dữ liệu',e.message,'<button class="button secondary" id="retry-load">Thử lại</button>');$('retry-load').addEventListener('click',()=>location.reload());});
})();
