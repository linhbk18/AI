/* Camera-only AI face enrollment ("Thêm sinh viên" / "Đăng ký lại khuôn mặt").
 * No upload, no capture button: frames go to the backend at a bounded rate
 * (one request in flight), the AI samples automatically. Frames are never
 * stored; only the backend keeps ArcFace vectors until "Hoàn tất đăng ký". */
(() => {
  'use strict';
  const FRAME_INTERVAL_MS = 150;           // ~6 frames/s max, one in flight
  const FRAME_WIDTH = 640;
  const CAMERA_HELP = {
    NotAllowedError: 'Bạn đã từ chối quyền camera. Nhấn biểu tượng camera/ổ khóa bên trái thanh địa chỉ → chọn "Cho phép" camera, rồi nhấn "Bật lại camera".',
    SecurityError: 'Trình duyệt chặn camera. Mở trang bằng http://127.0.0.1:5055 hoặc http://localhost:5055 và cho phép camera.',
    NotFoundError: 'Không tìm thấy camera. Hãy cắm webcam hoặc bật camera của máy, rồi nhấn "Bật lại camera".',
    OverconstrainedError: 'Camera không hỗ trợ cấu hình yêu cầu. Thử webcam khác.',
    NotReadableError: 'Camera đang được ứng dụng hoặc tab khác sử dụng (Zoom, Teams, tab điểm danh…). Hãy tắt chúng rồi nhấn "Bật lại camera".',
    AbortError: 'Không khởi động được camera. Hãy thử lại.'
  };

  function mount(main, ctx) {
    const { api, send, notice, esc, mode, student, classes, token } = ctx;
    const $ = id => document.getElementById(id);
    let session = null, stream = null, loopTimer = null, inFlight = false, stopped = true, lastSend = 0;
    let frameTimes = [], finished = false, starting = false, lastCollected = 0;
    const isNew = mode === 'new';
    const canvas = document.createElement('canvas');

    main.innerHTML = `<a class="back-link" href="${isNew ? '/students' : '/students/' + student.id}">← ${isNew ? 'Danh sách sinh viên' : 'Hồ sơ sinh viên'}</a>
      <div class="page-heading"><div><p class="eyebrow">QUẢN LÝ SINH VIÊN</p><h1>${isNew ? 'Thêm sinh viên' : 'Đăng ký lại khuôn mặt'}</h1>
      <p class="subtitle">${isNew ? 'Nhập thông tin, sau đó AI tự học khuôn mặt qua camera trực tiếp. Không cần chụp hay tải ảnh.' : esc(student.student_code + ' · ' + student.full_name) + ' — thu lại dữ liệu bằng camera. Dữ liệu cũ được giữ cho tới khi dữ liệu mới lưu thành công.'}</p></div></div>
      <ol class="enroll-steps" aria-label="Các bước"><li id="step-1" class="active">1. Thông tin</li><li id="step-2">2. AI học khuôn mặt</li><li id="step-3">3. Xác nhận & lưu</li></ol>
      <section class="panel" id="info-panel"><div class="panel-heading"><h2>Thông tin sinh viên</h2></div><div class="panel-body">
        ${isNew ? `<form id="info-form" class="enroll-info" novalidate>
          <label class="field">Mã sinh viên<input id="f-code" maxlength="64" required autocomplete="off" placeholder="VD: 2251012345"><span class="field-hint" id="code-hint" aria-live="polite"></span></label>
          <label class="field">Họ và tên<input id="f-name" maxlength="160" required autocomplete="off" placeholder="VD: Nguyễn Văn A"></label>
          <label class="field">Lớp<select id="f-class" ${classes.length ? 'required' : ''}><option value="">${classes.length ? 'Chọn lớp…' : 'Chưa có lớp nào (có thể phân lớp sau)'}</option>${classes.map(c => `<option value="${c.id}">${esc(c.class_code + ' · ' + c.class_name)}</option>`).join('')}</select></label>
          <p class="form-error" id="info-error" role="alert"></p>
          <button class="button primary" id="start-ai" type="submit">◎ Đăng ký khuôn mặt bằng AI</button></form>`
        : `<dl class="enroll-summary"><div><dt>Mã sinh viên</dt><dd>${esc(student.student_code)}</dd></div><div><dt>Họ và tên</dt><dd>${esc(student.full_name)}</dd></div><div><dt>Dữ liệu hiện có</dt><dd>${student.face_count} vector</dd></div></dl>
          <p class="form-error" id="info-error" role="alert"></p><button class="button primary" id="start-ai" type="button">◎ Bắt đầu đăng ký lại bằng AI</button>`}
      </div></section>
      <section class="panel" id="camera-panel" hidden><div class="panel-heading"><h2>AI đang học khuôn mặt</h2><span class="badge" id="fps-badge">Camera</span></div>
        <div class="enroll-grid"><div class="stage-col">
          <div class="enroll-stage" id="stage"><div class="mirror"><video id="enroll-video" autoplay muted playsinline></video><canvas id="enroll-overlay"></canvas></div>
            <div class="guide-oval" aria-hidden="true"></div>
            <div class="stage-banner" id="stage-banner" role="status" aria-live="polite">Đang bật camera…</div>
            <div class="stage-pose" id="stage-pose"></div></div>
          <p class="muted small">Hình ảnh được lật như gương cho dễ làm theo. Khung hình chỉ xử lý trong bộ nhớ, không lưu ảnh hay video.</p>
        </div><div class="side-col">
          <div class="overall"><div class="overall-top"><strong>Tiến độ tổng</strong><span id="overall-text">0%</span></div><div class="bar"><span id="overall-bar"></span></div></div>
          <ul class="pose-list" id="pose-list"></ul>
          <p class="pose-timer muted small" id="pose-timer"></p>
          <div class="camera-error" id="camera-error" role="alert" hidden></div>
          <div class="enroll-actions"><button type="button" class="button primary" id="retry-pose" hidden>↻ Thử lại tư thế</button>
            <button type="button" class="button secondary" id="restart-camera" hidden>◎ Bật lại camera</button>
            <button type="button" class="button secondary" id="redo">Làm lại từ đầu</button>
            <button type="button" class="button secondary danger" id="cancel">Hủy</button></div>
          <details class="tech"><summary>Thông số kỹ thuật</summary><div id="tech-body" class="small muted">—</div></details>
        </div></div></section>
      <section class="panel" id="confirm-panel" hidden><div class="panel-heading"><h2>Đã thu thập đủ dữ liệu khuôn mặt</h2><span class="badge success" id="done-badge">✓ Hoàn thành</span></div><div class="panel-body">
        <div id="verify-box"></div>
        <label class="check-label"><input type="checkbox" id="c-identity">Tôi (người vận hành) đã đối chiếu giấy tờ và xác nhận người vừa đứng trước camera đúng là <strong id="c-who"></strong>. AI không tự xác minh danh tính.</label>
        <label class="check-label" id="c-warn-row" hidden><input type="checkbox" id="c-warn">Tôi đã xem cảnh báo nhận dạng ở trên và vẫn xác nhận đây là đúng sinh viên.</label>
        ${isNew ? '' : '<label class="check-label"><input type="checkbox" id="c-replace">Thay thế dữ liệu khuôn mặt đăng ký bằng camera trước đây của sinh viên này (vector gốc từ dataset được giữ nguyên).</label>'}
        <p class="form-error" id="confirm-error" role="alert"></p>
        <div class="enroll-actions"><button class="button primary" id="finish" type="button">Hoàn tất đăng ký</button><button class="button secondary" id="redo-2" type="button">Làm lại</button><button class="button secondary danger" id="cancel-2" type="button">Hủy</button></div>
      </div></section>
      <section class="panel" id="result-panel" hidden><div class="panel-body" id="result-body"></div></section>`;

    const step = n => [1, 2, 3].forEach(i => $('step-' + i).className = i < n ? 'done' : i === n ? 'active' : '');

    // ---------------------------------------------------------------- step 1
    let codeTimer = null, codeOk = !isNew;
    async function checkCode() {
      const code = $('f-code').value.trim();
      codeOk = false;
      if (!code) { $('code-hint').textContent = ''; return false; }
      try {
        const r = await api('/api/enrollment/code-status?code=' + encodeURIComponent(code));
        codeOk = r.available;
        $('code-hint').textContent = r.available ? '✓ Mã hợp lệ, chưa được sử dụng.' : r.message;
        $('code-hint').className = 'field-hint ' + (r.available ? 'ok' : 'bad');
      } catch (e) { $('code-hint').textContent = e.message; $('code-hint').className = 'field-hint bad'; }
      return codeOk;
    }
    if (isNew) {
      $('f-code').addEventListener('input', () => { clearTimeout(codeTimer); codeTimer = setTimeout(checkCode, 350); });
      $('info-form').addEventListener('submit', async e => { e.preventDefault(); await begin(); });
    } else {
      $('start-ai').addEventListener('click', begin);
    }

    async function begin() {
      if (starting) return;
      $('info-error').textContent = '';
      let body;
      if (isNew) {
        const code = $('f-code').value.trim(), name = $('f-name').value.trim(), cls = $('f-class').value;
        if (!code) return void ($('info-error').textContent = 'Hãy nhập mã sinh viên.');
        if (!name) return void ($('info-error').textContent = 'Hãy nhập họ và tên.');
        if (classes.length && !cls) return void ($('info-error').textContent = 'Hãy chọn lớp.');
        if (!await checkCode()) return void ($('info-error').textContent = $('code-hint').textContent || 'Mã sinh viên không hợp lệ.');
        body = { mode: 'new', student_code: code, full_name: name, class_id: cls ? Number(cls) : null };
      } else body = { mode: 'reenroll', student_id: student.id };
      starting = true; $('start-ai').disabled = true;
      try {
        session = await send('/api/enrollment/sessions', 'POST', body);
      } catch (e) { $('info-error').textContent = e.message; starting = false; $('start-ai').disabled = false; return; }
      starting = false;
      $('c-who').textContent = isNew ? `${body.student_code.toUpperCase()} · ${body.full_name}` : `${student.student_code} · ${student.full_name}`;
      if (isNew) [...$('info-form').elements].forEach(el => el.disabled = true);
      else $('start-ai').disabled = true;
      $('camera-panel').hidden = false; step(2);
      renderProgress(session.progress);
      await openCamera();
      $('camera-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // ---------------------------------------------------------------- camera
    async function openCamera() {
      $('camera-error').hidden = true; $('restart-camera').hidden = true;
      if (!navigator.mediaDevices?.getUserMedia) return cameraError({ name: 'SecurityError' });
      try {
        const acquired = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }, audio: false });
        if (finished || !session) { acquired.getTracks().forEach(t => t.stop()); return; }
        stream = acquired;
        stream.getVideoTracks()[0]?.addEventListener('ended', () => { if (!finished) cameraError({ name: 'NotReadableError' }); });
        const video = $('enroll-video'); video.srcObject = stream; await video.play();
        stopped = false; banner('Đưa khuôn mặt vào giữa khung hình.', 'info');
        schedule();
      } catch (e) { cameraError(e); }
    }
    function cameraError(e) {
      stopCamera();
      const box = $('camera-error'); box.hidden = false;
      box.textContent = CAMERA_HELP[e?.name] || 'Không mở được camera. Kiểm tra thiết bị, quyền truy cập và các ứng dụng đang dùng camera.';
      $('restart-camera').hidden = false; banner('Camera chưa hoạt động.', 'bad');
    }
    function stopCamera() {
      stopped = true; clearTimeout(loopTimer); loopTimer = null;
      stream?.getTracks().forEach(t => t.stop()); stream = null;
      const v = $('enroll-video'); if (v) v.srcObject = null;
      const o = $('enroll-overlay'); o?.getContext('2d').clearRect(0, 0, o.width, o.height);
    }
    function schedule() {
      if (stopped) return;
      const wait = Math.max(0, FRAME_INTERVAL_MS - (performance.now() - lastSend));
      loopTimer = setTimeout(tick, inFlight ? FRAME_INTERVAL_MS : wait);
    }
    async function tick() {
      if (stopped || inFlight || !session) return schedule();
      const v = $('enroll-video');
      if (!v.videoWidth) return schedule();
      const scale = Math.min(1, FRAME_WIDTH / v.videoWidth);
      canvas.width = Math.round(v.videoWidth * scale); canvas.height = Math.round(v.videoHeight * scale);
      canvas.getContext('2d').drawImage(v, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.9));
      if (!blob || stopped) return schedule();
      inFlight = true; lastSend = performance.now();
      const sentAt = performance.now();
      try {
        const res = await fetch(`/api/enrollment/sessions/${session.session_id}/frames`, { method: 'POST', body: blob, headers: { 'Content-Type': 'image/jpeg', 'X-Camera-Token': token } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          if (res.status === 404) { stopCamera(); banner(data.error?.message || 'Phiên đăng ký đã hết hạn.', 'bad'); $('camera-error').hidden = false; $('camera-error').textContent = 'Phiên đã hết hạn. Tải lại trang để bắt đầu lại; chưa có gì được lưu.'; }
          else banner(data.error?.message || 'Không xử lý được khung hình, đang thử lại…', 'warn');
        } else handleFrame(data, performance.now() - sentAt);
      } catch { banner('Mất kết nối tới máy chủ, đang thử lại…', 'warn'); }
      finally { inFlight = false; schedule(); }
    }

    // ---------------------------------------------------------------- render
    function banner(text, tone) { const b = $('stage-banner'); b.textContent = text; b.className = 'stage-banner ' + (tone || ''); }
    function handleFrame(d, latency) {
      const now = performance.now(); frameTimes.push(now); frameTimes = frameTimes.filter(t => now - t < 3000);
      $('fps-badge').textContent = `${(frameTimes.length / 3).toFixed(1)} khung/s · ${Math.round(latency)} ms`;
      drawFace(d);
      renderProgress(d.progress, d);
      const q = d.quality || {}, p = d.progress;
      if (p.state === 'complete') return onComplete(d);
      if (p.state === 'pose_timeout') { banner('Hết thời gian cho tư thế này. Nhấn "Thử lại tư thế" — dữ liệu đã thu vẫn được giữ.', 'bad'); return; }
      const tone = q.code === 'NO_FACE' ? 'warn' : q.code === 'MULTIPLE_FACES' ? 'bad' : !q.ok ? 'warn' : d.pose_match ? 'ok' : 'info';
      let text = d.guidance || q.message;
      if (q.code === 'NO_FACE') text = 'Không thấy khuôn mặt — tạm dừng. Đưa mặt vào khung; dữ liệu đã thu vẫn được giữ.';
      if (q.code === 'MULTIPLE_FACES') text = 'Có nhiều khuôn mặt — tạm dừng. Chỉ một sinh viên đứng trước camera.';
      if (d.last_sample && d.last_sample.result === 'AI_BUSY') text += ' (AI đang bận xử lý camera khác)';
      banner(text, tone);
      const m = q.metrics || {};
      $('tech-body').innerHTML = `Tin cậy: ${m.detection_confidence ?? '—'} · Rộng mặt: ${m.face_width_px ?? '—'} px (${Math.round((m.face_width_ratio || 0) * 100)}%)<br>Độ nét: ${m.sharpness ?? '—'} · Độ sáng: ${m.brightness ?? '—'}<br>Yaw: ${d.relative_pose?.yaw ?? m.yaw ?? '—'}° · Pitch: ${d.relative_pose?.pitch ?? m.pitch ?? '—'}°<br>YuNet+chất lượng: ${d.timings_ms?.detection_quality ?? '—'} ms · ArcFace TB/mẫu: ${p.mean_embedding_ms ?? '—'} ms`;
    }
    function drawFace(d) {
      const o = $('enroll-overlay');
      if (!d.frame) return;
      o.width = d.frame.width; o.height = d.frame.height;
      const g = o.getContext('2d'); g.clearRect(0, 0, o.width, o.height);
      if (!d.face) return;
      const b = d.face.box, q = d.quality || {};
      g.lineWidth = Math.max(2, o.width / 200);
      g.strokeStyle = q.ok ? (d.pose_match ? '#22c55e' : '#38bdf8') : '#f59e0b';
      g.strokeRect(b.x, b.y, b.w, b.h);
    }
    function renderProgress(p, d) {
      if (!p) return;
      $('overall-text').textContent = `${p.collected}/${p.required} mẫu · ${p.percent}%`;
      $('overall-bar').style.width = p.percent + '%';
      $('pose-list').innerHTML = p.poses.map(x => `<li class="${x.done ? 'done' : x.current ? 'current' : ''}"><span class="tick" aria-hidden="true">${x.done ? '✓' : x.current ? '●' : '○'}</span><div><strong>${esc(x.label)}</strong>${x.current ? `<span class="small">${esc(x.instruction)}</span>` : ''}<div class="bar small-bar"><span style="width:${Math.round(100 * Math.min(x.collected, x.required) / x.required)}%"></span></div></div><span class="count-pill">${Math.min(x.collected, x.required)}/${x.required}</span></li>`).join('');
      const cur = p.poses.find(x => x.current);
      $('stage-pose').innerHTML = cur ? `<strong>${esc(cur.label)}</strong><span>${esc(cur.instruction)}</span>` : '';
      $('pose-timer').textContent = p.state === 'collecting' && p.pose_time_left_s != null ? `Thời gian còn lại cho tư thế: ${Math.ceil(p.pose_time_left_s)} giây (chỉ tính khi thấy khuôn mặt)${p.pending ? ' · đang xử lý mẫu…' : ''}` : '';
      $('retry-pose').hidden = p.state !== 'pose_timeout';
      if (d && p.collected > lastCollected) {
        $('stage').classList.add('flash');
        setTimeout(() => $('stage')?.classList.remove('flash'), 300);
      }
      lastCollected = p.collected;
    }

    // ---------------------------------------------------------------- complete
    function onComplete(d) {
      if (finished) return;
      finished = true; stopCamera(); step(3);
      banner('Đã thu thập đủ dữ liệu khuôn mặt.', 'ok');
      notice('Đã thu thập đủ dữ liệu khuôn mặt. Hãy xác nhận danh tính rồi nhấn "Hoàn tất đăng ký".');
      $('confirm-panel').hidden = false;
      renderVerification(d.verification);
      $('confirm-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    async function ensureVerification() {
      for (let i = 0; i < 20 && !session.verification; i++) {
        const s = await api(`/api/enrollment/sessions/${session.session_id}`); session.verification = s.verification;
        if (!s.verification) await new Promise(r => setTimeout(r, 500));
      }
      renderVerification(session.verification);
    }
    function renderVerification(v) {
      session.verification = v || session.verification;
      v = session.verification;
      if (!v) { $('verify-box').innerHTML = '<p class="muted">Đang so sánh với dữ liệu khuôn mặt đã có…</p>'; ensureVerification().catch(() => {}); return; }
      const rows = (v.top_other || []).map(o => `<li>${esc(o.student)} — tương đồng trung vị ${o.median_similarity.toFixed(2)}</li>`).join('');
      const own = v.own ? `<p class="small muted">So với dữ liệu cũ của chính sinh viên: tương đồng trung vị ${v.own.median_similarity.toFixed(2)}.</p>` : '';
      $('verify-box').innerHTML = (v.warnings?.length ? v.warnings.map(w => `<div class="alert warn">⚠ ${esc(w.message)}</div>`).join('') : '<div class="alert ok">Không phát hiện trùng với sinh viên khác trong gallery.</div>') +
        own + (rows ? `<details class="small"><summary>Sinh viên gần giống nhất</summary><ul>${rows}</ul><p class="muted">${esc(v.note || '')}</p></details>` : '');
      $('c-warn-row').hidden = !v.warnings?.length;
    }
    $('finish').addEventListener('click', async () => {
      const err = $('confirm-error'); err.textContent = '';
      if (!$('c-identity').checked) return void (err.textContent = 'Hãy xác nhận đã đối chiếu danh tính sinh viên.');
      if (session.verification?.warnings?.length && !$('c-warn').checked) return void (err.textContent = 'Hãy xác nhận đã xem cảnh báo nhận dạng.');
      if (!isNew && !$('c-replace').checked) return void (err.textContent = 'Hãy xác nhận thay thế dữ liệu khuôn mặt cũ.');
      const b = $('finish'); b.disabled = true; b.textContent = 'Đang lưu…';
      try {
        const r = await send(`/api/enrollment/sessions/${session.session_id}/complete`, 'POST', { identity_confirmed: true, acknowledge_warnings: $('c-warn').checked, confirm_replace: isNew ? false : $('c-replace').checked });
        session = null;
        $('confirm-panel').hidden = true; $('camera-panel').hidden = true; $('info-panel').hidden = true; $('result-panel').hidden = false;
        const poses = Object.entries(r.per_pose).map(([k, n]) => `${k}: ${n}`).join(' · ');
        $('result-body').innerHTML = `<div class="result-ok"><div class="big-tick">✓</div><h2>${isNew ? 'Đã thêm sinh viên' : 'Đã cập nhật khuôn mặt'}</h2><p>${esc(r.message)}</p>
          <p class="small muted">${r.embeddings_saved} embedding (${esc(poses)})${r.replaced_embeddings ? ` · thay ${r.replaced_embeddings} embedding cũ` : ''} · gallery phiên bản ${r.gallery_revision}${r.gallery_loaded ? ' (AI đã nạp)' : ''}<br>Thời gian thu: ${r.timing?.collection_s ?? '—'} giây · ArcFace trung bình ${r.timing?.mean_embedding_ms ?? '—'} ms/mẫu</p>
          <div class="enroll-actions"><a class="button primary" href="/students/${r.student_id}">Xem hồ sơ</a>${isNew ? '<a class="button secondary" href="/students/new">Thêm sinh viên khác</a>' : ''}</div></div>`;
        notice(r.message);
      } catch (e) { err.textContent = e.message + ' Chưa có thay đổi nào được ghi nếu thông báo nói đã hoàn tác.'; b.disabled = false; b.textContent = 'Hoàn tất đăng ký'; }
    });

    // ---------------------------------------------------------------- controls
    $('retry-pose').addEventListener('click', async () => { try { renderProgress((await send(`/api/enrollment/sessions/${session.session_id}/retry-pose`, 'POST')).progress); banner('Tiếp tục tư thế hiện tại.', 'info'); } catch (e) { notice(e.message, true); } });
    $('restart-camera').addEventListener('click', openCamera);
    const redo = async () => {
      if (!session) return;
      try {
        const r = await send(`/api/enrollment/sessions/${session.session_id}/reset`, 'POST');
        session.verification = null; finished = false; step(2);
        $('confirm-panel').hidden = true; $('camera-panel').hidden = false; $('c-identity').checked = false; $('c-warn').checked = false;
        renderProgress(r.progress); if (!stream) await openCamera();
        notice('Đã xóa dữ liệu vừa thu. Bắt đầu lại từ tư thế nhìn thẳng.');
      } catch (e) { notice(e.message, true); }
    };
    $('redo').addEventListener('click', redo); $('redo-2').addEventListener('click', redo);
    const cancel = async () => { await teardown(); location.href = isNew ? '/students' : '/students/' + student.id; };
    $('cancel').addEventListener('click', cancel); $('cancel-2').addEventListener('click', cancel);

    async function teardown(beacon) {
      stopCamera(); finished = true;
      const s = session; session = null;
      if (!s) return;
      const req = fetch(`/api/enrollment/sessions/${s.session_id}`, { method: 'DELETE', headers: { 'X-Camera-Token': token }, keepalive: true }).catch(() => {});
      if (!beacon) await req;
    }
    window.addEventListener('pagehide', () => teardown(true));
    return { teardown };
  }

  window.FaceEnrollment = { mount };
})();
