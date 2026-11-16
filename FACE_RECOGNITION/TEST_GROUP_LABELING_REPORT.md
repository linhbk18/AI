# Tận dụng DATASET/Test để gắn nhãn và đánh giá xác minh

## Cấu trúc thực tế

Đường dẫn đúng là `F:\FACE_RECOGNITION\DATASET\Test` (Test viết hoa). Có 9 file JPEG, không có thư mục con hay thư mục rỗng bên trong. Không tạo `dataset/test` mới và không đổi tên.

```
DATASET/Test/
  3.jpg
  4.jpg
  Group01.jpg
  Group10.jpg
  Group11.jpg
  Group12.jpg
  Group3.jpg
  Group5.jpg
  Group9.jpg
```

## Đếm mới bằng RetinaFace thật

Model và weights giữ nguyên. Chuẩn bị annotation dùng native RetinaFace, allow_upscaling=False, cạnh dài tối đa 1280 để giảm nguy cơ thiếu RAM. Chạy thêm cấu hình camera hiện tại: input giữ tỷ lệ trong 640×480, detector RetinaFace 480. Đây là **khung phát hiện**, chưa phải số người thật. Có thể có false positive; người dùng cần chọn IGNORE hoặc thêm mặt bị bỏ sót.

| Ảnh | Kích thước gốc | Khung để gắn nhãn | Khung ở pipeline camera | Số lịch sử |
|---|---|---:|---:|---:|
| 3.jpg | 1280×720 | 5 | 5 | 5 |
| 4.jpg | 5120×2880 | 22 | 8 | 21 |
| Group01.jpg | 1280×960 | 6 | 6 | 6 |
| Group10.jpg | 1276×956 | 2 | 2 | 2 |
| Group11.jpg | 2048×1365 | 12 | 12 | 12 |
| Group12.jpg | 2048×1365 | 43 | 19 | 35 |
| Group3.jpg | 2048×1536 | 7 | 7 | 7 |
| Group5.jpg | 1148×2048 | 3 | 3 | 3 |
| Group9.jpg | 2048×1536 | 3 | 3 | 3 |
| **Tổng** | | **103** | **65** | **94** |

Không lấy chênh lệch 103–65 làm số mặt bỏ sót đã xác minh, vì các khung chưa được adjudicate. Số lịch sử 94 thuộc cấu hình/resize cũ; ví dụ 4.jpg từng MemoryError rồi chạy bản 2048×1152. Không tái sử dụng ID/tọa độ từ bản vẽ cũ sang bộ mới.

## Công cụ đã triển khai

Mở **http://127.0.0.1:5055/test-labeling** hoặc liên kết trên trang Điểm danh AI. Mỗi ảnh có khung và số; crop tương ứng, chọn đúng mã sinh viên, UNKNOWN hoặc IGNORE. Mặc định toàn bộ 103 khung là UNLABELED; không có tên AI gợi ý hay prefill. UNKNOWN dùng khi biết người đó chưa đăng ký; không biết thật là ai thì IGNORE. Có mã người lạ ẩn danh để nhận biết lặp người giữa ảnh khi người dùng biết, không tự ghép theo AI.

Có lưu JSON/CSV với revision chống ghi đè, ghi chú, source hash, face ID và nguồn nhãn human; thêm khung tay và cập nhật bản số khi detector bỏ sót. Bản ảnh chỉ chứa số và khung, không dùng overlay tên AI cũ. Tất cả nằm riêng trong `outputs/test_labeling/`:

- `manifest.json`: ảnh, geometry, cấu hình detector, nguồn và audit lịch sử.
- `assets/*-numbered.jpg`: 9 bản sao đánh số; bản plain tương ứng dùng crop/ảnh nền.
- `labels.json`, `labels.csv`: nhãn người dùng; **hiện chưa có nhãn thật**, tất cả UNLABELED.
- `preparation_check.json`, `browser_check.json`, `initial_readiness.json`: kiểm tra chuẩn bị, UI và tình trạng dữ liệu lúc bàn giao.

Trang không ghi điểm danh, không đăng ký khuôn mặt, không ghi SQLite. UNKNOWN tuyệt đối không được thêm vào gallery.

## Lịch sử sử dụng và tính độc lập

`outputs/test_summary.json`, `test_report.csv`, `test_manual_review.md` cho thấy tất cả 9 ảnh đã chạy và xem dự đoán. Cột ground_truth trong báo cáo cũ có **0 nhãn thật**. Group12 còn đã được xem xét do nhiều mặt cùng ứng viên Thu Thảo; đó không phải ground truth.

Bằng chứng rõ về pipeline probes: `tests/benchmark_camera_optimization.py`, `benchmark_native_http.py`, `realtime_camera_benchmark.py` dùng Group10 làm ảnh hai mặt và Group01 làm ảnh nhiều mặt. Group10 còn xuất hiện trong các kiểm thử media/camera. Danh sách tham chiếu đường dẫn cụ thể lưu trong `manifest.json/provenance`.

Không có bằng chứng đủ để khẳng định Test từng được fit threshold bằng nhãn known/unknown, vì nhãn thật chưa có. Tuy nhiên cũng không có bằng chứng Test còn là holdout độc lập: đã xem dự đoán và dùng một phần cho quyết định pipeline. **Không tự coi 9 ảnh là tập đánh giá chính thức mới.** Có thể dùng nhãn Test cho calibration/chẩn đoán, giữ một holdout mới độc lập cho quyết định bật tự động.

## Đánh giá sau khi bạn gắn nhãn

Nút “Đánh giá nhãn đã lưu” dùng cùng model đã nạp trong Flask, không nạp một bản model nữa. Nó chạy RetinaFace + ArcFace trên toàn cảnh theo profile camera thực tế; ghép duy nhất từng mặt với annotation, không đoán khi mơ hồ. Tính similarity tối đa từng sinh viên, Top-2 khác danh tính, margin, similarity đúng người/khác người, known đúng/sai và mặt không ghép được. Lưu `evaluation.json`, `evaluation.csv` và `threshold_sweep.csv` cho các cặp ngưỡng dựa trên điểm quan sát; FAR/FRR chỉ có ý nghĩa khi có nhãn phù hợp và phải báo rõ lỗi detector/ghép khung.

Chưa chạy ra số FAR/FRR hoặc chọn ngưỡng vì chưa có nhãn người dùng. Không tự gán dự đoán làm nhãn để tạo kết quả đẹp. API từ chối đánh giá khi không có ground truth; nút đánh giá cũng khóa cho đến khi có known/UNKNOWN đã lưu.

Đã nối báo cáo nhãn nhóm vào quy trình hiệu chỉnh hiện có bằng `--group-calibration outputs/test_labeling/evaluation.json`. Nó kiểm tra hash nhãn/ảnh/manifest, signature gallery và profile, chỉ nhập group scores làm **calibration**; không đưa Test vào holdout. UNKNOWN cần mã người ẩn danh để kiểm tra không trùng người với unknown holdout. Ảnh không phát hiện/không ghép được vẫn tính là lỗi vận hành, không âm thầm bỏ.

## Chính xác còn thiếu

- Cần bạn gắn nhãn **103 khung hiện có**, cộng thêm các mặt bị detector bỏ sót nếu có. Chưa thể nói số người lạ thật trước khi có nhãn; 0 UNKNOWN đã lưu không có nghĩa là ảnh không có người lạ.
- Có 25 known validation dùng calibration. Test sau gắn nhãn bổ sung được known/unknown calibration; không cần thu lại toàn bộ từ đầu. Số unknown calibration còn thiếu không có một hằng số chung và chưa tính được trước khi phân loại Test, phụ thuộc độ phủ/tỷ lệ lỗi chấp nhận.
- Hiện có **0 known và 0 unknown holdout được chứng minh độc lập**. Ví dụ nếu người vận hành chọn FAR ≤1%, wrong-known ≤5%, cận trên một phía 95% và 0 lỗi trong các thử nghiệm độc lập: cần ít nhất **299 lượt unknown** và **59 lượt known**, có thể bố trí 60 known/12 mỗi sinh viên. Đây là kịch bản minh họa, không phải mục tiêu/threshold chính thức do hệ thống tự áp đặt. Người unknown holdout không được trùng nhóm Test dùng calibration; không lấy các frame liền nhau làm thử nghiệm độc lập.
- Nếu có dữ liệu độc lập sẵn ở nơi khác, có thể nhập thay vì chụp lại; vẫn phải chứng minh nhãn, split và tính độc lập. Nếu kết quả không đạt, cần cải thiện pipeline/dữ liệu rồi dùng holdout mới, không tối ưu trên cùng holdout.

Chưa tạo threshold mới, không bật tự động. Quy trình publish/activate chỉ hoạt động sau calibration + holdout độc lập đạt điều kiện và người vận hành phê duyệt.

## Kiểm thử và bảo toàn

- Đã chuẩn bị cả 9 ảnh bằng RetinaFace thật; 103 bản ghi khung và 9 ảnh đánh số. Không dùng cache dự đoán cũ làm nhãn.
- Kiểm tra hash toàn bộ DATASET, face_embeddings.json, face_config.json, recognition_threshold.json và logical dump SQLite trước/sau chuẩn bị: không đổi.
- `tests/test_group_labeling.py`: 4 test đạt trên bản sao tạm, gồm không prefill, lưu IGNORE JSON/CSV, revision conflict/invalid batch, thêm khung và cập nhật ảnh số. Lựa chọn trong test chỉ là fixture giao diện/lưu trữ, không được dùng như ground truth Test.
- `tests/group_labeling_browser.py`: chạy đạt trên trang thật, đọc 9 ảnh, 43 hàng Group12, các lựa chọn mã/UNKNOWN/IGNORE, không prefill, responsive, không lỗi JavaScript; không lưu nhãn giả vào bộ thật.
- Chạy cùng tests continuity/storage: tổng 16 test đạt. Compile backend thành công.

File mã mới: `backend/test_labeling.py`, `frontend/templates/test_labeling.html`, `frontend/static/js/test_labeling.js`, hai file test. Sửa `backend/app.py`, `frontend/templates/_recognition_modes.html`, `backend/face_recognition/verification_workflow.py`, `backend/attendance/automatic.py` để nối calibration nhóm và ràng buộc hash nhãn khi kích hoạt. Không thay ảnh, gallery, model, chức năng quản lý hoặc schema SQLite.

Hướng dẫn chi tiết: **TEST_GROUP_LABELING_GUIDE.md**; quy trình holdout/kích hoạt: **VERIFICATION_DATA_GUIDE.md**. Phần còn chờ người dùng là nhãn thật và bằng chứng holdout; không tuyên bố khả năng từ chối người lạ hoặc tự động điểm danh đã được kiểm chứng.
