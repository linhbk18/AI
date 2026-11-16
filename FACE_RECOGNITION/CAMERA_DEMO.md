# Camera nhận diện trên localhost

Chạy từ `F:\FACE_RECOGNITION`, không cần kích hoạt môi trường ảo:

```powershell
& .\.venv\Scripts\python.exe -m backend.app
```

Mở **http://127.0.0.1:5055**, đợi backend báo sẵn sàng, bấm **Bật camera** và
cho phép trình duyệt truy cập. Ứng dụng dùng cổng 5055 vì cổng 5000 đang có một
dịch vụ khác. Không dùng trang ở cổng 5000 để thử phiên bản này.

## Luồng hoạt động

- Trình duyệt dùng `getUserMedia`, không có nút chụp ảnh. Video camera chạy liên
  tục; mỗi khung gửi suy luận được giới hạn cạnh dài 640 px, giữ tỷ lệ ảnh.
- Chỉ gửi khung tiếp theo sau khi yêu cầu trước kết thúc, cách thêm tối thiểu
  150 ms. Backend cũng khóa để chỉ có một tác vụ suy luận, trả 429 thay vì xếp hàng.
- Flask nạp 92 vector đăng ký, cấu hình và chính sách hiện có một lần khi khởi
  động. ArcFace/RetinaFace pretrained được nạp một lần trong tiến trình, không
  tạo lại embedding đăng ký hay tải lại model theo từng yêu cầu.
- Mỗi khuôn mặt có khung, Top-1, Top-2 thuộc hai sinh viên khác nhau, cosine và
  chênh lệch điểm. Mọi ứng viên đều chưa xác nhận, không có thao tác điểm danh.
- Canvas kết quả vẽ đúng khung đã gửi. Khung trên video trực tiếp chỉ xuất hiện
  khi kết quả còn mới; nếu suy luận chậm, xem phần **Khung nhận diện gần nhất**.
  Thời hạn hiển thị này không phải ngưỡng similarity hay ngưỡng xác nhận danh tính.
- Không có mặt thì danh sách ứng viên được xóa. Tắt camera hoặc rời tab sẽ dừng
  track, dừng gửi yêu cầu, xóa kết quả và hủy phiên trên backend.

## Hiệu năng và giới hạn đã quan sát

Model thật trên laptop hiện tại mất khoảng **13–29 giây/khung** trong các kiểm
thử HTTP và camera. Video vẫn trực tiếp nhưng tần suất suy luận chưa đạt thời
gian thực mượt. Detector, normalization và alignment được giữ nguyên; không
tự chuyển sang detector khác hoặc đặt ngưỡng để che giới hạn này.

Lệnh native TensorFlow đã bắt đầu không thể bị ngắt an toàn ngay lập tức khi
nhấn Tắt camera. Backend đánh dấu phiên đã hủy, không nhận khung mới và bỏ kết
quả ở điểm hủy an toàn sau lệnh native. Không trả danh tính muộn về phiên đã tắt.
Không khởi động lại model chỉ để hủy một khung.

Camera thật đã mở trong trình duyệt và hoàn tất một khung suy luận (khung đó
không phát hiện mặt). Chưa dàn cảnh một/nhiều người hoặc đổi người trước camera
thật. Các trường hợp một mặt, hai mặt và đổi ứng viên đã được kiểm tra bằng ảnh
đầu vào HTTP với model thật và bằng camera giả lập ở phần giao diện. Những kiểm
thử này không thay thế đánh giá danh tính có nhãn thật trong điều kiện camera.

## Dữ liệu và cấu hình

- Không lưu ảnh camera, video hoặc embedding camera xuống đĩa. Screenshot QA
  chỉ chụp giao diện khi camera tắt. Khung và canvas kết quả chỉ ở bộ nhớ trình
  duyệt/backend.
- Chỉ gửi lên frontend tọa độ, ứng viên, điểm và thông tin hiệu năng. Không gửi
  vector, ảnh đăng ký, đường dẫn dữ liệu hoặc nội dung các JSON sinh trắc học.
- Flask chỉ phục vụ templates/static và các API được khai báo, không có route
  cho `DATASET/`, `outputs/` hoặc điểm danh. Bind loopback, không bật debug/reloader.
- `DATASET`, toàn bộ embedding, `recognition_threshold.json` và kết quả kiểm thử
  trước đó giữ nguyên. Không có ngưỡng chính thức thì mọi danh tính vẫn chưa xác nhận.

## Các file chính

- `backend/app.py`: Flask, phiên camera, kiểm tra JPEG và API.
- `backend/config.py`: cổng local, giới hạn dung lượng/kích thước khung.
- `backend/face_recognition/runtime_service.py`: model trong bộ nhớ, khóa suy
  luận, xử lý từng khuôn mặt, tái sử dụng `FaceMatcher` từ `face_service.py`.
- `frontend/templates/index.html`, `frontend/static/css/style.css`,
  `frontend/static/js/camera.js`: giao diện camera và xử lý cập nhật liên tục.
- `outputs/camera_test_report.json`: kết quả QA thực tế và giới hạn kiểm thử.

Kiểm thử API không cần mở camera hoặc nạp model:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_camera_api.py -v
```

Các script `tests/camera_browser_qa.py`, `tests/camera_live_api_qa.py` và
`tests/camera_physical_smoke.py` cần server đang chạy. Hai script browser dùng
Playwright và Chrome đã cài; script physical mở camera thật, xử lý một khung
rồi tắt, không lưu ảnh. `backend/requirements-dev.txt` chỉ dành cho QA trình duyệt.

Tham khảo API: [getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
và [giới hạn upload trong Flask](https://flask.palletsprojects.com/en/stable/patterns/fileuploads/).

K?t qu? t?i ?u v? c?c c?u h?nh th? nghi?m: xem [CAMERA_OPTIMIZATION.md](CAMERA_OPTIMIZATION.md).
