> Cập nhật sau phản hồi người dùng: khôi phục tên ứng viên với nhãn rõ “Gợi ý AI — chưa xác minh” trên camera và danh sách gợi ý ở trang /attendance. Không coi tên gợi ý là danh tính đã xác minh; không thay đổi policy, ngưỡng hoặc khóa tự ghi. Thông báo chính hiển thị “Chế độ xác nhận thủ công” thay cho danh sách lỗi kỹ thuật dài. Sửa thông báo nhầm “Chưa có hồ sơ SQLite” khi hồ sơ thực tế tồn tại. Bổ sung helper recognition_display.js và 6 tình huống kiểm thử Node; 41 kiểm thử Python vẫn đạt. Dòng mô tả ẩn toàn bộ tên gợi ý trong báo cáo cũ bên dưới đã được thay thế bởi cập nhật này.

# Kết quả sửa chức năng điểm danh theo prompt — 16/11/2026

## A. Chẩn đoán
Dự án thực tế: D:/AI/AI/FACE_RECOGNITION. Flask/SQLite/ArcFace/RetinaFace; YuNet phát hiện vị trí. Frontend dùng HTTP fetch cho preview, nhận diện và commit, không dùng websocket; danh sách còn đồng bộ mỗi 5 giây.
Luồng tự động đã có nhưng mặc định tắt vì policy chính thức và đánh giá độc lập chưa tồn tại. Trước sửa chỉ yêu cầu 2 kết quả nhất quán; UI hiển thị tên ứng viên chưa xác minh và ghi nhãn nguồn AI thành MANUAL.

## B. Dữ liệu và giới hạn đánh giá
Có 5 sinh viên, 3 lớp, 92 embedding; manifest có 25 mẫu calibration, không có holdout.
Báo cáo gán nhãn có sẵn ghi 22 khuôn mặt sinh viên, 30 UNKNOWN, 2 IGNORE, 50 chưa gán nhãn. 30 UNKNOWN chưa có mã phân biệt từng người. Ngưỡng calibration được đề xuất trước đây: similarity 0.533511788286182, margin 0.17386882414194063; nhận đúng 12/22 (54,5%), không đạt 90%. Không công bố ngưỡng này thành policy.
Báo cáo cũ có ca DEMO-SV002 bị chọn thành DEMO-SV005, cosine 0.7098578137141089, margin 0.13463664520040564: cosine cao không đủ xác minh.
Audit lại ảnh gốc hiện bị chặn do thiếu DATASET/Test/3.jpg và các nguồn DATASET trong bản dự án. Không sửa crop/alignment hoặc đổi ngưỡng khi chưa đánh giá được pipeline trên nguồn chuẩn. Không chia lại calibration thành holdout. Dừng đánh giá cuối, giữ chế độ thủ công.

## C. Mã đã sửa
- backend/attendance/stability.py: cửa sổ 7 lần suy luận độc lập theo thứ tự request trên cùng track; cần >=5 lần cùng student_id và frame hiện tại phải đạt. Chưa đủ 7 quan sát không ghi. Mất track, đổi danh tính hoặc gián đoạn thứ tự request sẽ reset. Preview không được tính là bằng chứng ArcFace.
- backend/app.py: áp dụng cửa sổ trước khi cấp grant ghi, báo cấu hình 5/7, không đánh dấu đủ điều kiện khi chưa ổn định hoặc ngoài lớp. Giữ kiểm tra grant một lần, track, policy, gallery và session tại commit.
- backend/face_recognition/runtime_service.py: so khớp top-2 toàn gallery trước khi kiểm tra lớp; không dùng top-2 giới hạn trong lớp để suy ra danh tính. Cho phép xác minh danh tính ngoài lớp nhưng không cấp quyền ghi cho họ.
- backend/attendance/automatic.py: giữ khóa mặc định; thêm kiểm tra cố định FAR<=1%, FRR<=10%, không sai danh tính, tối thiểu 200 mẫu webcam và 10 mã người lạ; báo cáo phải có kết quả temporal 5/7 không ghi sai. Không chấp nhận báo cáo tự nới mục tiêu.
- backend/attendance/attendance_service.py: migration cộng thêm source auto/manual, similarity, margin; giữ recognition_source để tương thích. Backup trước migration, giữ UNIQUE(session_id,student_id). Lưu audit tự động riêng để bằng chứng còn tồn tại sau sửa tay. Kiểm tra lại thành viên lớp và sinh viên chưa bị xóa trong transaction tự động. Sửa tay hiện có thực hiện sau khi kết thúc buổi và giữ lịch sử.
- backend/face_recognition/results.py: kết quả public camera không lộ danh tính ứng viên chưa xác minh; bản ghi Có mặt cũ không làm một khuôn mặt chưa xác minh thành ATTENDED.
- frontend/static/js/camera.js: ẩn tên chưa xác minh, hiện Không xác định; chỉ hiện Không thuộc lớp sau xác minh; bỏ dòng Đang nhận diện; không tô sinh viên theo ứng viên chưa xác minh.
- frontend/static/js/attendance.js: phân biệt nguồn AI/MANUAL; không cho xác nhận trực tiếp một ứng viên chưa xác minh. Vẫn có thể xác nhận thủ công từ danh sách lớp.
- tests/test_attendance_stability.py, tests/test_verified_attendance_storage.py, tests/test_attendance.py: kiểm thử chính sách, tiêu chí khóa, audit bền vững và top-2 toàn gallery.

## D. Kiểm chứng
41 kiểm thử unit/protocol đạt. Các fixture giao thức chỉ kiểm tra logic, không được dùng chứng minh độ chính xác ArcFace. Bao gồm 5/7, đổi người, thứ tự request, ghi trùng, ngoài lớp/đã gỡ lớp, hủy hoặc mất track, rollback batch, lỗi API, khởi động lại storage, audit sau sửa tay và khóa khi thiếu bằng chứng. Chuyển 0/2 -> 1/2 chỉ được kiểm chứng ở mức giao dịch SQL trên DB tạm.
Node --check cho hai file JS và git diff --check đạt. Chưa kiểm tra trực quan bằng trình duyệt/webcam.
Flask được khởi động lại tại http://127.0.0.1:5055; health ready=true, 92 embedding, 5 sinh viên. Kiểm tra một ảnh nhóm thật còn trong assets qua API: phát hiện khuôn mặt, danh tính vẫn chưa xác minh, trường student_name public bị ẩn. Đây là ảnh lưu sẵn, không phải webcam hay holdout độc lập.
Không tạo buổi hoặc bản ghi điểm danh thật để test. Đối chiếu toàn bộ cột gốc của students/classes/face_embeddings/attendance_sessions/attendance_records/attendance_edits với backup đều khớp; SHA-256 embedding không đổi; PRAGMA integrity_check=ok. Chi tiết: outputs/attendance_prompt_fix_checks.json.

## Điều kiện còn thiếu và cách kiểm tra
TỰ ĐỘNG VẪN KHÓA. Cần khôi phục ảnh gốc đúng đường dẫn/hash để audit calibration; hoàn thiện nhãn và mã riêng cho người lạ. Thu tối thiểu 200 frame webcam mới độc lập với calibration, gồm sinh viên đã đăng ký và >=10 người lạ khác nhau; sáng/tối/ngược sáng, chính diện/nghiêng, nhiều khoảng cách; lưu chuỗi frame theo capture_session, track, thứ tự và nhãn thật để đánh giá 5/7. Không coi 200 frame liền nhau là 200 thử nghiệm độc lập. Cần đủ mẫu cho các kiểm tra khoảng tin cậy hiện có; với 0 lỗi, muốn cận trên 95% FAR <=1% cần khoảng 299 thử nghiệm người lạ độc lập, vì vậy 200 frame tổng chỉ là mức tối thiểu, không bảo đảm đủ bằng chứng.
Đóng băng model/gallery/profile/ngưỡng trước khi dùng holdout; đánh giá FAR<=1%, true accept>=90%, 0 sai danh tính và 0 ghi sai sau 5/7. Report workflow hiện tại chỉ đánh giá ảnh tĩnh; không được tự điền temporal_evaluation hay holdout_webcam_frames để mở khóa. Cần thu và đánh giá các chuỗi webcam thật trước khi có báo cáo temporal hợp lệ.
Mở /attendance, chọn lớp, bắt đầu hoặc tiếp tục buổi, bật camera. Hiện hệ thống báo chế độ thủ công và không hiển thị tên chưa xác minh. Xác nhận trực tiếp từ danh sách khi giảng viên đã đối chiếu người thật. Chỉ sau khi có bằng chứng độc lập đạt yêu cầu và bật cấu hình, khuôn mặt vượt ngưỡng 5/7 mới được tự ghi Có mặt, cập nhật danh sách và hiển thị Đã điểm danh sau khi SQLite commit thành công.
Rủi ro còn lại: chưa chống ảnh in/màn hình điện thoại (liveness ngoài phạm vi); chưa chứng minh nhận diện chính xác, người lạ, đổi người và backend chậm bằng webcam thật. Không tuyên bố đã đạt các ca E2E này.
