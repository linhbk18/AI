# Hoàn thiện camera và luồng điểm danh tự động

## Kết quả

Camera chỉ vẽ khung nếu chưa có ứng viên được liên kết với đúng track; không vẽ chữ chờ hoặc nhãn chưa xác định. Có ứng viên thì vẽ họ tên, mã, similarity và trạng thái ứng viên/đã điểm danh. Không dùng CSS để che thông báo. Trạng thái xử lý trong mục kỹ thuật thu gọn vẫn có để chẩn đoán.

YuNet chỉ phát hiện nhanh; tên vẫn do RetinaFace + ArcFace và gallery 92 vector tạo ra. Đã kiểm tra bằng API thật và trình duyệt gọi HTTP thật. Video kiểm thử lấy từ ảnh dataset qua MediaStream canvas, không phải camera vật lý và không dùng kết quả AI giả lập. Không thay đổi detector/normalization/alignment hoặc mặc định profile `retinaface_scaled_experimental` của giao diện hiện tại.

## Nguyên nhân chậm và thay đổi

- RetinaFace và ArcFace chiếm phần lớn thời gian. Lúc chạy cả Chrome, ArcFace có mẫu hơn 5 giây; decode khoảng 10 ms và HTTP/client khoảng 60 ms trong mẫu đầu. Máy có 3,84 GiB RAM; khi đo tải 96%, còn khoảng 122 MiB vật lý trống. Đây là dấu hiệu áp lực bộ nhớ lớn, không phải chỉ JavaScript vẽ chậm.
- Yêu cầu kiểm tra khung trống có thể chiếm khóa AI ngay trước khi người mới xuất hiện. Nay đợi cảnh trống ổn định 8 giây trước fallback RetinaFace; vẫn giữ fallback để không bỏ hẳn mặt bị YuNet bỏ sót.
- Track mới được ưu tiên gửi ngay khi preview đã có tọa độ. Không thêm hàng đợi; vẫn chỉ một yêu cầu ArcFace đang xử lý. Sau phản hồi không ghép được track, thử khung mới ngay thay vì chờ thêm 1–3 giây.
- Giữ kết quả hiển thị trên track còn ổn định, tránh gọi AI liên tục khi cache còn mới; tuổi hiển thị giới hạn theo ảnh nguồn. Cache không dùng làm bằng chứng ghi Có mặt. Gỡ nhãn khi mất dấu, ảnh thay đổi hoặc phiên camera đổi; phản hồi cũ không được hồi sinh track đã hủy.
- Canvas kiểm tra track tái sử dụng bộ nhớ thay vì đổi kích thước mỗi lần vẽ. Model và gallery tiếp tục nạp một lần.

Thử biên dịch đồ thị TensorFlow cho đúng ArcFace hiện có: trên 25 ảnh validation, so sánh vector với eager trên cùng tensor đạt sai số cực đại <1e-4 và cosine >0,999999; 25/25 phân loại đúng. Tuy nhiên warm HTTP hai mặt gần như không cải thiện (2,560 so với 2,555 giây ở lượt thứ hai); đồ thị thử nghiệm **không được bật trong runtime**. Không thay mô hình hay đánh đổi detector lấy tốc độ.

## Số liệu

Hai lượt trình duyệt cùng video ảnh thật, API và model thật; số mẫu nhỏ, không phải cam kết độ trễ cho mọi điều kiện:

| Từ đưa ảnh vào video đến tên xuất hiện | Trước sửa lịch gửi/cache cuối | Sau sửa |
|---|---:|---:|
| Sinh viên A | 8,43 s | 8,12 s |
| Đổi sang sinh viên B | 17,45 s | 9,37 s |

Chỉ đo API trong tiến trình, không Chrome: eager một mặt 2,27–2,45 s; thử graph 2,07–2,08 s. Không so trực tiếp các số này như độ trễ camera. Chưa đạt mục tiêu 1–2 giây trong trình duyệt.

## Tự động Có mặt

Đã nối luồng server-side: match Top-2 toàn gallery → kiểm tra policy chính thức và bằng chứng đánh giá → kiểm tra thuộc buổi/lớp → giao dịch SQLite `AI_VERIFIED` → JSON thống kê → frontend cập nhật danh sách/tiến độ. Không có API cho frontend tự khai `identity_confirmed=true` hoặc dùng cờ `AI_VERIFIED` để ghi điểm danh. Khóa phiên và giao dịch ngăn ghi sau tắt camera/kết thúc buổi; ảnh quá 2 giây từ lúc server nhận không được dùng ghi tự động. Chống trùng theo sinh viên/buổi.

**Chế độ tự động vẫn TẮT** (`Config.AUTO_ATTENDANCE_ENABLED=False`). File ngưỡng hiện có `min_similarity=null`, `min_margin=null`, số mẫu người chưa đăng ký có nhãn là 0. Không tự đặt ngưỡng và không thay `recognition_threshold.json`. Xác nhận thủ công vẫn hoạt động.

Trước khi bật cần bổ sung dữ liệu known/unknown có nhãn, tách tập chọn ngưỡng và tập đánh giá độc lập, đo FAR/FRR/nhầm danh tính, xác định mục tiêu chấp nhận, kiểm thử đúng profile camera/gallery hiện tại. Policy chính thức phải liên kết bằng SHA-256 tới `outputs/automatic_attendance_evaluation.json`, có pipeline signature, profile, ngưỡng, kết quả độc lập và phê duyệt sử dụng tự động. Không chỉ bật một cờ hoặc lấy ứng viên có similarity cao nhất. Nhánh tự động bật với người thật chưa thể kiểm thử đáng tin cậy khi bằng chứng còn thiếu.

## Kiểm thử đã thực hiện

- `tests/real_ai_execution_benchmark.py`: model thật, API thật, đo decode/detect/align/embedding/match; 25 vector eager/graph và 25 nhãn validation đạt. Kiểm tra auto OFF, không ghi từ ứng viên, chặn cờ AI từ client, xác nhận thủ công, chống trùng, ảnh cũ và kết thúc buổi trên bản sao SQLite. Cuối lượt có lỗi dọn database tạm do Windows giữ connection; đã sửa đóng connection và kiểm thử riêng việc backup/đóng/dọn thành công. Các kiểm tra AI/SQL đã hoàn tất trước lỗi dọn tệp. Báo cáo: `outputs/real_ai_execution_benchmark.json`.
- `tests/real_camera_browser_qa.py`: chạy thành công HTTP qua cổng 5055, model thật; tên/mã đúng trước xác nhận, hai sinh viên lần lượt, gỡ nhãn khi trống, hai mặt riêng, giữ tên ổn định 5 giây không thêm yêu cầu ArcFace, cập nhật SQLite và thống kê sau xác nhận, tắt camera xóa nhãn, không lỗi JavaScript. Không dùng AI double/mock. Báo cáo: `outputs/real_camera_browser_qa.json`.
- `tests/test_verified_attendance_storage.py`: 4 test giao dịch SQLite đạt; nguồn AI_VERIFIED, chống trùng, kết thúc, ảnh cũ/hủy, outsider rollback và policy hiện tại không bật được tự động. Đây là kiểm thử primitive lưu trữ, không tuyên bố đã xác minh AI với người lạ.
- Đối chiếu hash DATASET, face_embeddings, face_config, recognition_threshold và logical dump database thật trước/sau: không đổi. Dữ liệu kiểm thử điểm danh chỉ nằm trong database tạm.

Số liệu RAM: `outputs/real_ai_memory_report.json`; tổng hợp: `outputs/ai_completion_summary.json`.

## File mã nguồn thay đổi

- `frontend/static/js/camera.js`, `face_tracks.js`, `attendance.js`.
- `backend/attendance/automatic.py` (mới), `attendance_service.py`, `backend/config.py`, `backend/app.py`.
- `backend/face_recognition/runtime_service.py`, `camera_pipeline.py` (hàm thử graph chưa bật).
- Ba file kiểm thử thật/SQL nêu trên; cập nhật kỳ vọng nhãn trống trong `tests/live_labels_browser_qa.py` để phù hợp yêu cầu mới, không chạy bộ AI giả lập đó trong lượt này.

## Kiểm tra camera thật

Mở `http://127.0.0.1:5055/attendance`, tải lại trang để nhận JS mới; chọn lớp, bắt đầu buổi và bật camera. Chưa có ứng viên phải chỉ thấy khung; có ứng viên thì thấy tên và mã. Đứng yên kiểm tra tên ổn định; lần lượt ra/vào khung và thử hai người. Có mặt vẫn cần xác nhận thủ công cho đến khi đủ bằng chứng bật tự động. Kiểm tra tiến độ tăng một lần và được giữ khi tải lại trang. Chưa thử camera vật lý trong lượt thực hiện này.
