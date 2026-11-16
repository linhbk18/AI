# Hoàn tất nhãn và thu dữ liệu xác minh độc lập

Tự động điểm danh tiếp tục khóa. Không dùng ngưỡng khảo sát như policy chính thức.

## 1. Hoàn tất Test

Mở http://127.0.0.1:5055/test-labeling. Chọn bộ lọc **Chưa gắn nhãn**; các nút ảnh ghi số khung còn thiếu (bao gồm UNKNOWN thiếu mã). Bấm khung trên ảnh để đi tới ô nhãn. Dùng **Lưu và đến ảnh tiếp theo**; thay đổi chỉ lưu khi bấm nút, không tự điền dự đoán.

Với UNKNOWN, mở **Đối chiếu người lạ giữa các ảnh**. Bấm ảnh nhỏ để mở đúng khung và nhập mã U01, U02… Dùng cùng mã cho cùng người ở mọi ảnh, khác mã cho người khác; danh sách gợi ý lấy từ mã bạn đã lưu. Mã gồm tối đa 64 chữ/số, chấm, gạch dưới, gạch ngang. Không dùng tên ứng viên AI làm nhãn thật. Nếu không xác định được người, dùng IGNORE thay vì đoán hoặc tạo mã giả để đủ số liệu.

Hoàn tất 50 khung còn thiếu và 30 UNKNOWN thiếu mã hiện tại. Bấm **Đánh giá nhãn đã lưu** để cập nhật kết quả. Để ép chạy lại mô hình thật thay cho cache, dùng POST /api/test-labels/evaluate với JSON `{"force":true}` và token trang. Sau mỗi đánh giá, analysis.json, per_face_decisions.csv và proposed_threshold.json được cập nhật; proposal luôn vô hiệu. Không tự chuyển dữ liệu này thành holdout.

## 2. Chốt kế hoạch trước khi chụp holdout

- Calibration: ảnh webcam mới của cả 5 sinh viên và người chưa đăng ký để chọn pipeline/ngưỡng. Có thể tận dụng Test có nhãn/mã UNKNOWN để chẩn đoán, nhưng Test đã được xem trước.
- Evaluation: ảnh webcam mới độc lập, đủ cả 5 sinh viên; người lạ khác những người đã dùng ở calibration/Test. Giữ mã người nhất quán để kiểm tra phân tách.
- Thu ở nhiều phiên thực tế, điều kiện ánh sáng, góc nhìn, khoảng cách đại diện cho cách dùng. Không chỉ chọn ảnh đẹp hoặc chỉ giữ ca thành công. Mỗi ảnh chỉ một người, tránh ảnh nhóm cho workflow holdout hiện tại.
- Một ảnh/người/phiên độc lập. Đổi mã phiên cho các frame liên tiếp không tạo ra độc lập thống kê. Ảnh lặp cùng người/phiên không được tính như nhiều thử nghiệm độc lập.
- Chọn trước mục tiêu FAR (nhận người lạ), FRR (từ chối sinh viên), wrong-known (nhận nhầm sinh viên) phù hợp sử dụng. Ví dụ 0 lỗi/299 thử nghiệm UNKNOWN độc lập mới đưa cận trên một phía 95% xuống dưới 1%; không phải 299 frame liên tiếp. Chưa có yêu cầu rủi ro được duyệt nên không mặc định các mục tiêu thành tiêu chuẩn triển khai.

## 3. Thu ảnh

Đóng camera website để nhường thiết bị, chạy từ thư mục dự án:

```powershell
.\.venv\Scripts\python.exe -m backend.face_recognition.collect_webcam_validation --identity DEMO-SV001 --split calibration --capture-session linh_visit_001
.\.venv\Scripts\python.exe -m backend.face_recognition.collect_webcam_validation --identity UNKNOWN:U01 --split calibration --capture-session unknown_visit_001
.\.venv\Scripts\python.exe -m backend.face_recognition.collect_webcam_validation --identity DEMO-SV001 --split evaluation --capture-session linh_visit_002
.\.venv\Scripts\python.exe -m backend.face_recognition.collect_webcam_validation --identity UNKNOWN:NEW001 --split evaluation --capture-session new_person_visit_001
```

Thay mã sinh viên theo SQLite và mã phiên thực tế. SPACE lưu một ảnh; Q/ESC hủy. Lặp cho đủ 5 sinh viên/người lạ/phiên theo kế hoạch. Công cụ lưu ảnh riêng trong outputs/verification_data và thêm tham chiếu vào manifest.csv, không thêm gallery hay điểm danh. Nếu đăng ký manifest thất bại, ảnh vẫn giữ lại và Terminal báo đường dẫn để sửa nhãn, không tự xóa. Công cụ chưa được thử với webcam vật lý trong lượt này.

## 4. Đánh giá cuối và bảo vệ holdout

```powershell
.\.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow audit
.\.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow evaluate --help
```

Chạy evaluate với ba tham số bắt buộc `--max-far`, `--max-frr`, `--max-wrong` theo kế hoạch đã chọn; profile phải là profile camera đã chốt. Nếu dùng `--group-calibration outputs/test_labeling/evaluation.json`, phải gán đầy đủ mã UNKNOWN và khớp chữ ký gallery/profile/nhãn thật hiện tại.

Workflow tính calibration trước, chọn ngưỡng chỉ trên calibration, lưu và khóa trial trước khi chạy bất kỳ inference holdout nào. Registry ghi hash ảnh, profile, chữ ký pipeline, ngưỡng và mục tiêu; chặn đổi cấu hình, đổi nhãn hoặc lấy tập con các ảnh đã lộ. Không chỉnh registry để chạy lại cấu hình khác. Nếu holdout thất bại, ghi nhận kết quả, cải thiện trên calibration và thu holdout mới. Độc lập thực tế của người/phiên vẫn cần người thu xác nhận; hash không phát hiện được mọi ảnh gần giống/cùng người đổi mã.

Không dùng `--publish`/activate cho đến khi có kết quả đủ điều kiện và được xem xét. Workflow kiểm tra cận tin cậy, đủ dữ liệu, tính độc lập được khai báo, hash, profile và gallery trước khi xuất policy. Publish không đồng nghĩa có quyền bỏ qua dữ liệu thiếu. Không có lệnh nào được chạy để bật tự động trong lượt này.

## 5. Chẩn đoán và kiểm thử

Xem outputs/test_labeling/verification_analysis/diagnostics/cases.html và cases.csv để kiểm tra từng ca sai/bị từ chối. Các crop chỉ là bản sao đầu ra. JSON có bbox, mắt, góc xoay, kích thước crop, tensor, độ nét đo được, similarity từng sinh viên và nguồn gallery gần nhất; không coi số đo độ nét là bằng chứng nguyên nhân duy nhất.

Sau khi hoàn tất nhãn, đánh giá lại trước; chạy công cụ diagnose_labelled_faces khi Flask đã dừng để tránh hai tiến trình TensorFlow chiếm RAM. Công cụ thử riêng các profile trên Test, không thay camera/policy/gallery. Phải đánh giá webcam lại nếu thay pipeline; không chuyển thành tích calibration thành accuracy triển khai.

Nhánh VERIFIED → Attendance Service → SQLite có kiểm thử điều kiện, idempotency, buổi đóng, người ngoài lớp, track mất dấu và tính bền vững trên DB tạm. API/browser thật kiểm tra ứng viên không tự ghi, xác nhận thủ công và cập nhật thống kê; sự kiện auto frontend là kiểm thử hợp đồng giao diện, chưa chứng minh ca auto camera dương khi policy đang khóa. Khi đủ policy chính thức, cần kiểm thử webcam vật lý với cả người trong lớp, ngoài lớp, UNKNOWN và đổi người.
