(() => {
  'use strict';
  const cfg=JSON.parse(document.getElementById('management-config').textContent);
  const $=id=>document.getElementById(id), main=$('main');
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let allStudents=[],allClasses=[],detail=null,media=null,editorKind=null,editorId=null,enrollmentMode=null,enrollmentSession=null,enrollmentRunning=false;
  let noticeTimer;
  let samplesPerPosture=6;
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
      `<div class="info-strip"><span aria-hidden="true">ⓘ</span><div><strong>Bắt đầu từ hồ sơ chính xác</strong>Tạo lớp và sinh viên, sau đó đăng ký khuôn mặt qua camera. Điểm danh chỉ được ghi sau khi con người xác nhận.</div></div>
      <section class="panel"><div class="panel-heading"><h2>Chuẩn bị dữ liệu lớp học</h2><span class="badge">Quản lý dữ liệu</span></div><div class="panel-body steps"><article class="step"><div class="step-number">01 / TỔ CHỨC</div><h3>Tạo lớp học</h3><p>Quản lý mã lớp, tên lớp và danh sách sinh viên cùng tham gia.</p><a href="/classes" class="button secondary">Quản lý lớp học →</a></article><article class="step"><div class="step-number">02 / HỒ SƠ</div><h3>Thêm sinh viên</h3><p>Nhập mã, họ tên, chọn lớp và đăng ký khuôn mặt bằng camera.</p><a href="/students" class="button secondary">Quản lý sinh viên →</a></article><article class="step"><div class="step-number">03 / KHUÔN MẶT</div><h3>Thu thập tự động</h3><p>AI hướng dẫn năm tư thế và tự lấy mẫu, không cần chụp từng ảnh.</p><a href="/students" class="button secondary">Chọn sinh viên →</a></article></div></section>`;
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
    stopMedia();
    const [s,classes,legacy]=await Promise.all([api(`/api/students/${identity}`),api('/api/classes'),api('/api/legacy-gallery')]);
    detail=s;allClasses=classes.items;
    const available=allClasses.filter(c=>!s.classes.some(member=>member.id===c.id));
    main.innerHTML=`<a class="back-link" href="/students">← Danh sách sinh viên</a>`+heading(s.full_name,s.student_code,`<button class="button secondary" data-action="edit" data-kind="students" data-id="${s.id}">Sửa hồ sơ</button>`)+
      `<div class="details-grid"><div><section class="panel"><div class="panel-heading"><h2>Hồ sơ sinh viên</h2>${badge(s.face_count)}</div><div class="panel-body"><dl><div class="detail-item"><dt>Mã sinh viên</dt><dd>${studentCode(s)}</dd></div><div class="detail-item"><dt>Họ và tên</dt><dd>${esc(s.full_name)}</dd></div><div class="detail-item"><dt>Ngày tạo hồ sơ</dt><dd>${esc(new Date(s.created_at).toLocaleString('vi-VN'))}</dd></div></dl></div></section>
      <section class="panel"><div class="panel-heading"><h2>Lớp tham gia <span class="count">${s.classes.length}</span></h2></div><div class="panel-body">${s.classes.length?s.classes.map(c=>`<div class="list-item"><a class="entity-name" href="/classes/${c.id}">${esc(c.class_code)}<span class="entity-sub">${esc(c.class_name)}</span></a><button class="text-button danger" data-action="remove-class" data-id="${c.id}">Gỡ</button></div>`).join(''):'<p class="muted">Chưa phân vào lớp học nào.</p>'}<form id="student-class-form" class="inline-form section-gap"><label class="field">Chọn lớp<select id="class-select" required><option value="">Chọn lớp học…</option>${available.map(c=>`<option value="${c.id}">${esc(c.class_code+' · '+c.class_name)}</option>`).join('')}</select></label><button class="button secondary" ${available.length?'':'disabled'}>Thêm</button></form></div></section></div>
      <div><section class="panel" id="face-registration"><div class="panel-heading"><h2>Đăng ký khuôn mặt bằng AI</h2><span class="badge">ArcFace · RetinaFace</span></div><div class="panel-body"><p class="muted">Camera tự thu mẫu theo năm tư thế. Khung hình chỉ được xử lý trong bộ nhớ; hệ thống chỉ lưu embedding sau khi người vận hành xác nhận danh tính.</p><button class="button secondary" id="reenroll-face" type="button">◎ ${s.face_count?'Đăng ký lại khuôn mặt':'Bắt đầu đăng ký khuôn mặt'}</button><p class="muted">Khi đăng ký lại, dữ liệu hiện tại được giữ đến khi bộ vector mới được lưu an toàn.</p></div></section>
      <section class="panel"><div class="panel-heading"><h2>Liên kết dữ liệu khuôn mặt cũ</h2></div><div class="panel-body">${s.legacy_label?`<p class="muted">Đã liên kết thủ công với <strong>${esc(s.legacy_label)}</strong>. Các vector gốc vẫn được giữ nguyên.</p>`:`<p class="muted">Chọn tên trong dataset tương ứng với hồ sơ này. Không tự gán mã sinh viên hay suy đoán từ similarity.</p><form id="mapping-form"><label class="field">Tên trong dataset<select id="legacy-select" required><option value="">Chọn tên tương ứng…</option>${legacy.items.filter(x=>!x.student_id).map(x=>`<option value="${esc(x.dataset_label)}">${esc(x.dataset_label)} · ${x.embedding_count} vector</option>`).join('')}</select></label><label class="check-label"><input id="mapping-confirm" type="checkbox" required>Tôi đã đối chiếu và xác nhận đúng sinh viên.</label><button class="button secondary">Liên kết dữ liệu cũ</button></form>`}</div></section>
      <section class="panel"><div class="panel-heading"><h2>Dữ liệu đã đăng ký <span class="count">${s.faces.length}</span></h2></div><div class="panel-body">${s.faces.length?s.faces.map(f=>`<div class="list-item"><span class="muted">${f.embedding_reference?'Liên kết vector gốc':'Ảnh đăng ký mới'} #${f.id}</span><span class="badge">${esc(new Date(f.created_at).toLocaleDateString('vi-VN'))}</span></div>`).join(''):'<p class="muted">Chưa có dữ liệu khuôn mặt cho hồ sơ này.</p>'}</div></section></div></div>`;
    $('student-class-form').addEventListener('submit',e=>mutateForm(e,async()=>{await send(`/api/classes/${$('class-select').value}/students`,'POST',{student_id:identity});await studentDetail();notice('Đã phân sinh viên vào lớp.');}));
    $('mapping-form')?.addEventListener('submit',e=>mutateForm(e,async()=>{const result=await send(`/api/students/${identity}/legacy-mapping`,'POST',{dataset_label:$('legacy-select').value,confirmed:$('mapping-confirm').checked});await studentDetail();notice(result.gallery_updated?'Đã liên kết và cập nhật gallery.':'Đã lưu liên kết; gallery sẽ cập nhật khi AI sẵn sàng hoặc xử lý khung tiếp theo.');}));
    $('reenroll-face').addEventListener('click',()=>openEnrollmentDialog({student_id:s.id,student_code:s.student_code,full_name:s.full_name,class_id:s.classes[0]?.id||''}));
  }
  function stopMedia(){enrollmentRunning=false;media?.getTracks().forEach(t=>t.stop());media=null;const v=$('enroll-video');if(v){v.srcObject=null;}}
  async function abandonEnrollment(){stopMedia();if(enrollmentSession)try{await send('/api/enrollment/cancel','POST',{session:enrollmentSession});}catch{}enrollmentSession=null;}
  const postureLabels=['Nhìn thẳng','Quay đầu sang trái','Quay đầu sang phải','Ngẩng nhẹ','Cúi nhẹ'];
  async function openEnrollmentDialog(existing=null){
    stopMedia();enrollmentMode=existing?'replace':'create';
    const classes=existing?{items:[]}:await api('/api/classes');
    const classSelect=existing?'':`<label class="field">Lớp học<select id="enroll-class" required><option value="">Chọn lớp…</option>${classes.items.map(c=>`<option value="${c.id}">${esc(c.class_code+' · '+c.class_name)}</option>`).join('')}</select></label>`;
    $('editor-title').textContent=existing?'Đăng ký lại khuôn mặt':'Thêm sinh viên bằng AI';
    $('editor-fields').innerHTML=`<label class="field">Mã sinh viên<input id="enroll-code" value="${esc(existing?.student_code||'')}" maxlength="64" autocomplete="off" ${existing?'readonly':''} required><span id="code-status" class="muted" aria-live="polite"></span></label><label class="field">Họ và tên<input id="enroll-name" value="${esc(existing?.full_name||'')}" maxlength="160" ${existing?'readonly':''} required></label>${classSelect}<p class="muted">Hãy xác nhận đúng sinh viên trước khi bắt đầu. Hệ thống không thể chứng minh khuôn mặt trước camera thuộc mã sinh viên đã nhập.</p><div class="enroll-stage"><video id="enroll-video" autoplay muted playsinline class="enrollment-media" hidden></video><div class="enroll-face-guide" aria-hidden="true"></div></div><p id="enroll-instruction" class="enroll-instruction" aria-live="polite">Nhập thông tin rồi bật camera để bắt đầu.</p><div class="enroll-progress-list">${postureLabels.map((label,i)=>`<div class="enroll-progress-row" id="pose-row-${i}"><span class="pose-mark">○</span><span>${label}</span><span class="pose-count" id="pose-count-${i}">0/6</span></div>`).join('')}</div><div class="enroll-total"><progress id="enroll-total-progress" max="30" value="0"></progress><span id="enroll-total-label">0/30 mẫu</span></div><div class="camera-controls"><button type="button" class="button secondary" id="start-enroll-camera">◎ Đăng ký khuôn mặt bằng AI</button><button type="button" class="button secondary" id="retry-enroll-pose" hidden>Thử lại tư thế</button><button type="button" class="button secondary" id="restart-enroll" hidden>Làm lại từ đầu</button><button type="button" class="button secondary" id="stop-enroll-camera" disabled>Tắt camera</button></div><div id="duplicate-warning" class="form-error" role="status"></div><label class="check-label"><input id="identity-confirmed" type="checkbox">Tôi đã đối chiếu và xác nhận đúng sinh viên trước khi lưu.</label><label class="check-label" id="similarity-ack-row" hidden><input id="similarity-ack" type="checkbox">Tôi đã xem cảnh báo trùng khuôn mặt và xác nhận tiếp tục.</label><p id="enroll-timing" class="muted" aria-live="polite"></p>`;
    const submit=$('editor-form').querySelector('[type="submit"]');submit.textContent='Hoàn tất đăng ký';submit.disabled=true;
    $('editor').classList.add('enrollment-dialog');$('editor-error').textContent='';$('editor').showModal();
    let codeVersion=0,codeValid=!!existing,cameraStarting=false;
    const ensureEnrollmentCamera=async()=>{
      if(cameraStarting||media||enrollmentRunning)return;
      if(!existing&&(!codeValid||!$('enroll-name').value.trim()||!$('enroll-class').value))return;
      cameraStarting=true;const start=$('start-enroll-camera');start.disabled=true;
      try{
        if(!enrollmentSession){const started=await send('/api/enrollment/start','POST',{});enrollmentSession=started.session;samplesPerPosture=started.samples_per_posture||6;updateEnrollmentProgress([0,0,0,0,0]);}
        if(!navigator.mediaDevices?.getUserMedia)throw new Error('Trình duyệt không hỗ trợ camera. Hãy dùng HTTPS hoặc localhost và cấp quyền camera.');
        media=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480}},audio:false});
        media.getVideoTracks().forEach(track=>track.addEventListener('ended',()=>{if(enrollmentRunning){stopMedia();start.disabled=false;$('enroll-instruction').textContent='Camera đã ngắt. Dữ liệu đã thu vẫn được giữ; hãy bật lại camera.';}}));
        const video=$('enroll-video');video.srcObject=media;video.hidden=false;await video.play();
        $('stop-enroll-camera').disabled=false;$('retry-enroll-pose').hidden=true;$('restart-enroll').hidden=false;
        enrollmentRunning=true;$('enroll-instruction').textContent='Nhìn thẳng vào camera.';pumpEnrollment();
      }catch(e){stopMedia();start.disabled=false;notice(e.name==='NotAllowedError'?'Bạn đã từ chối quyền camera. Hãy mở cài đặt quyền riêng tư của trình duyệt và cho phép camera.':e.name==='NotFoundError'?'Không tìm thấy camera. Hãy kết nối camera rồi thử lại.':e.name==='NotReadableError'?'Camera đang được ứng dụng khác sử dụng. Hãy đóng ứng dụng đó rồi thử lại.':e.name==='SecurityError'?'Trình duyệt chặn camera. Hãy mở ứng dụng bằng localhost hoặc HTTPS và cấp quyền camera.':e.message,true);}
      finally{cameraStarting=false;}
    };
    const evaluateEnrollment=()=>{clearTimeout(window.enrollCodeTimer);window.enrollCodeTimer=setTimeout(async()=>{await validate();if(codeValid)await ensureEnrollmentCamera();},350);};
    const validate=async()=>{if(existing)return;const input=$('enroll-code'),status=$('code-status'),version=++codeVersion;codeValid=false;$('start-enroll-camera').disabled=true;submit.disabled=true;if(!input.value.trim()){status.textContent='Nhập mã sinh viên.';return;}try{const result=await send('/api/students/check-code','POST',{student_code:input.value.trim()});if(version!==codeVersion)return;codeValid=!result.exists;status.textContent=result.exists?'Mã sinh viên đã tồn tại.':'Mã sinh viên có thể sử dụng.';status.classList.toggle('form-error',result.exists);if(result.exists)$('start-enroll-camera').disabled=true;}catch(e){status.textContent=e.message;codeValid=false;}};
    $('enroll-code').addEventListener('input',evaluateEnrollment);
    $('enroll-name').addEventListener('input',()=>{if(codeValid)evaluateEnrollment();});
    $('enroll-class')?.addEventListener('change',()=>{if(codeValid)evaluateEnrollment();});
    $('enroll-code').addEventListener('blur',validate);
    const updateSubmit=()=>{submit.disabled=!enrollmentSession||!$('identity-confirmed').checked||(!$('similarity-ack-row').hidden&&!$('similarity-ack').checked);};
    $('identity-confirmed').addEventListener('change',updateSubmit);
    $('similarity-ack').addEventListener('change',updateSubmit);
    $('start-enroll-camera').addEventListener('click',async()=>{if(!existing&&!codeValid)await validate();await ensureEnrollmentCamera();});
    $('stop-enroll-camera').addEventListener('click',()=>{stopMedia();$('start-enroll-camera').disabled=false;$('enroll-instruction').textContent='Camera đã tạm dừng. Bật lại để tiếp tục với dữ liệu đã thu.';});
    $('retry-enroll-pose').addEventListener('click',async()=>{try{await send('/api/enrollment/retry','POST',{session:enrollmentSession});$('retry-enroll-pose').hidden=true;$('enroll-instruction').textContent='Thử lại tư thế đang hướng dẫn.';enrollmentRunning=true;pumpEnrollment();}catch(e){notice(e.message,true);}});
    $('restart-enroll').addEventListener('click',async()=>{try{enrollmentRunning=false;await send('/api/enrollment/restart','POST',{session:enrollmentSession});updateEnrollmentProgress([0,0,0,0,0]);$('duplicate-warning').textContent='';$('similarity-ack-row').hidden=true;$('similarity-ack').checked=false;$('identity-confirmed').checked=false;submit.disabled=true;$('enroll-instruction').textContent='Nhìn thẳng vào camera.';$('start-enroll-camera').disabled=false;$('start-enroll-camera').click();}catch(e){notice(e.message,true);}});
    if(existing){$('code-status').textContent='Đăng ký lại cho hồ sơ đã xác nhận.';$('start-enroll-camera').disabled=false;}
    if(!existing)validate();else ensureEnrollmentCamera();
  }
  function updateEnrollmentProgress(counts){let total=0;counts.forEach((count,i)=>{total+=count;$(`pose-count-${i}`).textContent=`${count}/${samplesPerPosture}`;$(`pose-row-${i}`).classList.toggle('done',count>=samplesPerPosture);$(`pose-row-${i}`).querySelector('.pose-mark').textContent=count>=samplesPerPosture?'✓':'○';});$('enroll-total-progress').max=5*samplesPerPosture;$('enroll-total-progress').value=total;$('enroll-total-label').textContent=`${total}/${5*samplesPerPosture} mẫu`;}
  async function pumpEnrollment(){
    if(!enrollmentRunning)return;const video=$('enroll-video');
    if(!video||video.readyState<2||!video.videoWidth){setTimeout(pumpEnrollment,150);return;}
    const canvas=document.createElement('canvas');canvas.width=Math.min(640,video.videoWidth);canvas.height=Math.round(video.videoHeight*canvas.width/video.videoWidth);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
    try{
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.82));if(!blob)throw new Error('Không đọc được hình ảnh camera.');
      const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,i+0x8000));
      const result=await send('/api/enrollment/frame','POST',{session:enrollmentSession,frame:btoa(binary)});
      if(result.counts)updateEnrollmentProgress(result.counts);
      if(result.timeout){enrollmentRunning=false;$('retry-enroll-pose').hidden=false;$('enroll-instruction').textContent=result.reason;return;}
      if(result.complete){enrollmentRunning=false;stopMedia();$('stop-enroll-camera').disabled=true;$('enroll-instruction').textContent='Đã thu thập đủ dữ liệu khuôn mặt. Đang kiểm tra khả năng trùng…';
        const checked=await send('/api/enrollment/duplicate-check','POST',{session:enrollmentSession,student_id:enrollmentMode==='replace'?Number(identity):undefined});
        if(checked.warnings.length){$('duplicate-warning').textContent='Cảnh báo: độ tương đồng cao với '+checked.warnings.map(x=>`${x.student} (${x.similarity})`).join(', ')+'. Hãy đối chiếu danh tính trước khi tiếp tục.';$('similarity-ack-row').hidden=false;}
        else $('duplicate-warning').textContent='Không phát hiện cảnh báo trùng theo ngưỡng kiểm tra.';
        $('enroll-instruction').textContent='Đã thu thập đủ dữ liệu khuôn mặt.';$('enroll-timing').textContent='Hãy xác nhận danh tính sinh viên rồi nhấn Hoàn tất đăng ký.';
        $('identity-confirmed').dispatchEvent(new Event('change'));return;
      }
      if(result.posture){const active=result.next_posture||result.posture;const i=['thẳng','quay_trái','quay_phải','ngẩng_nhẹ','cúi_nhẹ'].indexOf(active);$('enroll-instruction').textContent=(postureLabels[i]||'Điều chỉnh tư thế')+'. '+(result.reason||'');}
    }catch(e){$('enroll-instruction').textContent=e.message;}
    if(enrollmentRunning)setTimeout(pumpEnrollment,100);
  }
  $('editor-form').addEventListener('submit',async e=>{
    e.preventDefault();const button=e.currentTarget.querySelector('[type="submit"]');button.disabled=true;
    try{
      if(enrollmentMode){
        if(!enrollmentSession)throw new Error('Hãy hoàn tất thu thập qua camera trước khi lưu.');
        const payload={session:enrollmentSession,student_id:enrollmentMode==='replace'?Number(identity):undefined,
          student_code:$('enroll-code').value.trim(),full_name:$('enroll-name').value.trim(),class_id:enrollmentMode==='create'?Number($('enroll-class').value):undefined,
          identity_confirmed:$('identity-confirmed').checked,gallery_warning_acknowledged:$('similarity-ack')?.checked||false};
        const wasReplace=enrollmentMode==='replace';
        const result=await send('/api/enrollment/finish','POST',payload);await abandonEnrollment();$('editor').close();$('editor').classList.remove('enrollment-dialog');enrollmentMode=null;
        if(wasReplace)await studentDetail();else window.location.assign(`/students/${result.student_id}`);
        notice(`${result.message} ${result.embedding_count} embedding; đăng ký ${Math.round(result.registration_ms)} ms, tạo embedding ${Math.round(result.embedding_ms)} ms, lưu và nạp gallery ${Math.round(result.persistence_ms)} ms.`);
      }else{
        await send(`/api/${editorKind}${editorId?'/'+editorId:''}`,editorId?'PUT':'POST',Object.fromEntries(new FormData(e.currentTarget)));$('editor').close();await render();notice('Đã lưu thông tin.');
      }
    }catch(error){$('editor-error').textContent=error.message;if(enrollmentMode&&$('enroll-timing'))$('enroll-timing').textContent='Chưa lưu. '+error.message;button.disabled=false;}
  });
  $('close-editor').addEventListener('click',async()=>{await abandonEnrollment();enrollmentMode=null;$('editor').classList.remove('enrollment-dialog');$('editor').close();});
  $('cancel-editor').addEventListener('click',async()=>{await abandonEnrollment();enrollmentMode=null;$('editor').classList.remove('enrollment-dialog');$('editor').close();});
  $('editor').addEventListener('cancel',e=>{e.preventDefault();abandonEnrollment().finally(()=>{$('editor').classList.remove('enrollment-dialog');$('editor').close();enrollmentMode=null;});});
  async function mutateForm(event,task){event.preventDefault();const buttons=[...event.currentTarget.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{await task();}catch(e){notice(e.message,true);}finally{buttons.forEach(b=>b.disabled=false);}}
  async function openEditor(kind,id=null){
    if(kind==='students'&&!id){await openEnrollmentDialog();return;}
    enrollmentMode=null;
    editorKind=kind;editorId=id;const s=id?await api(`/api/${kind}/${id}`):{};const student=kind==='students';
    $('editor-title').textContent=(id?'Sửa ':'Thêm ')+(student?'sinh viên':'lớp học');
    $('editor-fields').innerHTML=`<label class="field">${student?'Mã sinh viên':'Mã lớp'}<input name="${student?'student_code':'class_code'}" value="${esc(s.student_code||s.class_code||'')}" maxlength="64" required autocomplete="off"></label><label class="field">${student?'Họ và tên':'Tên lớp học'}<input name="${student?'full_name':'class_name'}" value="${esc(s.full_name||s.class_name||'')}" maxlength="160" required></label>${student?'':`<label class="field">Mô tả<textarea name="description" maxlength="1000">${esc(s.description||'')}</textarea></label>`}`;
    if(student && s.student_code_is_temporary) $('editor-fields').insertAdjacentHTML('beforeend','<p class="muted">Mã hiện tại là mã tạm phục vụ demo. Nhập mã chính thức khi có thông tin; liên kết khuôn mặt vẫn được giữ.</p>');
    $('editor-error').textContent='';$('editor').showModal();
  }
  main.addEventListener('click',async e=>{
    const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action,id=Number(b.dataset.id),kind=b.dataset.kind;
    if(b.disabled)return;b.disabled=true;
    try{
      if(action==='new'||action==='edit'){if(kind==='students'&&action==='new')await openEnrollmentDialog();else await openEditor(kind,action==='edit'?id:null);return;}
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
  window.addEventListener('pagehide',()=>{stopMedia();if(enrollmentSession)fetch('/api/enrollment/cancel',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json','X-Camera-Token':cfg.csrfToken},body:JSON.stringify({session:enrollmentSession})}).catch(()=>{});});
  render().catch(e=>{main.innerHTML=empty('Không tải được dữ liệu',e.message,'<button class="button secondary" id="retry-load">Thử lại</button>');$('retry-load').addEventListener('click',()=>location.reload());});
})();
