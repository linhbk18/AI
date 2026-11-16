# Báo cáo nâng cấp hệ thống điểm danh AI

## 1. Phạm vi và kiến trúc

Nâng cấp trực tiếp dự án hiện tại, giữ Flask/SQLite, 5 sinh viên, 92 embedding, các trang quản lý, ArcFace/RetinaFace và tone navy/xanh dương/trắng. Không migration database, không sửa DATASET hay gallery.

Trước: camera liên tục → YuNet preview → RetinaFace/ArcFace → ứng viên; xác nhận thủ công và cơ chế tự động có điều kiện. Đã có giao thức server track/receipt thay giới hạn suy luận 2 giây từ lượt sửa trước; không làm lại phần này.

Sau: cùng pipeline và cache model phục vụ bốn nguồn. Camera giữ nhánh server continuity + receipt + commit khi đủ điều kiện. Chụp ảnh/tải ảnh/tải video → tác vụ có giới hạn → chuẩn hóa kết quả → danh sách đề xuất không trùng → người dùng duyệt → giao dịch MANUAL chung Attendance Service → SQLite → cập nhật thống kê. Video xử lý nền một tác vụ, tiến độ bằng frame thực đã xử lý, có hủy. Không thêm card kết quả trùng vào camera trực tiếp.

## 2. Công nghệ và quyết định hiệu năng

Giữ ArcFace vì gallery hiện có thuộc đúng mô hình này. YuNet chỉ preview nhanh; giữ RetinaFace cho nhận diện vì các benchmark cũ trên máy đã ghi YuNet 23/25 hoặc 21/25, RetinaFace 25/25. Không đổi detector hoặc trộn embedding để lấy tốc độ. Giữ cách chạy eager đã có; thử TensorFlow graph ở lượt trước không cho lợi ích ổn định đa khuôn mặt nên không bật.

Công nghệ/chức năng mới dùng thư viện hiện có: OpenCV VideoCapture/codec, Pillow JPEG/PNG/WEBP, worker Python thread có event hủy và API polling. Không cài thêm model hoặc runtime. Không chạy đồng thời nhiều bản model. Engine dùng một inference lock chung; media job chờ có giới hạn khi engine bận, không tạo hàng đợi frame vô hạn. Frontend dừng camera khi chuyển phương thức. File tạm được stream xuống thư mục riêng, không giữ toàn bộ video/frame trong RAM.

Đã khảo sát các lựa chọn qua nguồn chính thức:

| Lựa chọn | Tương thích / Windows / tài nguyên | Quyết định |
|---|---|---|
| OpenCV YuNet/DNN | Đang chạy trên Windows; model nhẹ, dùng được cho preview; landmark khác có thể đổi embedding | Giữ preview, không tự thay nhánh nhận diện |
| ONNX Runtime CPU | Cần chuyển đúng model và kiểm tra vector/tiền xử lý, RAM và tốc độ thực trên máy | Chưa triển khai hay tuyên bố nhanh hơn; tránh thêm một bản model khi RAM hạn chế |
| MediaPipe Detection/Mesh | Cần dependency/landmark pipeline riêng và đánh giá lại alignment | Chưa chọn; không có benchmark mới trên máy để khẳng định lợi ích |
| InsightFace/SFace | Đổi implementation/weights có thể mất tương thích gallery; cần gallery riêng và đánh giá lại | Không thay ArcFace hiện tại |

Giấy phép: [ONNX Runtime MIT](https://github.com/microsoft/onnxruntime/blob/main/LICENSE), [YuNet/OpenCV Zoo MIT](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/README.md), [MediaPipe Apache-2.0](https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE). [InsightFace phân biệt mã nguồn và pretrained weights, model công bố có giới hạn dùng nghiên cứu phi thương mại](https://github.com/deepinsight/insightface/blob/master/python-package/docs/model_zoo.md). [DeepFace có giấy phép MIT](https://github.com/serengil/deepface/blob/master/LICENSE), nhưng [các model/weights bọc bên trong cần xét giấy phép riêng](https://github.com/serengil/deepface/blob/master/README.md). Không suy ra giấy phép weights chỉ từ giấy phép thư viện. Không tải weights mới trong lượt này.

## 3. Benchmark và chất lượng

Đo mới bằng HTTP thật, model thật, ba lượt mỗi loại, profile giao diện hiện tại `retinaface_scaled_experimental`, ảnh vào giữ tỷ lệ tối đa 640×480. Startup/warm-up khoảng 58,04 giây, model_load_count=1. Hai lượt sau trong mỗi nhóm là warm samples; n=3 nhỏ, P95 chỉ mang tính mô tả.

| Trường hợp | Trung bình HTTP | P95 HTTP | Trung bình hai lượt warm |
|---|---:|---:|---:|
| Một mặt | 2,415 s | 2,468 s | 2,392 s |
| Hai mặt | 2,912 s | 3,197 s | 2,750 s |
| Không mặt | 1,521 s | 1,559 s | 1,500 s |

Phân rã trung bình, đơn vị ms:

| Mẫu | Decode | RetinaFace | Alignment | Embedding | Matching |
|---|---:|---:|---:|---:|---:|
| Một mặt | 3,37 | 1580,92 | 100,55 | 567,98 | 4,98 |
| Hai mặt | 8,71 | 1592,23 | 160,11 | 991,44 | 1,46 |

HTTP bao gồm phần còn lại của server/transport; từng stage là số đo server, không cộng chúng thành thời gian webcam. Các số đo capture/HTTP/hiển thị và tuổi track của camera vẫn được lưu ở `window.cameraDiagnostics`; webcam vật lý chưa đo trong lượt này. Báo cáo lượt trước có thời gian đưa ảnh vào video trình duyệt tới tên khoảng 8,12–9,37 giây. **Chưa chứng minh đạt mục tiêu 1–2 giây.**

So với mốc trước trong `outputs/real_ai_execution_benchmark.json`, API eager warm một mặt khoảng 2,269 giây và hai mặt 2,560 giây. Không thấy cải thiện kernel đáng tin cậy trong phép đo mới; chênh lệch do tải/RAM/warm-up không được quảng bá thành tăng tốc do code. Lợi ích nâng cấp là tái sử dụng model, tránh frame queue, chọn frame video có giới hạn và cập nhật bất đồng bộ. Video thử 4 giây/4fps có 16 frame nhưng chỉ suy luận 2 frame, không suy luận cả 16. Điều này có thể bỏ sót người xuất hiện ngắn hơn khoảng lấy mẫu; không tuyên bố recall video 100%.

**25/25 ảnh validation phân loại đúng** trong 5 người đã đăng ký khi chạy mới. Đây là closed-set accuracy, không phải độ chính xác xác minh người lạ. Ảnh làm mờ vẫn phát hiện 1 mặt trong mẫu kiểm thử; không có chốt chất lượng mờ đã kiểm chứng nên vẫn chỉ là đề xuất, không xác minh tự động.

Chi tiết benchmark/stage/validation: `outputs/upgrade_real_qa.json`.

## 4. Xác minh và tự động Có mặt

Không thay ngưỡng null bằng một số tùy tiện. Hiện có 25 known calibration, 0 unknown calibration và 0 known/unknown holdout mới. Không đủ đo FAR hay tạo policy chính thức. Tự động camera **vẫn khóa**; không tự ghi chỉ vì tên hoặc similarity 0.7292.

Giữ quy trình đánh giá độc lập, confidence bounds, signature gallery runtime, server track/request ID và receipt dùng một lần. Bổ sung lệnh `verification_workflow add-sample` để nhập đường dẫn ảnh thật + nhãn + split + lượt chụp, kiểm tra trước khi thêm manifest. Không tự thêm vào gallery đăng ký. Hướng dẫn nhập dữ liệu/đánh giá/publish/activate ở `VERIFICATION_DATA_GUIDE.md`.

Chuỗi ghi tự động khi đủ điều kiện vẫn là: policy đúng model/profile/gallery → match Top-2 qua similarity/margin → sinh viên thuộc buổi → track còn liên tục → cấp receipt → frontend tự commit → kiểm tra lại trước transaction → AI_VERIFIED + timestamp → UI cập nhật. Không dùng tổng thời gian suy luận >2 giây để loại ngay; kiểm tra độ mới của preview hiện tại và phiên xuất hiện. Chưa có chứng cứ để kiểm thử nhánh tự động bật với webcam thật. Chưa có chống giả mạo ảnh/màn hình được kiểm chứng; track continuity không đồng nghĩa anti-spoofing.

## 5. Bốn phương thức và quy tắc nguồn

| Phương thức | Đã triển khai | Ghi Có mặt |
|---|---|---|
| Camera trực tiếp | Giữ video/track, khung và tên; không chữ chờ trên khung | Chỉ tự động khi policy và presence/continuity hợp lệ; hiện tắt |
| Chụp ảnh | Mở camera xem trước, chụp, ảnh có khung/tên, chụp lại, duyệt danh sách | MANUAL sau duyệt; không tự động |
| Tải ảnh | JPEG/PNG/WEBP thực, ảnh một/đa mặt, không mặt, preview và danh sách không trùng | MANUAL sau duyệt; không tự động |
| Tải video | Preview, metadata, worker nền, lấy mẫu mỗi 2s, timestamp/frame, chọn khung xem nhãn, danh sách duy nhất, hủy | MANUAL sau duyệt; không tự động |

Đã kiểm thử backend MP4/mp4v và AVI/MJPG, WebM/VP8 qua cả trình duyệt và backend. MP4 là container, không bảo đảm mọi codec trong MP4 đều phát được trên trình duyệt. MOV chưa có fixture thực tế để xác nhận; hỗ trợ tùy OpenCV/codec. Nếu browser không phát được nhưng backend đọc được, hiển thị thông báo; không giả lập preview. Timestamp dùng decoder khi có, nếu phải tính index/fps thì gắn `timestamp_estimated`; chọn frame chờ seek trước khi vẽ để không gắn khung cũ lên ảnh khác.

Nguồn tĩnh không chứng minh hiện diện trực tiếp. Không tự chọn checkbox đề xuất; giữ lựa chọn khi polling thống kê, xóa khi đổi tệp/buổi. Dữ liệu CANDIDATE không biến thành VERIFIED vì người dùng tải ảnh lên. Nếu chưa có ngưỡng để từ chối unknown, UI gọi rõ là ứng viên chưa xác nhận; người ngoài lớp/không có mapping/bị policy từ chối là UNKNOWN, không ép thành sinh viên hợp lệ.

## 6. API và dữ liệu chuẩn hóa

Thêm:

- `POST /api/media/jobs` multipart: `file`, `source_type=snapshot|image|video`, `session_id`, `profile`.
- `GET /api/media/jobs/<id>`: trạng thái, progress, frame metadata và danh sách sinh viên duy nhất.
- `DELETE /api/media/jobs/<id>`: yêu cầu hủy; native inference đang chạy kết thúc tại điểm hủy an toàn.
- `POST /api/media/jobs/<id>/confirm`: JSON `confirmed=true`, `student_ids` là tập con đề xuất; chỉ người dùng duyệt mới được gọi ghi MANUAL.

Camera và media đều có kết quả chuẩn hóa: student_id/code/name, similarity, margin, verification_status, identity_verified, presence_verified, auto_attendance_eligible, face_id/track_id, bounding_box, source_type, recognized_at, source_timestamp_seconds và attendance_status. Phân biệt DETECTED/CANDIDATE/VERIFIED/UNKNOWN/ATTENDED. Trạng thái ATTENDED phản ánh bản ghi đã lưu của sinh viên ứng viên, không tự chứng minh khuôn mặt hiện tại đã xác minh. Các cờ identity/presence vẫn riêng biệt.

Giới hạn: ảnh 8 MiB và 20MP; video 64 MiB, 120 giây, ≤1080p, tối đa 60 frame suy luận, tối đa 2000 face rows. Một media job đang xử lý; lưu tối đa 10 job kết quả trong RAM, dọn job cũ khi tiếp nhận mới. Job không bền qua Flask restart, nhưng attendance đã commit giữ trong SQLite. File dùng tên ngẫu nhiên trong `.venv/media_uploads`, không dùng đường dẫn filename người dùng, kiểm tra chữ ký/decoder và giới hạn bytes. Release VideoCapture và xóa file khi hoàn thành/hủy/lỗi; file còn lại từ crash được dọn theo tuổi lúc startup. Các API khác vẫn giới hạn request 1 MiB.

## 7. Giao diện và kiểm thử

Thêm bộ chọn bốn nguồn, chọn buổi đã lưu, lọc danh sách Tất cả/Có mặt/Chưa ghi nhận/Vắng mặt. Camera lớn bên trái và thống kê/roster bên phải giữ nguyên. Media review chỉ xuất hiện trong mode tĩnh, không tạo card kết quả trùng cho camera. Đã sửa xung đột CSS `video{position:absolute}` để video tải lên nằm đúng panel; kiểm tra snapshot permission/cancel và phản hồi theo generation để không giữ nguồn cũ.

Đã chạy:

1. `python tests/upgrade_real_qa.py`: thành công; model và HTTP thật, 25 validation; JPEG/PNG/WEBP, một/đa mặt, blank/blur, file hỏng/size/path traversal, MP4/AVI, timestamps/dedup, video quá dài/hỏng/codec, hủy/giới hạn một job/dọn file, ngoài lớp, xác nhận batch có nguồn MANUAL và chống trùng. Browser chụp ảnh/tải ảnh/video, duyệt → 0/2 thành 1/2, filter và responsive; tạo lại Flask app giữ dữ liệu.
2. `python tests/upgrade_real_qa.py --ui-only`: thành công sau sửa UI; thêm kiểm tra checkbox không mất qua polling, WebM/VP8 phát/seek và canvas có nhãn, filename/metadata, vị trí video không absolute, responsive.
3. `python -m unittest tests.test_continuity_receipts tests.test_verified_attendance_storage`: 12 test đạt, gồm server continuity và transaction batch mới (rollback outsider, MANUAL, unique, buổi đóng).
4. Compile backend thành công; CLI audit dữ liệu xác minh cho thấy known calibration 25, các nhóm còn lại 0.
5. `python tests/upgrade_readonly_browser_smoke.py`: thành công trên trang dùng SQLite thật, chỉ thao tác đọc và mô phỏng lỗi quyền thiết bị (không mô phỏng AI). Kiểm tra buổi đã lưu, bốn mode, bộ lọc, không card kết quả trùng, từ chối quyền camera, đóng stream khi quyền trả về muộn sau đổi mode, video không phủ trang, responsive và không lỗi JavaScript. Hash database/gallery không đổi. Báo cáo `outputs/upgrade_readonly_smoke.json`.
6. Nhập lại một ảnh calibration đã có bằng `add-sample` bị từ chối đúng vì trùng hash; manifest không đổi. Công cụ cũng chặn ảnh registered làm calibration/holdout.

Các bài kiểm thử dùng SQLite bản sao tạm, ảnh thật và video dựng từ ảnh thật; AI không giả lập. Device camera của bài browser là MediaStream canvas ảnh dataset, **không phải webcam vật lý**. Không dùng các kết quả này để tuyên bố anti-spoofing hoặc tự động điểm danh trực tiếp đã đạt. Hash DATASET/gallery/config/policy và logical dump database thật trước/sau không đổi. Không ghi trạng thái điểm danh thật để phục vụ test.

Đã có các kiểm tra camera rời khung/đổi người/tắt giữa suy luận thật và protocol >2s ở `outputs/real_auto_attendance_trace.json` từ lượt trước; pipeline nhận diện không đổi. Không có dữ liệu unknown thật để kiểm tra người lạ mở rộng trong lượt này; không báo FAR bằng 0.

## 8. File sửa/thêm

Mới: `backend/media.py`, `backend/face_recognition/results.py`, `frontend/templates/_recognition_modes.html`, `_media_panel.html`, `frontend/static/js/media.js`, `tests/upgrade_real_qa.py`, báo cáo này.

Sửa: `backend/app.py`, `backend/config.py`, `backend/attendance/attendance_service.py`, `backend/face_recognition/verification_workflow.py`; `frontend/templates/index.html`, `frontend/static/css/style.css`, `frontend/static/js/attendance.js`; `tests/test_verified_attendance_storage.py`; `VERIFICATION_DATA_GUIDE.md`.

Không thêm dependency, không đổi model/weights/embedding, không migration schema SQLite, không thay đổi frontend quản lý lớp/sinh viên.

## 9. Demo và hạn chế còn lại

1. Mở `http://127.0.0.1:5055/attendance`, tải lại JS. Chọn lớp và buổi đã lưu hoặc tạo buổi mới. Chỉ thao tác điểm danh thật khi thực sự muốn lưu vào database thật.
2. Chọn Camera trực tiếp, bật camera và cho phép quyền. Khi có ứng viên hiện tên; hiện vẫn dùng xác nhận thủ công vì tự động khóa. Thử nhiều người, rời khung/đổi người; video phải chạy liên tục.
3. Chọn Chụp ảnh → Mở camera → Chụp ảnh. Đối chiếu khung/tên, tick sinh viên thực sự có mặt → Xác nhận danh sách. Dùng Mở camera chụp ảnh để chụp lại.
4. Chọn Tải ảnh → Chọn tệp → Phân tích → đối chiếu và duyệt. Không có mặt/ngoài lớp thì không có đề xuất hợp lệ. Kiểm tra progress/statistics và nguồn MANUAL sau lưu.
5. Chọn Tải video; ưu tiên MP4 H.264 hoặc WebM VP8 mà browser/máy hỗ trợ, ≤120s/64MiB/1080p. Bấm Phân tích, xem progress thực, chọn khung/timestamp, duyệt danh sách; thử Hủy. Người lặp lại chỉ có một mục sinh viên và một bản ghi/buổi.
6. Muốn bật tự động: bổ sung ảnh có nhãn độc lập theo `VERIFICATION_DATA_GUIDE.md`, dùng add-sample/audit/evaluate/publish/activate, restart Flask và kiểm thử webcam thật. Không bỏ qua ngưỡng hoặc dùng ảnh/video tải lên làm bằng chứng hiện diện.

Hạn chế: chưa đạt 1–2 giây với browser/camera thực; RAM máy thấp; model có warm-up đáng kể; codec phụ thuộc môi trường; lấy mẫu video có thể bỏ sót người xuất hiện ngắn; job sẽ mất khi restart; chưa kiểm thử webcam vật lý, chưa có ngưỡng unknown chính thức và chưa bật tự động. Mức độ hoàn thành: bốn phương thức và luồng duyệt/lưu đã triển khai và kiểm thử; tự động verified-live còn bị chặn bởi bằng chứng dữ liệu/presence còn thiếu.
