# Gắn nhãn và tận dụng ảnh nhóm Test

Mở **http://127.0.0.1:5055/test-labeling**. Có thể vào từ liên kết “Gắn nhãn ảnh nhóm Test” trên trang Điểm danh AI.

## Cách gắn nhãn

1. Chọn ảnh. Ảnh chỉ có khung và số thứ tự, **không có tên AI dự đoán**. Bên phải là crop từng mặt tương ứng.
2. Nếu chắc chắn đây là một trong 5 sinh viên đã đăng ký, chọn đúng mã SQLite.
3. Chỉ chọn **UNKNOWN** khi bạn biết người đó chưa đăng ký. Nếu không biết có phải sinh viên đã đăng ký hay không, mặt quá mờ/che hoặc không phải mặt thật, chọn **IGNORE** và ghi chú. Không dùng điểm cosine để quyết định nhãn thật.
4. Với UNKNOWN, khi có thể nhận biết cùng người xuất hiện ở nhiều ảnh, nhập cùng mã ẩn danh, ví dụ U01. Không lấy tên hoặc ghép người theo dự đoán AI. Nếu không xác định được nhóm người, để trống; vẫn dùng được để chẩn đoán, nhưng chưa dùng để chứng minh tách người giữa calibration/holdout.
5. Lưu nhãn ảnh này trước khi đổi ảnh. Nhãn lưu riêng trong `outputs/test_labeling/labels.json` và `labels.csv`. Có kiểm tra revision để tránh hai cửa sổ ghi đè nhau.
6. Rà toàn ảnh, không chỉ các khung có sẵn. Nếu thiếu mặt, nhấn “Thêm khung bị bỏ sót”, kéo quanh mặt trên ảnh rồi chọn/lưu nhãn. Bản tải có số được cập nhật; ảnh nguồn không đổi.
7. Dùng “Tải bản có số” để xem hoặc chia sẻ riêng tư bản annotation. Các file nằm trong `outputs/test_labeling/assets`, được gitignore cùng outputs.

Hiện RetinaFace ảnh nhóm (cạnh dài tối đa 1280, native/no upscaling) tạo 103 khung trên 9 ảnh. Một số có thể sai hoặc trùng; không coi 103 là số người thật. Profile camera hiện tại (ảnh tối đa 640×480, RetinaFace detector 480) trả 65 khung; cần nhãn thật để phân biệt mặt bỏ sót, vùng giả và ghép sai. Số lịch sử 94 thuộc lần chạy khác, không dùng nguyên tọa độ/ID cũ cho bộ mới.

## Đánh giá sau khi có nhãn thật

Tắt camera/video nặng rồi bấm “Đánh giá nhãn đã lưu”. Công cụ:

- Chạy pipeline RetinaFace + ArcFace hiện tại trên toàn cảnh thu nhỏ đúng giới hạn camera, **không tự crop mặt rồi coi là cùng pipeline**.
- Ghép một-một mặt phát hiện với khung đã gắn nhãn; ghép mơ hồ không được đoán, được thống kê cùng các mặt không ghép được.
- Tính similarity tối đa theo từng sinh viên, Top-2 là hai mã sinh viên khác nhau, margin và điểm same-person/different-person theo nhãn người dùng.
- Lưu `evaluation.json`, `evaluation.csv`, `threshold_sweep.csv` và cache điểm theo source hash + signature gallery + profile. Cache chỉ chứa điểm, không thêm embedding vào gallery. Thay nhãn phải đánh giá lại trước khi dùng cho hiệu chỉnh chính thức.
- IGNORE và UNLABELED không tham gia tính identity metrics. Nếu mới gắn một phần, kết quả chỉ phản ánh phần đó. UNKNOWN thiếu dữ liệu hoặc chưa có ngưỡng thì FAR không được công bố như kết quả chính thức.

Trang gắn nhãn không tự trình bày gợi ý AI cạnh lựa chọn để tránh thiên lệch. Xem các báo cáo riêng sau khi đã chốt nhãn.

## Tận dụng Test cho calibration, không gom vào holdout

Toàn bộ 9 ảnh đã có dự đoán ở `outputs/test_report.csv` và báo cáo kiểm tra thủ công cũ. Group01/Group10 còn được dùng trong benchmark/tối ưu. Do đó không có bằng chứng để xác nhận Test là holdout hoàn toàn độc lập. Bộ nhãn mới rất hữu ích cho **calibration/chẩn đoán**, nhưng không tự biến Test thành tập đánh giá độc lập chỉ vì nhãn vừa được bổ sung.

Sau khi gắn nhãn, mã người UNKNOWN nhất quán và đánh giá xong, dùng thêm báo cáo nhóm làm calibration:

```powershell
.venv\Scripts\python.exe -m backend.face_recognition.verification_workflow evaluate --group-calibration outputs/test_labeling/evaluation.json --max-far 0.01 --max-frr 0.10 --max-wrong 0.05
```

Các tỷ lệ trên **chỉ là ví dụ mục tiêu rủi ro do người vận hành lựa chọn**, không phải similarity/margin đặt sẵn. Không dùng lại một người UNKNOWN từ Test calibration trong holdout unknown. Ảnh nhóm không bị cắt để giả lập ảnh đăng ký; công cụ dùng điểm đúng pipeline camera đã tính, kèm lỗi không phát hiện/ghép được.

Công cụ kiểm tra hash nhãn, manifest, ảnh nguồn, signature runtime và profile trước khi nhập group calibration. Holdout vẫn phải là ảnh/phiên/người độc lập trong `outputs/verification_data/manifest.csv` theo `VERIFICATION_DATA_GUIDE.md`. Nếu thiếu một loại dữ liệu hoặc không đạt confidence bounds, không publish/activate.

## Chính xác còn thiếu gì lúc bàn giao?

- Có 103 khung cần bạn gắn nhãn, hiện tất cả UNLABELED; chưa biết bao nhiêu là registered/UNKNOWN/IGNORE. Không thể trung thực nói Test có chính xác bao nhiêu người lạ trước khi bạn phân loại.
- Known calibration đã có 25 ảnh validation. Sau gắn nhãn, các mặt known/unknown của Test có thể bổ sung calibration, nên **không bắt buộc thu thập lại toàn bộ unknown calibration từ đầu**.
- Hiện không có holdout known/unknown mới được chứng minh độc lập. Lượng cần thêm tùy mục tiêu lỗi bạn chọn. Ví dụ muốn FAR ≤1% với cận trên một phía 95% khi 0 false accept và các lượt độc lập: cần ít nhất **299 lượt unknown holdout**; với wrong-known ≤5% và 0 lỗi cần **59 lượt known**, có thể bố trí **60 lượt (12/sinh viên)**. Đây là điều kiện thống kê trong giả định đã nêu, không bảo đảm đạt chất lượng thực tế.
- Nếu đổi mục tiêu, dùng công cụ evaluate với các tỷ lệ do bạn xác định; nó tính cận và từ chối nếu chưa đủ mẫu/không đạt. Không dùng hàng trăm frame liền nhau của một người làm hàng trăm phép thử độc lập.

Khi đủ dữ liệu và đạt: `publish` → `activate` → khởi động lại Flask → chọn đúng profile → thử webcam thực tế. Hướng dẫn đầy đủ trong `VERIFICATION_DATA_GUIDE.md`. Tại thời điểm này chưa có nhãn thật để tính FAR/FRR hay chọn threshold; tự động vẫn khóa. UNKNOWN không bao giờ được đưa vào gallery sinh viên.
