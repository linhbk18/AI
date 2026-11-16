# Luồng tự động ghi Có mặt — 15/11/2026

## Kết luận tách biệt

- Luồng kỹ thuật VERIFIED → camera.js tự POST commit → Attendance Service → SQLite PRESENT/AI_VERIFIED → frontend 0/2 thành 1/2 → overlay Đã điểm danh: **đã chạy thành công bằng fixture VERIFIED**, không nhấn xác nhận thủ công.
- Nhận diện ArcFace thật: kiểm thử riêng, không giả nhận diện fixture là bằng chứng sinh trắc học. Chưa có VERIFIED thật từ policy được đánh giá độc lập; vì vậy production vẫn khóa.
- DATASET, 92 embedding, nhãn người dùng, policy chính thức hiện tại và SQLite thật được giữ nguyên; mọi ghi điểm danh kiểm thử dùng SQLite sao lưu tạm.

## Điều kiện đang chặn production

AUTO_ATTENDANCE_ENABLED đang false; recognition_threshold.json chưa official/enabled, similarity/margin null; không có automatic_attendance_evaluation.json đạt yêu cầu; independent known/unknown holdout đều 0. 30 UNKNOWN của Test là calibration, chưa có mã người nhất quán, không thay cho holdout. Tên ứng viên không phải identity_confirmed/VERIFIED.

Không có lỗi SQLite buộc người dùng phải bấm nút: nhánh ứng viên hiện bị chặn trước khi cấp quyền commit tự động. Chưa thể bật hợp lệ bằng score 0.7292 riêng lẻ hoặc proposal calibration.

## Thay đổi thực hiện

- backend/app.py: API POST /api/attendance/automation-mode bật/tắt có kiểm chứng, status can_enable và lý do khóa; lưu activation gắn hash policy/profile. API từ chối bật khi thiếu bằng chứng. Thêm điều kiện hai kết quả VERIFIED liên tiếp cùng student_id trên cùng server track và request sequence; đổi ứng viên/mất track thì bắt đầu lại. Cấp receipt dùng một lần sau khi đạt điều kiện, không cho client gửi student_id tùy ý để ghi tự động.
- backend/attendance/automatic.py: kiểm tra cả cận upper95 từ báo cáo so với mục tiêu, không chỉ tỷ lệ lỗi quan sát được. Similarity/margin vẫn đến từ policy có báo cáo được hash kiểm chứng.
- frontend/static/js/camera.js: nút Bật/Tắt điểm danh tự động theo readiness thực tế; tự yêu cầu lượt xác minh tiếp theo khi chờ tính nhất quán thay vì giữ cache ứng viên làm dừng pipeline; chặn request_id đến muộn; tự commit và phát sự kiện attendance:auto sau SQL thành công; báo lỗi commit; không còn thông báo “hệ thống không tự ghi” khi chế độ tự động đã bật.
- frontend/static/js/attendance.js: nhận dữ liệu session đã lưu, cập nhật roster/thống kê/overlay và thông báo các trang khác bằng sự kiện lưu trữ.
- tests/verified_camera_e2e.py: kiểm thử browser + HTTP + SQLite thật với fixture tại biên Recognition, evidence/policy fixture chỉ ở thư mục tạm. Không có endpoint xác nhận thủ công được gọi. Production không nạp engine fixture.
- tests/test_camera_api.py: kiểm tra không thể bật khi thiếu policy, từ chối enabled/profile sai định dạng.

Hai lần VERIFIED là điều kiện nhất quán bảo thủ, không được coi là hai thử nghiệm sinh trắc học độc lập hoặc thay thế đánh giá FAR. Không có thay đổi mô hình, detector, preprocessing hoặc gallery.

## Thời gian và tính mới

Không giới hạn suy luận ở 2 giây. Khi suy luận 2.41 giây hoặc lâu hơn, preview vẫn cập nhật server track; chỉ kết quả gắn đúng track hiện tại mới cấp receipt. Snapshot có giới hạn 30 giây, preview không được ngắt quá 2.5 giây; receipt phải commit trong 3 giây kể từ khi **tạo kết quả**, không phải từ khi bắt đầu inference. Request sequence, phiên camera, hash policy, gallery revision và continuity được kiểm tra lại khi ghi SQL. Kết quả cũ, track mất dấu, camera tắt, request mới thay thế đều không ghi. Không dùng việc kéo dài TTL để bỏ qua đổi người.

## Bằng chứng kỹ thuật tự động

outputs/verified_camera_e2e.json ghi rõ test_kind là VERIFIED protocol fixture, không phải biometric evidence. Trình duyệt chạy camera.js thật với canvas video thay webcam vật lý; engine fixture chờ 2.41 giây/request. Sau hai kết quả nhất quán, API commit tự được gọi, SQLite tạm ghi PRESENT, recognition_source AI_VERIFIED và confirmed_at; frontend đổi 0/2 thành 1/2, overlay Đã điểm danh. Số request tới /confirm bằng 0. Kiểm tra chống trùng, UNKNOWN/candidate chưa xác minh, ngoài lớp, mất track, receipt hết hạn, sequence cũ, camera tắt và persistence đều đạt. Fixture không chứng minh khả năng nhận đúng người lạ/sinh viên.

## Các bước kích hoạt hợp lệ

1. Chọn trước các mục tiêu FAR, FRR và nhận sai sinh viên. Không dùng lỗi trên holdout để chỉnh lại mục tiêu hoặc ngưỡng.
2. Thu calibration webcam của đủ cả 5 sinh viên và UNKNOWN, rồi thu evaluation mới độc lập, người UNKNOWN khác calibration. Mỗi người/phiên độc lập chỉ một ảnh; không tính nhiều frame liên tiếp là các thử nghiệm độc lập. Xem WEBCAM_HOLDOUT_GUIDE.md và công cụ collect_webcam_validation.
3. Nếu tận dụng Test làm calibration, phải hoàn thiện mã UNKNOWN và nhãn dùng trong phân tích. Hoàn tất toàn bộ 50 khung Test là hữu ích nhưng **không phải điều kiện bắt buộc của runtime** nếu dùng một bộ calibration webcam mới riêng đầy đủ; không cần tiếp tục gắn nhãn Test để chỉ sửa luồng tự động.
4. Chạy audit rồi evaluate với profile camera và ba mục tiêu đã chốt. Ví dụ các cờ là --max-far, --max-frr, --max-wrong; xem --help. Workflow khóa trial trước inference holdout và không cho retune/tái dùng tập con ảnh đã lộ.
5. Chỉ khi report status=passed, chạy `.\.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow publish`. Khởi động lại Flask bằng `.\.venv\Scripts\python.exe -m backend.app` để nạp policy.
6. Chọn lớp/buổi đang mở và đúng profile, bấm **Bật điểm danh tự động**. Backend chỉ cho bật nếu report/hash/profile/gallery/threshold/cận lỗi đều hợp lệ; việc bật không ghi Có mặt ngay. Bật camera: cùng track phải nhận được hai kết quả VERIFIED nhất quán mới tự commit, không cần nút xác nhận sinh viên.
7. Kiểm tra bằng webcam thật với người trong lớp, ngoài lớp, UNKNOWN, đổi người và mất dấu. Tắt chế độ bằng nút tương ứng; camera dừng khi đổi chế độ, người vận hành bật lại sau đó.

Không có số ảnh cố định đủ cho mọi mục tiêu. Ví dụ nếu chọn FAR <=1% với cận một phía 95%, cần ít nhất 299 thử nghiệm UNKNOWN thực sự độc lập và không lỗi; nếu cũng chọn wrong-known <=1%, cần ít nhất 299 known độc lập và không lỗi, phủ đủ 5 người. Nếu có lỗi hoặc tương quan, số này không bảo đảm đạt. Calibration hiện từ chối 10/22 nên cần cải thiện/đánh giá trên webcam đại diện, không chỉ thêm ảnh để ép báo cáo pass.

Không có thao tác publish/activate production trong lượt này. Không tuyên bố camera sinh trắc học đã tự điểm danh khi còn thiếu dữ liệu độc lập.

Hồi quy lượt này: 28/28 unittest đạt. `upgrade_real_qa.py` chạy ArcFace thật, 25/25 validation Top-1 đúng (không phải FAR/VERIFIED), xử lý JPEG/PNG/WebP, ảnh nhóm/trống/mờ, MP4/AVI, hủy job/file lỗi, ngoài lớp, browser chụp ảnh/tải ảnh/video và persistence đều đạt. outputs/upgrade_real_qa.json có protected_unchanged=true. Đã chạy lại nhánh tự động fixture bằng tests/verified_camera_e2e.py; xem outputs/verified_camera_e2e.json cho từng điều kiện. Webcam vật lý chưa được kiểm thử.

Sau khởi động lại, health=ready, gallery 92/5, nạp model một lần, requested=false/can_enable=false. Kiểm thử readonly production browser đạt, bao gồm nút tự động bị khóa đúng điều kiện và bốn nguồn dữ liệu; SQLite/gallery không đổi. Sửa test smoke cũ vốn hardcode buổi 7: buổi 7 đã COMPLETED nên không thể canStart; test nay chọn buổi mở thực tế, không sửa trạng thái buổi trong database. Buổi 8 hiện IN_PROGRESS, DEMO-SV001 student_id=1 và DEMO-SV002 student_id=2, thống kê thật vẫn 0/2; không ghi bản demo vào buổi này. Xem http://127.0.0.1:5055/attendance?session=8 để tiếp tục buổi hiện có.
