# Sửa nhãn camera và đơn giản hóa Điểm danh AI

## Nguyên nhân kiểm tra được

- YuNet chỉ trả tọa độ; tên phải chờ luồng RetinaFace + ArcFace riêng.
- Luồng cũ có thời gian hết hạn nhãn 2,5 giây, độ mới preview 1,2 giây và so khớp ảnh RGB thô quá nhạy với ánh sáng/crop jitter. Track bị thay ID thì ticket của yêu cầu ArcFace đang chạy không còn liên kết được.
- Lần nhận diện đầu có thể chụp trước khi preview trả về, tạo ticket rỗng. Thay đổi tổng số mặt còn khiến toàn bộ phản hồi bị loại, kể cả những khuôn mặt vẫn được theo dõi.
- API thật không thiếu embedding hoặc ánh xạ SQLite trong các mẫu đã thử: có 92 vector/5 sinh viên, hai ảnh một người đều trả đúng hồ sơ tương ứng. `top1.candidate` là chuỗi gồm mã và tên; overlay đọc tên/mã từ `top1.student`.

## Thay đổi

- `frontend/static/js/face_tracks.js`: kiểm tra liên tục riêng từng track, ghép một-một; bù sai lệch crop nhỏ và thay đổi sáng nhẹ. Không đổi ngưỡng cosine hay mô hình AI. Chặn phản hồi của track đã mất hoặc bị thay thế.
- `frontend/static/js/camera.js`: chờ có track trước khi bắt đầu nhận diện ban đầu; giữ tên khi request mới đang chạy; bỏ chặn toàn cục theo số lượng mặt. Preview/track phải còn mới. Thời gian giữ tên mới tối đa 10 giây từ khi nhận kết quả, đồng thời giới hạn tuổi ảnh nguồn 30 giây. Sau đó ghi rõ `kết quả cũ`, bỏ tô sáng ứng viên; xóa nhãn sau thời gian giữ tối đa 20 giây từ phản hồi hoặc tuổi ảnh 40 giây. Mất dấu hoặc ảnh thay đổi thì gỡ tên ngay theo kiểm tra hiển thị, không đợi các thời gian này. Có chẩn đoán số ticket/face liên kết được, không xuất embedding.
- `frontend/templates/index.html`: bỏ card Kết quả nhận diện khỏi trang điểm danh, giữ ở trang chẩn đoán camera. Bên phải giữ thống kê và danh sách; thông tin kỹ thuật chuyển vào mục thu gọn dưới camera.
- `frontend/static/js/attendance.js`: tô xanh dương dòng ứng viên còn mới, xác nhận thủ công từ danh sách. Dữ liệu Có mặt và tiến độ vẫn chỉ lấy từ API SQLite sau lưu thành công. Không có nút xác nhận từ kết quả AI cũ.
- `frontend/static/css/style.css`: màu nền ứng viên xanh dương nhạt và mục kỹ thuật thu gọn.
- `tests/live_labels_browser_qa.py`, `tests/phase2_browser_qa.py`: cập nhật kiểm thử theo giao diện mới.

## Kiểm thử

API thật tại cổng 5055, profile RetinaFace detector 480 hiện có, ArcFace/base/alignment không đổi. Ảnh chỉ xử lý trong RAM, không lưu ảnh camera:

| Trường hợp | Mặt YuNet / RetinaFace | HTTP nhận diện |
|---|---|---|
| Ảnh sinh viên A | 1 / 1 | 13,67 giây |
| Ảnh sinh viên B | 1 / 1 | 5,06 giây |
| Group10.jpg | 2 / 2 | 8,73 giây |
| Ảnh trống tổng hợp | 0 / 0 | 2,04 giây |

Hai mẫu một người trả ứng viên đúng hồ sơ nguồn, có mã SQLite. Không suy đoán nhãn thật cho ảnh nhóm và không công bố accuracy từ những mẫu này. Chi tiết JSON thật: `outputs/overlay_api_check.json`.

- `tests/live_labels_browser_qa.py`: 13 nhóm kiểm tra đạt, gồm tên trước xác nhận, hai mặt, xác nhận SQLite, mirror/resize/DPR, đổi người cùng vị trí, không có mặt, ứng viên chưa xác định, phản hồi muộn và tắt camera; bổ sung mô phỏng chậm 14 giây, kết quả cũ, thay đổi sáng và ghép ảnh/JSON từ API thật. Video UI dùng giả lập; kiểm tra ảnh/API thật được phát lại trong bộ theo dõi. Kết quả: `outputs/live_labels_tests.json`.
- `tests/phase2_browser_qa.py`: 17 nhóm kiểm tra đạt với Flask/SQLite tạm: lớp 5 người, tiến độ 0/5 → 1/5, kết thúc còn 4 vắng, chỉnh sửa → 2/5, đổi lớp/buổi, khởi động lại ứng dụng, responsive. Kết quả: `outputs/attendance_simplified_ui_tests.json`.
- `python -m unittest tests.test_attendance`: 12 test nghiệp vụ đạt.
- Đối chiếu SHA-256 ảnh DATASET, embedding, cấu hình, ngưỡng và logical dump SQLite thật trước/sau: không thay đổi. Không tạo bản ghi điểm danh thử trong database thật.

## Giới hạn

Chưa kiểm thử camera vật lý với người thật trong lượt này. Kiểm tra RGB/crop là cơ chế theo dõi hiển thị, không phải xác minh sinh trắc học; có thể mất dấu khi chuyển động mạnh hoặc nhiều người che nhau. Người có hình ảnh tương tự vẫn là rủi ro của theo dõi hình ảnh. Khi ghép mơ hồ thì không hiện tên thay vì tự gán tên sang track khác.

Laptop vẫn cần khoảng 5–14 giây cho các mẫu nhận diện đã đo; đây không phải xác nhận đạt tốc độ thời gian thực. Các thời gian tuổi track/nhãn chỉ điều khiển UI, không phải ngưỡng chấp nhận danh tính. Không tạo ngưỡng chính thức và không tự ghi Có mặt.
