# Tự động điểm danh: sửa thời gian, bổ sung quy trình kiểm chứng

## Trạng thái bàn giao

**Chưa đạt mục tiêu tự động điểm danh trên webcam/demo.** Kỹ thuật liên kết kết quả chậm và giao dịch ghi Có mặt đã được bổ sung, nhưng chưa được bật vì còn thiếu dữ liệu xác minh. Không tạo một ngưỡng dựa riêng vào similarity 0.7292, không sửa trạng thái buổi thật để giả lập thành công.

## Thay đổi thời gian và liên kết khuôn mặt

Không còn điều kiện suy luận phải hoàn tất trong 2 giây ở đường ghi camera. Có ba mốc riêng: timestamp chụp của client (chỉ chẩn đoán, không tin để ghi), server nhận frame/bắt đầu suy luận và server có kết quả.

- Mỗi phiên camera có bộ theo dõi server riêng, ID ngẫu nhiên, snapshot của frame và số thứ tự yêu cầu. YuNet cập nhật track liên tục bằng vị trí/kiểm tra thay đổi hình ảnh. Ghép mơ hồ, đổi ảnh, mất dấu hoặc đổi tỷ lệ thì không giữ liên kết cũ. Preview đến muộn không được hồi sinh track.
- Sau RetinaFace/ArcFace, mỗi mặt phải ghép một-một với snapshot và đúng ID server còn được theo dõi. Vẫn có giới hạn an toàn: khoảng cách preview hiện tại ≤2,5 giây và tuổi ảnh tối đa 30 giây, thay vì bắt toàn bộ suy luận ≤2 giây.
- Chỉ mặt đã vượt policy xác minh và còn liên kết mới được cấp receipt ngẫu nhiên, dùng một lần. Receipt gắn request, track, phiên camera, buổi, signature gallery và policy.
- Frontend sau kiểm tra track hiện tại tự POST `/api/camera/sessions/<id>/attendance/commit`; không gửi student_id hay cờ tự khai đã xác minh. Server kiểm tra lại track và metadata, receipt không quá 3 giây **từ khi có kết quả**, camera chưa tắt, policy còn hợp lệ, gallery chưa đổi. Kiểm tra lại ngay trong giao dịch SQLite trước khi lưu `AI_VERIFIED` và thời gian. SQL vẫn chống trùng theo buổi/sinh viên.
- Khi đã commit, payload buổi cập nhật danh sách, tỷ lệ và nhãn Đã điểm danh. Cache ứng viên hiển thị không được dùng thay cho chứng cứ ghi Có mặt.

Kiểm tra ảnh ở đây chỉ bảo vệ liên tục track, không tự chứng minh danh tính. Chuyển động mạnh hoặc che nhau có thể khiến từ chối ghi; người có hình ảnh tương tự vẫn cần kiểm thử webcam thực tế. Không tuyên bố tracking là xác minh sinh trắc học.

## Xác minh và signature

Cosine được tính trên vector chuẩn hóa; mỗi danh tính lấy điểm cao nhất trong các vector của mình; Top-2 là **hai sinh viên khác nhau**. Đã kiểm tra gallery thật đủ 92 vector/5 người và margin bằng Top-1 trừ Top-2.

Sửa việc policy luôn bị bỏ khi gallery SQLite có revision khác 0: chỉ nhận policy khi **signature thực sự khớp gallery runtime**, không dựa riêng revision. Quy trình mới tính signature bằng đúng gallery/model/config runtime, gắn thêm profile camera trong báo cáo. Có kiểm tra hash báo cáo, nguồn ảnh, phiên bản/model/detector/tiền xử lý. Policy mới cần được nạp sau publish; readiness không được báo đủ điều kiện khi runtime chưa nạp policy.

## Dữ liệu và công cụ

Đã tạo `backend/face_recognition/verification_workflow.py` với các lệnh `init`, `seed-known-calibration`, `audit`, `evaluate`, `publish`, `activate`, `deactivate`.

Đã chuẩn bị `outputs/verification_data/manifest.csv`: 25 tham chiếu ảnh validation thật, ánh xạ đúng mã SQLite, chỉ dùng calibration. Không sao chép hoặc đổi ảnh gốc. Audit hiện tại:

| Tập | Known | Unknown |
|---|---:|---:|
| Calibration | 25 | 0 |
| Evaluation độc lập | 0 | 0 |

Chạy đánh giá hiện tại đã từ chối vì thiếu dữ liệu, ghi báo cáo needs_real_labelled_data, không ghi policy chính thức. Lệnh activate cũng từ chối. `recognition_threshold.json` và 92 embedding được giữ nguyên.

Sau khi có ảnh thật: chỉ tìm ngưỡng trên calibration, đánh giá holdout riêng, kiểm tra cận trên nhị thức một phía 95% thay vì chỉ dùng FAR thực nghiệm bằng 0. Chặn ảnh trùng, phiên chụp trùng split, unknown cùng người ở cả hai split, ảnh cũ làm holdout, nhiều frame cùng lượt đánh giá. Registry chặn dùng lại holdout với cấu hình/ngưỡng khác. Publish yêu cầu báo cáo đạt, hash không đổi và signature đang chạy còn đúng; activate lưu cấu hình bên ngoài mã nguồn. Không tự chọn mục tiêu rủi ro cho người vận hành.

Hướng dẫn chi tiết, số lượng đề xuất và lệnh từng bước: **VERIFICATION_DATA_GUIDE.md**. Ví dụ kế hoạch: thêm 100 unknown calibration, 60 known holdout (12/người) và ≥299 unknown holdout độc lập; hai nhóm unknown khác người. 299 là lượng cần để cận trên FAR một phía 95% dưới 1% nếu 0 lỗi và giả định thử nghiệm độc lập, không phải bảo đảm độ tin cậy ngoài thực tế. Mục tiêu tỷ lệ lỗi do người vận hành lựa chọn; không có similarity/margin mặc định.

## Kiểm thử và giới hạn bằng chứng

- `python -m unittest tests.test_continuity_receipts tests.test_verified_attendance_storage`: **11 test đạt**. Dùng ảnh/embedding và kết quả phát hiện thật đã lưu để kiểm thử primitive tracking, đồng hồ thử nghiệm 8 giây, mất dấu/đổi người, preview đảo thứ tự, camera mới, source AI_VERIFIED, 0/2 → 1/2, chống trùng/ngoài lớp/khởi động lại và công thức cận tin cậy. Nhánh SQL dùng callback kiểm tra track; không coi đây là AI tự xác minh.
- `python tests/real_auto_attendance_trace.py`: **đạt**, dùng model và HTTP thật, không mock kết quả AI. Linh trả đúng student_id; mẫu suy luận khoảng **2,766 giây** vẫn `tracking.valid=true`. Người đã đăng ký nhưng ngoài lớp bị chặn; ảnh trống không ghi. Bơm preview ảnh trống lúc native inference đang chạy: track cũ bị loại, grants rỗng. Tắt camera trong suy luận thật: HTTP 409. Trong trình duyệt, ứng viên có tên trước xác nhận; xác nhận thủ công chuyển 0/2 → 1/2 và giữ sau tải lại/tạo lại Flask app. Đường sự kiện auto UI được kiểm tra riêng với payload SQLite đã commit, không giả lập xác minh AI.
- Báo cáo máy đọc: `outputs/real_auto_attendance_trace.json`, `outputs/verification_workflow_report.json`.
- Đối chiếu hash DATASET, embedding, face_config, recognition_threshold và logical dump SQLite thật trước/sau: không đổi. Các buổi điểm danh thử chỉ nằm trong SQLite tạm.
- **Chưa có dữ liệu unknown để đo FAR thật, chưa kiểm thử camera vật lý, chưa kiểm thử thành công nhánh policy chính thức → receipt → tự ghi trên webcam.** Không được diễn giải kiểm thử SQL/API thành bằng chứng demo tự điểm danh đã hoạt động.

## File sửa/thêm

- `backend/attendance/continuity.py` mới; `automatic.py`, `attendance_service.py`.
- `backend/app.py`, `backend/config.py`, `backend/face_recognition/runtime_service.py`.
- `backend/face_recognition/verification_workflow.py` mới.
- `frontend/static/js/camera.js`, `face_tracks.js`.
- `tests/test_continuity_receipts.py` mới; `test_verified_attendance_storage.py`, `real_auto_attendance_trace.py` cập nhật.
- `VERIFICATION_DATA_GUIDE.md`, báo cáo này và manifest/báo cáo audit trong outputs.

Không đổi layout/tone màu, không xóa SQLite, không thêm embedding hoặc thay ArcFace/RetinaFace. Xác nhận thủ công giữ làm dự phòng. Muốn thử tự động bằng webcam: bổ sung ảnh, audit → evaluate → duyệt/publish → activate → khởi động lại Flask → tải lại trang/chọn đúng profile → mở buổi và thử 13 tình huống người dùng yêu cầu. Nếu không đạt, deactivate ngay; không bỏ gate để ép Có mặt.
