# Truy vết tự động Có mặt cho DEMO-SV001

## Nguyên nhân thực tế

Database thật: DEMO-SV001/Bùi Kiều Linh có `student_id=1`, chưa xóa và thuộc snapshot buổi `id=7`, lớp `id=2`, đang `IN_PROGRESS`. Buổi có hai sinh viên (DEMO-SV001 và DEMO-SV003), cả hai chưa ghi nhận. Không phải lỗi thiếu ánh xạ hoặc thiếu sinh viên trong buổi.

Ứng viên top-1 không phải kết quả xác minh. `AUTO_ATTENDANCE_ENABLED=False`; policy `insufficient_labelled_unknown_data`, `enabled=false`, cả ngưỡng similarity và margin là null, unknown có nhãn bằng 0, chưa đánh giá độc lập và chưa có `outputs/automatic_attendance_evaluation.json`. Vì thế ArcFace trả `identity_confirmed=false`, Attendance Service không được gọi ghi AI_VERIFIED. Điểm 0.7292 của khung camera người dùng nêu không đủ để vượt các điều kiện này. Khung camera đó không được lưu, nên không tuyên bố tái lập chính xác điểm này.

Frontend không cần gọi một API xác nhận bằng student_id từ ứng viên. Backend `/api/recognize` đã có nhánh tự ghi sau xác minh server-side; client chỉ nhận trạng thái đã commit, phát `attendance:auto` và cập nhật danh sách/thống kê. Gửi xác nhận tự động từ client chỉ dựa vào tên sẽ bỏ qua kiểm chứng và không được triển khai.

## Đã sửa và kiểm tra

- `backend/attendance/automatic.py`: bổ sung readiness với lý do chặn cụ thể; kiểm tra đầy đủ hash báo cáo, profile camera, pipeline signature, ngưỡng, metrics và phê duyệt; dùng cùng kiểm tra bằng chứng cho trạng thái và xác minh. Không bật cờ khi chưa đủ chứng cứ.
- `backend/app.py`: thêm GET `/api/attendance/automation-status`; trả readiness trong health và kết quả nhận diện theo profile thực tế, để phân biệt khóa cấu hình, thiếu ngưỡng, thiếu unknown và thiếu báo cáo.
- `frontend/static/js/camera.js`: đọc trạng thái này và hiển thị lý do trong vùng thông báo đã có. Không thêm card hoặc đổi bố cục.
- `frontend/static/js/attendance.js`: nhận payload buổi đã lưu từ backend, render ngay; thông báo phản ánh cập nhật từ máy chủ thay vì tự tuyên bố AI đã xác minh.
- `tests/test_verified_attendance_storage.py`: thêm chuyển 0/2 → 1/2 và kiểm tra mở lại SQLite, bổ sung kiểm tra policy thực tế bị khóa.
- `tests/real_auto_attendance_trace.py`: kiểm thử mới với model, HTTP API và SQLite thật trên bản sao tạm. Không giả lập kết quả nhận diện.

## Kết quả kiểm thử

`python -m unittest tests.test_verified_attendance_storage`: 5 test đạt. Nhánh lưu trữ nội bộ AI_VERIFIED ghi một lần, có timestamp, từ chối batch chứa người ngoài lớp, ảnh cũ/hủy, buổi kết thúc; 0/2 → 1/2 và giữ lại khi tạo lại service. Đây là kiểm thử giao dịch, **không phải bằng chứng AI đã xác minh người thật**.

`python tests/real_auto_attendance_trace.py`: chạy đạt với ArcFace/RetinaFace hiện có và API thật qua localhost:5055:

- Ảnh registered của Bùi Kiều Linh trả đúng DEMO-SV001/student_id=1, thuộc lớp, nhưng `identity_confirmed=false` theo policy hiện tại; không tự ghi Có mặt.
- Ảnh Hà Vi là sinh viên đã đăng ký nhưng ngoài lớp hai người kiểm thử: `outside_class=true`, không đủ điều kiện xác nhận và không tự ghi Có mặt.
- Ảnh trống không trả khuôn mặt, không ghi.
- Trình duyệt hiển thị tên Linh trước xác nhận; thống kê vẫn 0/2 và thông báo chế độ tự động đang khóa. Sau xác nhận thủ công, SQLite và giao diện chuyển ngay 1/2, progress value=1/max=2; gửi lại không ghi trùng.
- Kiểm tra riêng hợp đồng sự kiện `attendance:auto` bằng payload buổi thật đã commit từ SQLite: frontend cập nhật đúng; không dùng một kết quả AI giả để tuyên bố tự xác minh. Kết quả Có mặt trong bài kiểm thử trình duyệt này có nguồn MANUAL.
- Tải lại trang và tạo lại Flask app với cùng database tạm vẫn thấy 1/2. Đối chiếu hash dataset, 92 embedding, config/ngưỡng và logical dump SQLite thật trước/sau: không thay đổi. Buổi thật số 7 không bị ghi thử.

Chi tiết JSON: `outputs/real_auto_attendance_trace.json`. Chưa kiểm thử camera vật lý và chưa thể kiểm thử end-to-end nhánh tự động bật một cách có căn cứ vì thiếu dữ liệu unknown có nhãn. Ảnh người ngoài lớp đã đăng ký không được tính thay cho người lạ chưa đăng ký.

## Điều kiện ghi tự động và việc còn thiếu

Phải có policy chính thức đã kiểm thử trên known và unknown có nhãn, tập đánh giá độc lập không dùng để chọn ngưỡng, báo cáo được phê duyệt/hash khớp cho đúng gallery và profile camera. So khớp Top-2 toàn gallery phải đạt cả similarity lẫn margin. Sinh viên phải thuộc snapshot buổi đang mở; camera chưa hủy; ảnh phải còn mới. Giao dịch SQLite chống trùng, lưu AI_VERIFIED và thời gian rồi mới trả payload cập nhật UI.

Tự động **vẫn tắt**. Ngoài thiếu ngưỡng và dữ liệu người lạ, nhánh ghi tự động hiện từ chối ảnh quá 2 giây từ khi server nhận; tốc độ trình duyệt đã đo ở lượt trước khoảng 8–9 giây chưa đáp ứng. Không nới tuổi ảnh hoặc tự đặt ngưỡng để ép ghi Có mặt. Cần đánh giá xác minh và độ mới với pipeline camera thực tế trước khi bật.

Sau khởi động lại, health xác nhận model sẵn sàng và 92 vector; readiness còn báo signature policy cũ không khớp gallery/pipeline runtime. Cần tạo bằng chứng/ngưỡng cho đúng signature đang chạy, không tái sử dụng cấu hình cũ. Buổi thật số 7 được kiểm tra lại vẫn nguyên 0/2, không có bản ghi thử.

## Camera thật

Sau khi Flask khởi động lại, tải lại `http://127.0.0.1:5055/attendance`, mở lại buổi của lớp. Kiểm tra vùng thông báo lý do khóa, cho Linh vào camera và đối chiếu tên/mã. Hiện phải dùng xác nhận thủ công để ghi Có mặt vì chưa đủ điều kiện bật tự động; không kỳ vọng điểm 0.7292 tự chuyển 0/2 thành 1/2. Khi có đầy đủ chứng cứ và cấu hình hợp lệ, backend sẽ tự ghi và UI nhận payload đã lưu mà không cần nút xác nhận cho trường hợp đã xác minh.
