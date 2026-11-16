# Bổ sung dữ liệu thật và kích hoạt tự động điểm danh

Hiện đã có `outputs/verification_data/manifest.csv` tham chiếu đúng 25 ảnh validation với mã sinh viên trong SQLite, **chỉ dùng hiệu chỉnh**. Không di chuyển, sao chép hoặc sửa ảnh gốc. Chưa có ảnh người lạ và ảnh đánh giá độc lập. Không có ngưỡng chính thức mới; tự động vẫn khóa.

## Thu thập

Xin sự đồng ý của người tham gia; giữ dữ liệu trong `outputs` đã được loại khỏi Git. Dùng ảnh camera laptop ở khoảng cách, ánh sáng và góc nhìn tương tự buổi demo, một khuôn mặt mỗi ảnh. Không lấy ảnh registered, validation, Test hoặc các ảnh trùng để làm holdout.

| Nhóm | Đã có | Kế hoạch bổ sung thực tế |
|---|---:|---|
| Known hiệu chỉnh | 25 ảnh / 5 sinh viên | Có thể bổ sung thêm tình huống khó |
| Unknown hiệu chỉnh | 0 | Ví dụ 100 lượt chụp của ít nhất 5–10 người chưa đăng ký |
| Known đánh giá độc lập | 0 | Ví dụ 60 lượt chụp mới, 12 lượt mỗi sinh viên |
| Unknown đánh giá độc lập | 0 | Ít nhất 299 lượt độc lập nếu muốn FAR ≤1% với cận trên một phía 95% và không có lỗi nhận nhầm; trải trên nhiều người, ví dụ ít nhất 20 người khác nhóm hiệu chỉnh |

Đây là kế hoạch thu thập, không phải bảo đảm đạt chất lượng. Không tính 299 frame liền nhau thành 299 thử nghiệm độc lập. Nhóm unknown đánh giá phải là **người khác** nhóm unknown hiệu chỉnh. Known có thể cùng 5 sinh viên nhưng khác buổi/lượt chụp. Chỉ một ảnh cho mỗi người trong mỗi lượt đánh giá độc lập. Thay đổi môi trường, khoảng cách, kính, góc mặt; không chọn riêng các ảnh có similarity đẹp.

Giới hạn tỷ lệ lỗi phải do người vận hành xác định theo mức rủi ro chấp nhận được. Ví dụ mục tiêu FAR 1%, từ chối nhầm 10%, nhầm danh tính known 5%: với 0 lỗi, 60 known và 299 unknown holdout đủ cho kiểm tra cận trên tương ứng, nhưng vẫn phụ thuộc tính độc lập và độ đại diện của dữ liệu. Không có số similarity/margin đặt sẵn.

Lưu ảnh mới vào:

```
outputs/verification_data/known/calibration/
outputs/verification_data/known/evaluation/
outputs/verification_data/unknown/calibration/
outputs/verification_data/unknown/evaluation/
```

Thêm dòng vào CSV, giữ đúng cột:

```
source,identity,split,capture_session
```

Có thể nhập nhãn bằng lệnh (thay đường dẫn bằng ảnh thật đã chụp; công cụ không tạo ảnh và không thêm vào gallery):

```powershell
.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow add-sample --source "outputs/verification_data/unknown/calibration/u01.jpg" --identity "UNKNOWN:U01" --split calibration --capture-session "u01_visit01"
```

Lệnh kiểm tra ảnh có thật, hash trùng và tách split trước khi cập nhật CSV; nếu sai, manifest cũ được giữ nguyên.

`source` là đường dẫn ảnh thật (tương đối từ gốc dự án hoặc tuyệt đối). `identity` của known là mã thật, ví dụ `DEMO-SV001`; unknown dùng mã người ẩn danh như `UNKNOWN:U01`, tuyệt đối không dùng một mã sinh viên để dán nhãn người lạ. `split` là `calibration` hoặc `evaluation`. `capture_session` là mã lượt chụp, không được xuất hiện ở cả hai split. Mỗi người unknown chỉ được thuộc một split. Không tạo dòng cho ảnh chưa chụp. Công cụ kiểm tra tồn tại file, hash trùng, trộn split và trùng lượt đánh giá.

## Kiểm tra, đánh giá và bật — không sửa mã nguồn

1. Kiểm tra dữ liệu:

```powershell
.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow audit
```

2. Dừng Flask trong Terminal để tránh hai bản TensorFlow tranh RAM. Chạy đánh giá với **các mục tiêu đã được bạn chấp nhận**. Ví dụ dưới đây chỉ minh họa mục tiêu tỷ lệ lỗi, không phải ngưỡng cosine:

```powershell
.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow evaluate --max-far 0.01 --max-frr 0.10 --max-wrong 0.05
```

Công cụ chạy ArcFace/RetinaFace thật, dùng gallery runtime SQLite hiện tại; kiểm tra Top-2 là hai sinh viên khác nhau. Chỉ tìm similarity/margin trên calibration. Holdout dùng đánh giá một lần với cận trên nhị thức 95%; không dùng holdout tối ưu ngưỡng. Cấu hình đã thử với holdout được ghi registry để chặn dùng lại holdout với ngưỡng/pipeline khác. Nếu thất bại, cần holdout mới trước lần điều chỉnh tiếp theo, không đổi nhãn/xóa ảnh lỗi để làm đẹp kết quả.

3. Xem `outputs/verification_workflow_report.json`. Nếu kết quả đạt và bạn phê duyệt dùng tự động, xuất kết quả đã đánh giá; không chạy lại hoặc tinh chỉnh trên holdout:

```powershell
.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow publish
```

Chỉ khi đạt mới xuất `automatic_attendance_evaluation.json` và policy chính thức `recognition_threshold.json`. Báo cáo chứa metadata model, detector/profile, tiền xử lý, package/weights, signature gallery runtime, hash dữ liệu và metric. Không sửa `face_embeddings.json` hoặc tạo embedding đăng ký mới. Sai hash/profile/gallery hoặc thiếu chứng cứ sẽ khóa lại.

4. Kích hoạt cấu hình đã kiểm chứng:

```powershell
.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow activate
.venv\Scripts\python.exe -m backend.app
```

Lệnh activate tự kiểm tra policy và dữ liệu; không được kích hoạt khi thiếu điều kiện. Nó lưu `outputs/automation_activation.json`, không yêu cầu sửa `config.py`. Tải lại trình duyệt. Profile mặc định của các lệnh là `retinaface_scaled_experimental`, đúng tùy chọn hiện tại trên trang; nếu dùng profile khác phải đánh giá/publish/activate với cùng `--profile` rồi chọn đúng profile trên trang.

5. Mở `/api/attendance/automation-status?profile=retinaface_scaled_experimental`; chỉ khi `ready=true` mới kiểm thử tự động trên webcam: mở buổi hai người, bật camera, cho từng sinh viên vào/ra và thử người ngoài lớp/người lạ. Backend cấp receipt có giới hạn cho track đã xác minh; frontend tự commit; SQLite phải lưu AI_VERIFIED và thời gian, thống kê phải 0/2 → 1/2, tiếp tục đứng không được ghi trùng. Thử rời khung trước khi AI xong và tắt/bật camera: kết quả cũ không được ghi. Các bài kiểm tra ảnh/API không thay thế bước webcam này.

6. Tắt ngay nếu cần:

```powershell
.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow deactivate
```

Cờ tắt được đọc lúc kiểm tra/commit, không cần sửa mã. Xác nhận thủ công vẫn có thể dùng làm dự phòng.
