# Kết quả tối ưu camera — đo tại laptop, cổng 5055

Website: http://127.0.0.1:5055. Nhấn Ctrl+F5 để tải giao diện mới. Chọn cấu hình trước khi bật camera. Mặc định vẫn là RetinaFace tương thích; các lựa chọn nhanh hơn ghi rõ thử nghiệm. Không có cấu hình nào xác nhận danh tính hoặc ghi điểm danh.

## Kết luận

Chưa đạt trải nghiệm nhận diện thời gian thực với RetinaFace trên laptop này. RetinaFace là nút thắt, không phải so khớp 92 vector. DeepFace căn chỉnh bằng cách thêm biên lớn trước phát hiện, trong khi RetinaFace mặc định còn phóng ảnh nội bộ. Vì vậy chỉ giảm kích thước JPEG gửi lên không đảm bảo giảm đáng kể khối lượng phát hiện. Pipeline ban đầu đã giữ mô hình trong bộ nhớ; không có bằng chứng nạp lại mô hình mỗi frame hay phát hiện hai lần là nguyên nhân chính.

Máy có 8 CPU logic nhưng chỉ khoảng 3,84 GiB RAM khả dụng toàn hệ thống; lúc kiểm tra đã dùng khoảng 93%. Thời gian dao động lớn, phù hợp áp lực bộ nhớ/CPU nhưng chưa có phép đo page fault để khẳng định paging là nguyên nhân. Không coi một phép đo nhanh là bảo đảm cho camera thực tế.

## Số đo

Mẫu 640×480 gồm một khuôn mặt, hai khuôn mặt, ảnh trống tổng hợp, ảnh nhóm sáu khung. Trung bình/P95 bên dưới gộp các tình huống, không phải FPS video. Gốc và tái sử dụng có 4 mẫu/cấu hình; mỗi thử nghiệm mới có 12 mẫu/cấu hình. P95 mẫu nhỏ chỉ mang tính khảo sát. Các lần chạy khác thời điểm, có áp lực RAM khác nhau; không quy toàn bộ chênh lệch cho mã nguồn.

| Pipeline | Trung bình | P95 | Validation mới | Ảnh có đúng một khung /25 |
|---|---:|---:|---:|---:|
| Gốc RetinaFace | 21,69 s | 32,52 s | 25/25 | 25 |
| RetinaFace tái sử dụng, mặc định | 19,86 s | 30,82 s | 25/25 | 25 |
| RetinaFace không phóng ảnh, thử nghiệm | 5,03 s | 9,92 s | 25/25 | 25 |
| YuNet 640, confidence phát hiện 0,75, thử nghiệm | 1,26 s | 3,40 s | 23/25 | 25 |

RetinaFace không phóng ảnh dùng cùng trọng số nhưng thay đổi đầu vào detector: phát hiện trên ảnh chưa thêm biên, `allow_upscaling=False`, rồi dùng tọa độ dịch sang ảnh có biên để căn chỉnh. Không kế thừa kết quả validation cũ. Ở 640×360 kiểm thử mới 24/25; ở 320×240 là 19/25 và ảnh hai người chỉ còn một khung. Do đó không tự động chọn độ phân giải thấp. YuNet lần lượt đạt 22/25 và 18/25 ở hai kích thước nhỏ hơn. Đếm khung trên ảnh nhóm không phải recall người thật khi thiếu nhãn bounding box.

Đo HTTP một khuôn mặt đã làm nóng, 3 mẫu mỗi cấu hình:

| Pipeline | Trung bình round-trip | P95 |
|---|---:|---:|
| Gốc | 19,48 s | 20,46 s |
| Tái sử dụng | 15,03 s | 15,59 s |
| YuNet thử nghiệm | 0,834 s | 0,888 s |

Chuỗi HTTP YuNet một người, xen kẽ hai sinh viên và nghỉ 150 ms giữa yêu cầu đạt thực đo khoảng **1,01 lần nhận diện/giây** sau làm nóng. Đây không phải bảo đảm tốc độ/độ đúng cho camera; YuNet vẫn sai hai ảnh validation. Với 6 khuôn mặt thời gian khoảng 3,3–3,4 giây. RetinaFace native ở 640×480: một người 14,29 giây lần đầu rồi 4,54/3,79 giây; hai người 3,60–3,84 giây; ảnh trống 2,61–2,64 giây; sáu khung 6,12–6,34 giây.

Các giai đoạn trung bình ở 640×480, đơn vị ms:

| Pipeline | Giải mã | Phát hiện | Căn chỉnh/cắt | Embedding | So khớp |
|---|---:|---:|---:|---:|---:|
| Gốc | 23,25 | 18070,10 | 478,54 | 3096,93 | 19,49 |
| Tái sử dụng | 23,25 | 17242,19 | 386,88 | 2185,80 | 12,02 |
| RetinaFace native | 24,97 | 3425,89 | 317,59 | 1219,32 | 3,23 |
| YuNet thử nghiệm | 5,59 | 71,76 | 220,71 | 964,80 | 0,87 |

Giải mã hai pipeline offline dùng cùng ảnh đã giải mã; thời gian HTTP thực được ghi riêng. HTTP header `Server-Timing` tách serialization, server_total và các giai đoạn; JSON round-trip/client overhead lưu ở `http_qa.json`. Tổng native bao gồm HTTP, tổng offline không bao gồm HTTP. Alignment gồm cấp phát biên/crop/chuẩn hóa; embedding gồm resize và ArcFace forward, không cộng thêm `arcface_forward_ms` lần thứ hai.

Server hiện tại khởi động 42,92 giây, trong đó làm nóng ArcFace 0,849 giây, `model_load_count=1` sau 111 lượt inference. Đây là thời gian khởi động, không cộng vào mỗi frame. Lần gọi RetinaFace đầu tiên vẫn có chi phí khởi tạo graph riêng; xem từng mẫu và các startup JSON. Dữ liệu benchmark giữ lại cả mẫu chậm, không loại để làm đẹp báo cáo.

## Thay đổi mã nguồn

- `backend/face_recognition/camera_pipeline.py`: một lần phát hiện, tái sử dụng khuôn mặt đã căn chỉnh, gọi ArcFace đã nạp sẵn; không dùng skip trên khung ảnh thô. Chín đối chiếu ở ba độ phân giải cho vector tương đương pipeline gốc, sai lệch thành phần tối đa 0. Đây là kiểm tra mẫu, không chứng minh mọi ảnh.
- `runtime_service.py`: tải gallery/mô hình một lần, làm nóng ArcFace, đo từng giai đoạn, hỗ trợ profile thử nghiệm riêng. Tất cả profile thử nghiệm vô hiệu xác nhận danh tính, không kế thừa ngưỡng.
- `backend/app.py`, `backend/camera_profiles.json`: chọn profile theo phiên camera, trả timing và header HTTP; cấu hình đăng ký gốc không đổi.
- Frontend HTML/CSS/JavaScript: chọn profile và kích thước, giữ tỷ lệ, chỉ một request đang chạy, chụp frame mới nhất sau khi request trước xong; khoảng nghỉ 150 ms. Video vẫn liên tục. Xóa ứng viên cũ khi bắt đầu lần xử lý tiếp, tách khung đã xử lý khỏi video hiện tại. Dừng camera hủy request/session, dừng tracks, xóa kết quả và bỏ qua response muộn. Không thêm tracking chưa kiểm chứng.
- Các script kiểm thử/benchmark trong `tests/`; cách chạy: `.venv\Scripts\python.exe tests/summarize_camera_optimization.py`. Chỉ tổng hợp kết quả đã đo, không suy luận lại mô hình.

## Độ tin cậy và kiểm thử

8 kiểm thử API đạt; 9 kiểm tra giao diện với camera giả lập đạt: video tiếp tục, một request, giới hạn kích thước, chuyển người thay ứng viên, không mặt không hiện tên, dừng bỏ kết quả muộn, không lỗi JavaScript. HTTP thực dùng mô hình thật đã chạy ảnh một/hai/nhiều/không khuôn mặt và thay đổi ảnh sinh viên. Chưa kiểm thử có người thật lần lượt đứng trước laptop trong đợt tối ưu này; camera giả lập không thay thế kiểm thử đó.

YuNet thử nghiệm nhầm `validation/Bùi Kiều Linh/Bùi Kiều Linh_02.jpg` và `validation/Nhi/Nhi_15.jpg` sang ứng viên Thu Thảo ở 640×480. Hai thử nghiệm YuNet confidence 0,9 và bản 960 kém hơn/thiếu khuôn mặt đã bị vô hiệu trên giao diện. Confidence phát hiện 0,75 không phải ngưỡng cosine nhận diện. Không có nhãn người lạ để xác lập từ chối tin cậy. 25 ảnh đóng chỉ đánh giá Top-1 trong năm người đăng ký, không chứng minh an toàn điểm danh.

Đã đối chiếu SHA-256 của **174 file gốc**: DATASET, 92 embedding, cấu hình, ngưỡng và kết quả kiểm thử cũ không thay đổi. Không lưu ảnh camera. Không bổ sung dữ liệu Test/validation vào gallery.

## Báo cáo chi tiết

Các file mới nằm riêng trong `outputs/camera_optimization/`: `performance_summary.json`, `performance_benchmark.csv`, `validation_comparison.csv`, `http_qa.json`, `benchmark_native_http.json`, `validation_native_http.json`, các benchmark/validation/startup JSON và báo cáo browser QA. Thư mục outputs đã nằm trong .gitignore. Không đưa các báo cáo chứa nhãn sinh viên hoặc gallery lên GitHub công khai.

Muốn tăng tốc mà giữ pipeline RetinaFace đầy đủ cần thêm tài nguyên phần cứng hoặc môi trường GPU tương thích được đo lại; giảm ảnh hoặc đổi detector đơn thuần đã cho thấy đánh đổi độ đúng. Website hiện phục vụ thử nghiệm liên tục, chưa đạt nhận diện thời gian thực đáng tin cậy để tự động điểm danh.
