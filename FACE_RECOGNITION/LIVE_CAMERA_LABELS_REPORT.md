# Nhãn ứng viên trên camera

Đã thêm nhãn họ tên, mã sinh viên, cosine similarity và trạng thái ngay cạnh từng khung khuôn mặt. Ô kết quả bên phải và thao tác xác nhận thủ công vẫn giữ nguyên. Nhãn `Đã điểm danh` đọc từ trạng thái buổi sau khi API xác nhận đã lưu SQLite, không dựa vào AI.

## File thay đổi

- `frontend/static/js/face_tracks.js`: theo dõi hiển thị riêng từng khuôn mặt, ghép một-một bằng vị trí và kiểm tra thay đổi hình ảnh, chặn ticket cũ khi mất dấu hoặc thay đổi tỷ lệ ảnh.
- `frontend/static/js/camera.js`: vẽ nhãn navy/trắng, ánh xạ object-fit contain, hỗ trợ lật gương và DPR, cập nhật trạng thái từ buổi điểm danh.
- `frontend/static/js/attendance.js`: phát sự kiện trạng thái từ dữ liệu API sau xác nhận hoặc tải lại buổi.
- `frontend/templates/index.html`: nạp bộ theo dõi và thêm nút chọn lật gương.
- `frontend/static/css/style.css`: định dạng điều khiển lật gương.
- `tests/live_labels_browser_qa.py`: kiểm thử video tổng hợp, AI giả lập, Flask thật và SQLite tạm.

## Kiểm thử

Chạy `.venv\Scripts\python.exe tests/live_labels_browser_qa.py`: 12 nhóm kiểm tra đạt. Bao gồm một/hai khuôn mặt; tên, mã và điểm; xác nhận thủ công lưu SQLite rồi cập nhật nhãn; người mới ở cùng vị trí; không có mặt; ứng viên chưa xác định; mất dấu và tái xuất hiện; đảo thứ tự kết quả; ghép mơ hồ; hết hạn nhãn; thay đổi tỷ lệ/độ phân giải; lật gương, resize và DPR=2; tắt camera khi HTTP còn xử lý; không có lỗi JavaScript. Kết quả: `outputs/live_labels_tests.json`. Ảnh minh họa chỉ dùng video tổng hợp: `.venv/live_labels_synthetic.png`.

Chạy `.venv\Scripts\python.exe -m unittest tests.test_attendance`: 12 test đạt.

Kiểm tra SHA-256 ảnh DATASET, embedding, cấu hình và ngưỡng trước/sau; đối chiếu logical dump SQLite thật: không thay đổi. Không tạo điểm danh thử trong database thật; không thay đổi ArcFace/RetinaFace hoặc 92 embedding.

## Giới hạn và quy tắc hiển thị

Chưa thử lại với người thật trước camera laptop trong lượt này. Kiểm thử tự động dùng video tổng hợp và AI giả lập, không phải đánh giá độ chính xác sinh trắc học.

Nhãn chỉ hiện trên track còn được quan sát gần đây (1,2 giây); nhãn ứng viên hết hạn sau 2,5 giây nếu không có kết quả mới. Khi hình ảnh thay đổi đáng kể, track mất dấu hoặc ghép mơ hồ, tên cũ bị gỡ; kết quả cũ không được dùng lại trên track mới. Các giới hạn vị trí, thời gian và khác biệt ảnh chỉ là kiểm soát hiển thị, không phải ngưỡng cosine hay xác nhận danh tính. Người di chuyển mạnh hoặc backend chậm có thể thấy `Đang nhận diện...` thường xuyên. Hai người có hình ảnh rất giống nhau vẫn là giới hạn của theo dõi bằng ảnh; không coi nhãn là danh tính đã xác minh. Không có ngưỡng danh tính mới, không tự động ghi Có mặt.

Đã khởi động lại Flask tại `http://127.0.0.1:5055/attendance`: trang trả HTTP 200, nạp script theo dõi mới và có điều khiển lật gương. Health sau khởi động: `ready=true`, `registered_embeddings=92`, `registered_students=5`, `model_load_count=1`, `threshold_enabled=false`.
