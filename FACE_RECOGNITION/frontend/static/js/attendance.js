/* Server-persisted attendance: manual confirmation or verified live-camera commits. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),cfg=JSON.parse($('attendance-config').textContent);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const mode=location.pathname.split('/')[1],pageId=Number(location.pathname.split('/')[2])||null;
  let active=null,selectedClass=null,classes=[],roster=[],key=crypto.randomUUID(),pending=false,polling=false;
  function notice(text,error=false){const n=$('attendance-notice');if(n){n.textContent=text;n.hidden=false;n.dataset.error=String(error);}}
  async function api(url,method='GET',body){
    try{const response=await fetch(url,{method,headers:{'Content-Type':'application/json','X-Camera-Token':cfg.csrfToken},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
      let data;try{data=await response.json();}catch{throw new Error('Backend trả phản hồi không hợp lệ. Kiểm tra Terminal và tải lại dữ liệu.');}if(!response.ok)throw new Error(data.error?.message||'Yêu cầu thất bại.');return data;
    }catch(e){if(e.name==='TimeoutError'||e.name==='AbortError')throw new Error('Quá thời gian chờ. Tải lại dữ liệu trước khi thử ghi lại.');if(e instanceof TypeError)throw new Error('Mất kết nối backend. Kiểm tra Flask và thử lại.');throw e;}
  }
  const label=status=>status==='PRESENT'?'Có mặt':status==='ABSENT'?'Vắng mặt':'Chưa ghi nhận';
  const badge=status=>`<span class="badge ${status==='PRESENT'?'success':status==='ABSENT'?'absent':'warning'}">${label(status)}</span>`;
  const options=()=>'<option value="">Chọn lớp học…</option>'+classes.map(c=>`<option value="${c.id}">${esc(c.class_code+' · '+c.class_name)}</option>`).join('');
  function statsHTML(s,completed){
    const percent=s.total?Math.round(s.present/s.total*100):0;
    return `<div class="session-counts"><div><span>Tổng sĩ số lớp</span><strong data-count="total">${s.total}</strong></div><div><span>Có mặt</span><strong data-count="present">${s.present}</strong></div><div><span>Chưa ghi nhận</span><strong data-count="pending">${s.pending}</strong></div>${completed?`<div><span>Vắng mặt</span><strong data-count="absent">${s.absent}</strong></div>`:''}</div><div class="attendance-progress-label"><strong>Tiến độ Có mặt: ${s.present}/${s.total}</strong><span>${percent}%</span></div><progress class="attendance-progress" max="${s.total||1}" value="${s.present}" aria-label="Tiến độ Có mặt" aria-valuetext="${s.present}/${s.total} sinh viên · ${percent}%"></progress>`;
  }
  async function safe(task){if(pending)return;pending=true;document.body.dataset.attendanceBusy='true';try{await task();}catch(e){notice(e.message,true);}finally{pending=false;document.body.dataset.attendanceBusy='false';}}
  function renderRoster(){
    const records=active?active.records:roster.map(s=>({student_id:s.id,student_code_snapshot:s.student_code,full_name_snapshot:s.full_name,attendance_status:null,face_count:s.face_count}));
    $('attendance-roster').innerHTML=records.length?records.map(r=>`<div class="attendance-roster-row" data-roster-student="${r.student_id}"><strong>${esc(r.full_name_snapshot)}</strong><span class="muted">${esc(r.student_code_snapshot)}</span><div class="row-actions">${badge(r.attendance_status)}${active?.status==='IN_PROGRESS'&&r.attendance_status!=='PRESENT'?`<button class="text-button" data-confirm-student="${r.student_id}">Xác nhận Có mặt</button>`:''}</div></div>`).join(''):'<p class="muted">Lớp chưa có sinh viên. Hãy thêm sinh viên tại Quản lý lớp học.</p>';
    $('attendance-stats').innerHTML=active?statsHTML(active.stats,active.status==='COMPLETED'):`<p class="muted">${selectedClass?'Đã chọn lớp. Bắt đầu buổi để xem sĩ số và tiến độ từ danh sách của buổi.':'Chọn lớp và bắt đầu buổi để xem tình trạng điểm danh.'}</p>`;
    $('session-status').textContent=active?(active.status==='IN_PROGRESS'?'Đang điểm danh':'Đã kết thúc'):'Chưa bắt đầu';
    $('session-info').textContent=active?`${active.session_name} · ${active.class_code_snapshot} · ${active.session_date} · #${active.id}`:'Chọn lớp và tạo buổi. Chưa có kết quả nào được ghi.';
    $('finish-session').disabled=active?.status!=='IN_PROGRESS';$('begin-session').disabled=!!active||!selectedClass||!roster.length;
    $('new-session').hidden=active?.status!=='COMPLETED';
    $('session-name').disabled=!!active;$('session-date').disabled=!!active;
    if($('start-camera'))$('start-camera').disabled=active?.status!=='IN_PROGRESS'||!window.cameraControls?.isReady();
    document.querySelectorAll('.attendance-confirm[data-student-id]').forEach(b=>{if(active?.status!=='IN_PROGRESS'){b.disabled=true;return;}if(active.records.some(r=>r.student_id===Number(b.dataset.studentId)&&r.attendance_status==='PRESENT')){b.textContent='Đã điểm danh';b.disabled=true;}});
    window.dispatchEvent(new CustomEvent('attendance:state',{detail:{sessionId:active?.id??null,records:active?.records??[]}}));
    filterRoster();
  }
  function filterRoster(){const value=$('roster-filter')?.value||'all';document.querySelectorAll('[data-roster-student]').forEach(row=>{const record=active?.records.find(r=>r.student_id===Number(row.dataset.rosterStudent));row.hidden=value!=='all'&&(record?.attendance_status||'PENDING')!==value;});}
  async function loadSessionChoices(){
    const select=$('existing-session');if(!select)return;
    select.replaceChildren(new Option('Tạo buổi mới / chọn buổi đã lưu',''));select.disabled=!selectedClass;
    if(!selectedClass)return;const classId=selectedClass,rows=(await api('/api/attendance/sessions?class_id='+classId)).items;
    if(classId!==selectedClass)return;
    for(const s of rows)select.add(new Option(s.session_name+' · '+s.session_date+' · '+(s.status==='IN_PROGRESS'?'Đang điểm danh':'Đã kết thúc'),s.id));
    select.options[0].disabled=!!active;
    select.value=active?String(active.id):'';
  }
  async function refreshSession(){if(!active)return;const id=active.id,wasOpen=active.status==='IN_PROGRESS';const result=await api('/api/attendance/sessions/'+id);if(active?.id!==id)return;active=result;if(wasOpen&&active.status==='COMPLETED')await window.cameraControls?.stop('Buổi đã kết thúc. Camera đã dừng.');renderRoster();}
  function notifySessionChange(id){try{localStorage.setItem('attendance:updated',JSON.stringify({id,at:Date.now()}));}catch{}}
  async function confirmStudent(sid,sessionId){
    if(active?.id!==sessionId||active.status!=='IN_PROGRESS')throw new Error('Buổi đã thay đổi hoặc kết thúc. Không ghi nhận từ kết quả cũ.');
    const r=active.records.find(r=>r.student_id===sid);if(!r)throw new Error('Sinh viên không thuộc buổi này.');
    if(!confirm(`Bạn xác nhận ${r.student_code_snapshot} · ${r.full_name_snapshot} CÓ MẶT trong buổi ${active.session_name}? Đây là xác nhận thủ công.`))return;
    const result=await api(`/api/attendance/sessions/${sessionId}/confirm`,'POST',{student_id:sid,confirmed:true,recognition_source:'MANUAL'});
    if(active?.id===sessionId){active=result.session;renderRoster();notice(result.changed?'Đã lưu Có mặt · xác nhận bởi con người.':'Sinh viên đã được điểm danh; không tạo bản ghi trùng.');}
  }
  async function cameraPage(){
    const sessionLabel=document.createElement('label');sessionLabel.className='field existing-session-field';sessionLabel.textContent='Buổi đã lưu';const sessionSelect=document.createElement('select');sessionSelect.id='existing-session';sessionLabel.append(sessionSelect);$('session-start').after(sessionLabel);
    sessionSelect.addEventListener('change',()=>{const id=Number(sessionSelect.value);if(id)safe(async()=>{await window.cameraControls?.stop();location.assign('/attendance?session='+id);});});
    const filter=document.createElement('select');filter.id='roster-filter';filter.setAttribute('aria-label','Lọc trạng thái điểm danh');
    for(const [value,text] of [['all','Tất cả'],['PRESENT','Có mặt'],['PENDING','Chưa ghi nhận'],['ABSENT','Vắng mặt']])filter.add(new Option(text,value));
    filter.addEventListener('change',filterRoster);document.querySelector('.attendance-roster-card .card-heading').append(filter);
    classes=(await api('/api/classes')).items;$('attendance-class').innerHTML=options();
    $('session-date').value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    window.attendanceBridge={canStart:()=>active?.status==='IN_PROGRESS',getSessionId:()=>active?.status==='IN_PROGRESS'?active.id:null};
    $('start-camera').disabled=true;
    window.addEventListener('attendance:auto',e=>{if(active?.id===e.detail.id){active=e.detail;renderRoster();notifySessionChange(active.id);notice('Đã cập nhật Có mặt từ kết quả điểm danh tự động đã lưu trong SQLite.');}});
    $('attendance-class').addEventListener('change',()=>safe(async()=>{
      const chosen=Number($('attendance-class').value)||null;
      if(active?.status==='IN_PROGRESS'&&!confirm('Đổi lớp sẽ tắt camera. Buổi đang diễn ra vẫn được lưu trong Lịch sử để tiếp tục. Đổi lớp?')){$('attendance-class').value=String(selectedClass);return;}
      active=null;selectedClass=chosen;roster=[];renderRoster();$('attendance-roster').textContent='Đang tải danh sách lớp…';await window.cameraControls?.stop();key=crypto.randomUUID();history.replaceState({},'',location.pathname);
      roster=chosen?(await api('/api/classes/'+chosen)).students:[];renderRoster();await loadSessionChoices();notice(roster.length?'Danh sách lớp đã tải. Nhập tên buổi mới hoặc chọn buổi đã lưu.':'Chưa có sinh viên hoặc chưa chọn lớp.');
    }));
    $('session-start').addEventListener('submit',e=>{e.preventDefault();safe(async()=>{
      if(!selectedClass)throw new Error('Hãy chọn lớp trước.');
      active=await api('/api/attendance/sessions','POST',{class_id:selectedClass,session_name:$('session-name').value,session_date:$('session-date').value,request_key:key});
      history.replaceState({},'',`/attendance?session=${active.id}`);renderRoster();await loadSessionChoices();notice('Đã tạo buổi và chụp danh sách sinh viên. Bật camera hoặc xác nhận thủ công.');
    });});
    $('finish-session').addEventListener('click',()=>safe(async()=>{
      if(!active||!confirm('Kết thúc buổi? Sinh viên chưa xác nhận sẽ được ghi Vắng mặt.'))return;
      const id=active.id;await window.cameraControls?.stop();active=await api(`/api/attendance/sessions/${id}/finish`,'POST',{});renderRoster();await loadSessionChoices();notice('Đã kết thúc và lưu kết quả. Xem chi tiết trong Lịch sử điểm danh.');
    }));
    $('new-session').addEventListener('click',()=>safe(async()=>{active=null;roster=[];renderRoster();$('attendance-roster').textContent='Đang tải danh sách lớp…';await window.cameraControls?.stop();key=crypto.randomUUID();history.replaceState({},'',location.pathname);roster=selectedClass?(await api('/api/classes/'+selectedClass)).students:[];renderRoster();await loadSessionChoices();notice('Nhập tên và ngày cho buổi mới; dữ liệu cũ được giữ trong lịch sử.');}));
    $('attendance-roster').addEventListener('click',e=>{const b=e.target.closest('[data-confirm-student]');if(b&&active)safe(()=>confirmStudent(Number(b.dataset.confirmStudent),active.id));});
    window.addEventListener('camera:tracks',e=>{
      const ids=e.detail.sessionId===active?.id?e.detail.studentIds:[];
      document.querySelectorAll('[data-roster-student]').forEach(row=>row.classList.toggle('ai-candidate-fresh',ids.includes(Number(row.dataset.rosterStudent))));
    });
    window.addEventListener('camera:candidates',e=>{
      const {faces,sessionId}=e.detail;if(!active||active.status!=='IN_PROGRESS'||active.id!==sessionId)return;
      document.querySelectorAll('.candidate-item').forEach((card,index)=>{
        const f=faces[index],sid=f.top1.student?.id,r=active.records.find(r=>r.student_id===sid);const b=document.createElement('button');b.className='button secondary attendance-confirm';if(sid)b.dataset.studentId=String(sid);
        if(!r||!f.identity_confirmed||f.outside_class||f.eligible_for_confirmation===false){b.textContent=f.identity_confirmed&&f.outside_class?'Ứng viên toàn cục ngoài lớp · chọn thủ công':'Chưa đủ thông tin · chọn thủ công từ danh sách';b.disabled=true;}
        else if(r.attendance_status==='PRESENT'){b.textContent='Đã điểm danh';b.disabled=true;}
        else{b.textContent='Xác nhận Có mặt';b.onclick=()=>safe(()=>confirmStudent(sid,sessionId));}card.append(b);
      });
    });
    const id=Number(new URLSearchParams(location.search).get('session'));
    if(id){active=await api('/api/attendance/sessions/'+id);selectedClass=active.class_id;if(!classes.some(c=>c.id===selectedClass))$('attendance-class').append(new Option(active.class_code_snapshot+' · '+active.class_name_snapshot+' · lịch sử',String(selectedClass)));$('attendance-class').value=String(selectedClass);$('session-name').value=active.session_name;$('session-date').value=active.session_date;}
    window.addEventListener('storage',e=>{if(e.key==='attendance:updated'&&active)refreshSession().catch(err=>notice(err.message,true));});
    renderRoster();await loadSessionChoices();setInterval(async()=>{if(!active||polling||pending)return;polling=true;try{await refreshSession();}catch(e){notice(e.message,true);}finally{polling=false;}},5000);
  }
  function metrics(items){return '<div class="attendance-metrics">'+items.map(([value,name])=>`<div><strong>${esc(value)}</strong><span>${esc(name)}</span></div>`).join('')+'</div>';}
  function historyTable(rows){return rows.length?`<div class="table-scroll"><table><thead><tr><th>BUỔI / LỚP</th><th>NGÀY</th><th>TRẠNG THÁI</th><th>CÓ MẶT / VẮNG</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr><td><strong>${esc(r.session_name)}</strong><div class="muted">${esc(r.class_code_snapshot+' · '+r.class_name_snapshot)}</div></td><td>${esc(r.session_date)}</td><td>${r.status==='COMPLETED'?'Đã kết thúc':'Đang điểm danh'}</td><td>${r.present} / ${r.absent}</td><td><a href="/history/${r.id}">Chi tiết</a>${r.status==='IN_PROGRESS'?` · <a href="/attendance?session=${r.id}">Tiếp tục</a>`:''}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Chưa có buổi điểm danh phù hợp.</div>';}
  function filters(withDate=true){return `<form id="attendance-filter" class="filter-bar"><label class="field">Lớp học<select id="filter-class"><option value="">Tất cả lớp</option>${classes.map(c=>`<option value="${c.id}">${esc(c.class_code+' · '+c.class_name)}</option>`).join('')}</select></label>${withDate?'<label class="field">Ngày học<input id="filter-date" type="date"></label>':''}<button class="button primary">Lọc dữ liệu</button></form>`;}
  async function historyPage(){
    const main=$('attendance-main');
    if(pageId){
      const s=await api('/api/attendance/sessions/'+pageId);main.innerHTML=`<a href="/history" class="back-link">← Lịch sử điểm danh</a><div class="history-heading"><div><h1>${esc(s.session_name)}</h1><p class="muted">${esc(s.class_code_snapshot+' · '+s.class_name_snapshot+' · '+s.session_date)}</p></div>${s.status==='IN_PROGRESS'?`<a class="button primary" href="/attendance?session=${s.id}">Tiếp tục buổi điểm danh</a>`:'<span class="badge">Đã kết thúc</span>'}</div>`+metrics([[s.stats.total,'Danh sách snapshot'],[s.stats.present,'Có mặt'],[s.stats.absent,'Vắng mặt'],[s.stats.pending,'Chưa ghi nhận']])+`<section class="panel"><div class="panel-heading"><h2>Kết quả của buổi #${s.id}</h2></div><div class="table-scroll"><table><thead><tr><th>MÃ / HỌ TÊN TẠI LÚC BẮT ĐẦU</th><th>TRẠNG THÁI</th><th>NGUỒN</th><th>CHỈNH SỬA</th></tr></thead><tbody>${s.records.map(r=>`<tr><td>${esc(r.student_code_snapshot)}<div><strong>${esc(r.full_name_snapshot)}</strong></div></td><td>${badge(r.attendance_status)}</td><td>${r.recognition_source==='AI_VERIFIED'?'Tự động / AI':r.recognition_source==='MANUAL'?'Con người / MANUAL':'Chưa xác nhận'}</td><td>${s.status==='COMPLETED'?`<button class="text-button" data-edit-record="${r.student_id}" data-status="${r.attendance_status}">Sửa kết quả</button>`:'Chỉ sửa sau khi kết thúc'}</td></tr>`).join('')}</tbody></table></div></section><section class="panel"><div class="panel-heading"><h2>Nhật ký chỉnh sửa</h2></div><div class="panel-body">${s.edits.length?s.edits.map(e=>`<p class="muted">#${e.record_id}: ${label(e.old_status)} → ${label(e.new_status)} · ${esc(e.reason)} · ${esc(e.created_at)}</p>`).join(''):'Chưa có chỉnh sửa.'}</div></section>`;
      main.querySelectorAll('[data-edit-record]').forEach(b=>b.onclick=()=>{
        const dialog=document.createElement('dialog');dialog.innerHTML=`<form><h2>Sửa kết quả thủ công</h2><p class="muted">Chỉ tác động buổi #${s.id}. Lý do được lưu trong nhật ký.</p><label class="field">Trạng thái<select name="status"><option value="PRESENT">Có mặt</option><option value="ABSENT">Vắng mặt</option></select></label><label class="field">Lý do sửa<textarea name="reason" required maxlength="500"></textarea></label><p role="alert" class="form-error"></p><div class="form-actions"><button type="button" class="button secondary">Hủy</button><button class="button primary">Lưu chỉnh sửa</button></div></form>`;document.body.append(dialog);dialog.querySelector('select').value=b.dataset.status;dialog.querySelector('[type=button]').onclick=()=>{dialog.close();dialog.remove();};
        dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();const button=dialog.querySelector('.primary');button.disabled=true;try{await api(`/api/attendance/sessions/${s.id}/records/${b.dataset.editRecord}`,'PUT',{attendance_status:dialog.querySelector('select').value,reason:dialog.querySelector('textarea').value,confirmed:true});notifySessionChange(s.id);dialog.close();dialog.remove();await historyPage();notice('Đã lưu chỉnh sửa thủ công.');}catch(err){dialog.querySelector('.form-error').textContent=err.message;}finally{button.disabled=false;}};dialog.showModal();
      });return;
    }
    main.innerHTML='<h1>Lịch sử điểm danh</h1><p class="muted">Danh sách và kết quả được lưu bền vững trong SQLite.</p>'+filters()+'<section class="panel" id="history-results"></section>';
    async function load(){const q=new URLSearchParams();if($('filter-class').value)q.set('class_id',$('filter-class').value);if($('filter-date').value)q.set('date',$('filter-date').value);$('history-results').innerHTML=historyTable((await api('/api/attendance/sessions?'+q)).items);}
    $('attendance-filter').onsubmit=e=>{e.preventDefault();safe(load);};await load();
  }
  async function reportsPage(){
    const main=$('attendance-main');main.innerHTML='<h1>Báo cáo chuyên cần</h1><p class="muted">Chỉ tính các buổi đã kết thúc. Danh sách sinh viên dùng snapshot của từng buổi.</p>'+filters(false)+'<div id="report-results"></div>';
    async function load(){const q=$('filter-class').value?'?class_id='+$('filter-class').value:'';const d=await api('/api/attendance/reports'+q);
      $('report-results').innerHTML=metrics([[d.classes,'Tổng lớp hoạt động'],[d.students,'Tổng sinh viên'],[d.completed_sessions,'Buổi đã kết thúc'],[d.present+' / '+d.absent,'Lượt Có mặt / Vắng mặt']])+`<section class="panel"><div class="panel-heading"><h2>Chuyên cần theo sinh viên</h2><a class="button secondary" href="/api/attendance/reports.csv${q}">Xuất CSV</a></div>${d.by_student.length?`<div class="table-scroll"><table><thead><tr><th>SINH VIÊN (SNAPSHOT)</th><th>SỐ BUỔI</th><th>CÓ MẶT</th><th>VẮNG MẶT</th><th>TỶ LỆ</th></tr></thead><tbody>${d.by_student.map(r=>`<tr><td>${esc(r.student_code)} · ${esc(r.full_name)}</td><td>${r.total}</td><td>${r.present}</td><td>${r.absent}</td><td>${(r.attendance_rate*100).toFixed(1)}%</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Chưa có buổi đã kết thúc để tính tỷ lệ chuyên cần.</div>'}</section><section class="panel"><div class="panel-heading"><h2>Chuyên cần theo lớp</h2></div>${d.by_class.length?`<div class="table-scroll"><table><thead><tr><th>LỚP</th><th>LƯỢT CÓ MẶT / TỔNG</th><th>TỶ LỆ</th></tr></thead><tbody>${d.by_class.map(r=>`<tr><td>${esc(r.class_code+' · '+r.class_name)}</td><td>${r.present} / ${r.total}</td><td>${(r.attendance_rate*100).toFixed(1)}%</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Chưa có dữ liệu chuyên cần.</div>'}</section>`;
    }
    $('attendance-filter').onsubmit=e=>{e.preventDefault();safe(load);};await load();
  }
  (async()=>{if(mode==='attendance')await cameraPage();else{classes=(await api('/api/classes')).items;for(const s of (await api('/api/attendance/sessions')).items){if(!classes.some(c=>c.id===s.class_id))classes.push({id:s.class_id,class_code:s.class_code_snapshot,class_name:s.class_name_snapshot+' · lịch sử'});}if(mode==='history')await historyPage();else await reportsPage();}})().catch(e=>{notice(e.message,true);if($('attendance-main'))$('attendance-main').innerHTML='<h1>Không tải được dữ liệu</h1><p class="muted">'+esc(e.message)+'</p><a href="'+esc(location.pathname)+'" class="button secondary">Thử lại</a>';});
})();
