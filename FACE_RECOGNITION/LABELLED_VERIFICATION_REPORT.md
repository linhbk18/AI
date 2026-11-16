# Đánh giá nhãn thật và xác minh danh tính

Ngày kiểm tra: 15/11/2026. Chế độ tự động **vẫn tắt**; chưa có ngưỡng chính thức.

## Dữ liệu và lần chạy thực tế

Đã chạy lại RetinaFace + ArcFace thật trên 8 ảnh có nhãn sử dụng được, không lấy dự đoán AI làm nhãn. Profile `retinaface_scaled_experimental`, alignment và normalization theo cấu hình runtime. Ảnh Group12 chưa có nhãn hợp lệ nên không đưa vào tính chỉ số. Các ảnh Test không bổ sung vào gallery.

Nhãn revision 38: 104 khung, gồm 22 VALID thuộc 5 sinh viên, 30 UNKNOWN, 2 IGNORE và 50 chưa gắn nhãn. Không thấy lỗi định dạng, sai hash nguồn, bản ghi trùng ID hoặc cặp khung chồng lấn đạt ngưỡng kiểm tra hình học. Đây không phải bằng chứng không có cùng một người xuất hiện lại: cả 30 UNKNOWN chưa có mã người ẩn danh nên chưa đếm được số người lạ khác nhau.

Nhãn còn thiếu: 3.jpg: 1; Group11.jpg: 6; Group12.jpg: 43. Không tự điền những nhãn này.

## Kết quả

| Mã sinh viên | Nhãn thật | Top-1 đúng | Chấp nhận đúng ở ngưỡng khảo sát | Từ chối |
|---|---:|---:|---:|---:|
| DEMO-SV001 | 5 | 4 | 3 | 2 |
| DEMO-SV002 | 6 | 5 | 5 | 1 |
| DEMO-SV003 | 2 | 2 | 2 | 0 |
| DEMO-SV004 | 7 | 7 | 2 | 5 |
| DEMO-SV005 | 2 | 1 | 0 | 2 |
| Tổng | 22 | 19 | 12 | 10 |

20/22 khuôn mặt sinh viên ghép được kết quả; 19 đúng Top-1 và 1 sai. Hai khung chưa ghép được là 4.jpg #3 (DEMO-SV005), #7 (DEMO-SV001); cần xem lại kích thước/độ rõ và phát hiện/ghép khung. Không tự kết luận nhãn sai.

Group10.jpg #2: nhãn thật DEMO-SV002, ứng viên DEMO-SV005, similarity 0.709858, margin 0.134637. Trường hợp này chứng minh similarity cao riêng lẻ chưa đủ xác minh.

18/30 UNKNOWN ghép được kết quả; 12 không ghép được và bị từ chối. Median similarity đối với danh tính thật: 0.638758 (20 khung); median ứng viên gần nhất của UNKNOWN: 0.685904 (18 khung). UNKNOWN có score tối đa 0.720941. Điểm hai nhóm chồng lấn đáng kể.

Khảo sát các điểm giữa các giá trị đo được, tối đa số chấp nhận đúng với điều kiện không có ca nhận nhầm quan sát được, đề xuất **chỉ để phân tích calibration**:

- similarity >= 0.533511788286182;
- margin >= 0.17386882414194063;
- đúng/chấp nhận: 12/22; nhận sai sinh viên: 0; từ chối nhầm: 10/22 (45.45%);
- nhận nhầm người lạ: 0/30; từ chối người lạ: 30/30 (gồm 12 khung không ghép được).

Không coi 0/30 là bảo đảm từ chối người lạ. Các khung cùng ảnh có tương quan, chưa biết số người khác nhau và dữ liệu đã được xem trước. Ngay giả sử 30 thử nghiệm độc lập, cận trên một phía 95% của 0/30 vẫn khoảng 9.50%. Ngưỡng này không được nạp làm ngưỡng chính thức.

## Độc lập dữ liệu và điều kiện còn thiếu

Cả 9 ảnh Test đã được xử lý/xem kết quả trước khi gắn nhãn. Group01 và Group10 từng xuất hiện trong benchmark; Group12 từng được phân tích phân phối ứng viên. Không sử dụng ảnh nào trong nhóm này làm holdout độc lập. Chi tiết nguồn gốc nằm trong label_audit.json và analysis.json.

Cần hoàn tất 50 nhãn và gán mã người ẩn danh nhất quán cho 30 UNKNOWN (cùng người dùng cùng mã; không dùng AI quyết định). Hiện holdout độc lập có **0 ảnh known và 0 ảnh unknown**. Cần thu dữ liệu webcam mới, khác phiên chụp; người UNKNOWN trong holdout phải khác người dùng để calibration. Phải cải thiện/tái đánh giá mức từ chối nhầm 45.45% trước khi triển khai.

Số mẫu cần thiết phụ thuộc mục tiêu rủi ro được chọn trước, không có một con số bảo đảm chung. Ví dụ mục tiêu FAR <=1% với cận một phía 95% và 0 lỗi cần ít nhất 299 thử nghiệm UNKNOWN độc lập; mục tiêu nhận sai known <=5% với 0 lỗi cần 59 thử nghiệm known độc lập (có thể bố trí 60, 12/sinh viên). Các khung cùng video hoặc cùng người không mặc nhiên độc lập. Không trừ 30 khung Test khỏi các chỉ tiêu holdout mới. Nếu có lỗi, cần tính lại cận và số mẫu; không thu thêm chỉ để che lỗi hoặc tinh chỉnh trên holdout.

## Thu webcam mới

Chạy tại thư mục dự án, đóng camera website trước:

```powershell
.\.venv\Scripts\python.exe -m backend.face_recognition.collect_webcam_validation --identity DEMO-SV001 --split evaluation --capture-session visit_linh_001
.\.venv\Scripts\python.exe -m backend.face_recognition.collect_webcam_validation --identity UNKNOWN:NEW001 --split evaluation --capture-session visit_unknown_001
```

SPACE lưu một ảnh, Q/ESC hủy. Chỉ một người đã được người vận hành xác nhận nhãn trong mỗi ảnh. Mỗi mã phiên phải phản ánh phiên thực tế mới; không đổi mã cho các frame liên tiếp. Dùng `--split calibration` cho tập chỉnh ngưỡng riêng trước khi khóa policy và đánh giá holdout. Ảnh nằm riêng trong outputs/verification_data, không thay dataset/gallery; lệnh add-sample kiểm tra manifest. Công cụ mới chỉ kiểm tra CLI/biên dịch, chưa thử webcam vật lý.

## Runtime và nghiệp vụ

Đã đồng bộ chữ ký pipeline thực tế trong policy đang vô hiệu; giữ enabled/official=false và hai ngưỡng null. Bản policy cũ được sao lưu. Runtime chỉ sử dụng policy đúng chữ ký; không dùng proposal làm cấu hình xác minh.

Kết quả inference lâu hơn 2 giây không bị loại chỉ vì thời gian chạy. Runtime kiểm tra track hiện tại, tính liên tục, bbox/appearance và phiên camera; track mất dấu/đổi người, kết quả quá hạn hoặc không tương ứng không được commit. Receipt VERIFIED chỉ dùng một lần, gắn phiên/track/policy/gallery; kiểm tra thuộc lớp, buổi mở và SQL chống trùng. Không nới tuổi kết quả vô điều kiện.

Do thiếu policy được đánh giá độc lập, chưa thể chạy ca dương tự động với camera thật mà không giả mạo VERIFIED. Các bài kiểm thử SQL/receipt/continuity kiểm tra nhánh có điều kiện, không chứng minh AI đã xác minh trên webcam. Xác nhận thủ công vẫn là phương án dự phòng. Không sửa dữ liệu điểm danh thực để kiểm thử.

## File bằng chứng

- outputs/test_labeling/verification_analysis/label_audit.json
- outputs/test_labeling/verification_analysis/analysis.json
- outputs/test_labeling/verification_analysis/operating_points.csv
- outputs/test_labeling/verification_analysis/per_face_decisions.csv
- outputs/test_labeling/verification_analysis/proposed_threshold.json (vô hiệu)
- outputs/test_labeling/verification_analysis/real_run_check.json
- outputs/test_labeling/evaluation.json và evaluation.csv
- outputs/test_labeling/browser_check.json
- outputs/real_auto_attendance_trace.json (kiểm thử trên SQLite tạm; xem thời điểm chạy)

Không thay đổi nhãn người dùng, DATASET, 92 embedding hoặc dữ liệu SQLite thật.

Kiểm thử lại trong lượt này: 16/16 unittest đạt; công cụ nhãn trên trình duyệt đạt (khớp nhãn người dùng, không điền dự đoán, không đổi nhãn); `real_auto_attendance_trace.py` đạt với inference thật cho sinh viên trong lớp, sinh viên ngoài lớp và ảnh trống. API từ chối kết quả khi rời camera/tắt phiên; giao diện và SQLite tạm chuyển 0/2 → 1/2 bằng xác nhận thủ công, chống trùng và giữ kết quả sau khởi tạo lại Flask. Sự kiện frontend tự động chỉ được kiểm tra hợp đồng giao diện bằng payload SQL đã lưu; không giả lập VERIFIED để tuyên bố camera tự động thành công. Snapshot bảo toàn dữ liệu thực đạt.
