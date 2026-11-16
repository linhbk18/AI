# Chẩn đoán và chuẩn bị xác minh tự động — 15/11/2026

Giữ chế độ tự động tắt. Không thay gallery 92 embedding, SQLite thật, DATASET, nhãn người dùng hoặc profile camera đang chạy.

## Công cụ gắn nhãn

Trang /test-labeling có bộ lọc khung chưa có nhãn, UNKNOWN thiếu mã và tất cả UNKNOWN; số khung cần xử lý trên từng nút ảnh; lưu rồi chuyển ảnh còn việc; bấm bounding box để mở ô nhãn; bảng đối chiếu ảnh nhỏ UNKNOWN giữa các ảnh. Mã người ẩn danh có gợi ý từ mã đã lưu, kiểm tra định dạng ở backend, lưu JSON/CSV theo revision và không lấy dự đoán AI. Các thay đổi nhãn vẫn do người dùng thực hiện, không tự gán 50 khung còn thiếu.

## Group10 #2: bằng chứng thực nghiệm

Nhãn thật do người vận hành: DEMO-SV002. Ghép khung dùng IoU duy nhất, không lấy tên AI để ghép. Pipeline thật RetinaFace phát hiện được khung, ArcFace trả vector 512 chiều, tensor 1×112×112×3; không gặp embedding rỗng/NaN trong lần chẩn đoán.

| Thử nghiệm | Crop trước resize | Góc căn chỉnh | Top-1 | Similarity | Margin |
|---|---|---:|---|---:|---:|
| Camera hiện tại 640 | 19×32 | 18.43° | DEMO-SV005 | 0.709858 | 0.134637 |
| Native RetinaFace 640 | 22×31 | 0° | DEMO-SV005 | 0.744519 | 0.194150 |
| Input lớn hơn, detector vẫn 480 | 37×61 | 11.31° | DEMO-SV002 | 0.549599 | 0.093161 |

Điểm với đúng DEMO-SV002 ở baseline chỉ 0.313457, trong khi DEMO-SV005 đạt 0.709858. Crop rất nhỏ, góc mặt nghiêng và landmark làm góc căn chỉnh nhạy là các yếu tố đáng nghi. Tăng độ phân giải nguồn/crop thay đổi kết quả về đúng nhãn, cung cấp bằng chứng mất chi tiết và độ nhạy detection/alignment góp phần vào lỗi. Chưa tách biệt được nguyên nhân duy nhất: thử nghiệm đồng thời thay đổi rounding landmark, crop và thông tin ảnh. Không kết luận nhãn sai, không sửa gallery và không hạ margin để ép nhận.

Ảnh crop/các mắt/bbox/góc, điểm từng sinh viên, đường dẫn gallery gần nhất, norm embedding và miền giá trị tensor có trong diagnostics.json/cases.html. Crop hiển thị đã được quan sát; không phải do không phát hiện được mặt. ArcFace tạo vector được không đồng nghĩa vector đủ phân biệt danh tính.

## 10 trường hợp từ chối ở baseline

Số đo ở ngưỡng calibration similarity 0.5335117883 và margin 0.1738688241. Đây không phải ngưỡng chính thức.

| Ảnh/khung | Nhãn | Similarity | Margin | Điều kiện không đạt |
|---|---|---:|---:|---|
| 3.jpg #3 | DEMO-SV004 | .522998 | .152176 | similarity và margin |
| 4.jpg #3 | DEMO-SV005 | — | — | không có detection ghép IoU đạt yêu cầu |
| 4.jpg #7 | DEMO-SV001 | — | — | không có detection ghép IoU đạt yêu cầu |
| Group01.jpg #5 | DEMO-SV004 | .526331 | .079631 | similarity và margin |
| Group01.jpg #6 | DEMO-SV001 | .652554 | .164189 | margin |
| Group10.jpg #1 | DEMO-SV004 | .532848 | .057864 | similarity và margin |
| Group10.jpg #2 | DEMO-SV002 | .709858 | .134637 | margin, đồng thời Top-1 sai |
| Group11.jpg #11 | DEMO-SV004 | .578901 | .075550 | margin |
| Group3.jpg #5 | DEMO-SV005 | .535379 | .109280 | margin |
| Group5.jpg #1 | DEMO-SV004 | .607824 | .110714 | margin |

Tổng: 2 ca không ghép được detection; 8 ca margin thấp (3 trong số đó đồng thời similarity thấp). Không chỉ do thiếu detection. Crop, độ nét Laplacian và độ sáng từng ca được lưu để xem xét, không dùng ngưỡng chất lượng tùy ý để tự bỏ ca khó. Group01 #6 có crop 121×157 nhưng vẫn margin thấp, nên không thể quy mọi lỗi cho mặt quá nhỏ.

## So sánh 3 phương án trên cùng nhãn thật Test

Đã chạy mô hình thật trên 8 ảnh có nhãn, cùng 22 known/30 UNKNOWN; so sánh ở ngưỡng khảo sát baseline cố định, không retune từng profile rồi gọi là holdout. Thời gian là một lượt chẩn đoán sau tải mô hình, gồm nhiều khuôn mặt mỗi ảnh, không phải benchmark webcam/P95.

| Phương án | Known ghép được | Top-1 đúng | Chấp nhận đúng | Chấp nhận sai known | Known từ chối | UNKNOWN chấp nhận | TB giây/ảnh |
|---|---:|---:|---:|---:|---:|---:|---:|
| Camera hiện tại 640/detector480 | 20 | 19 | 12 | 0 | 10 | 0 | 4.45 |
| Native detector/input640 | 21 | 20 | 11 | 1 | 10 | 2 | 6.28 |
| Input1280×960 giữ crop lớn/detector480 | 20 | 20 | 13 | 0 | 9 | 0 | 5.97 |

Native detector tìm thêm khuôn mặt nhưng nhận sai cả Group10 #2 qua ngưỡng và 2 UNKNOWN; không triển khai thay thế. Crop lớn là hướng thử nghiệm có ích nhưng chỉ cải thiện 1 ca chấp nhận, vẫn từ chối 9/22, chậm hơn và chưa có holdout webcam. Không tự thay độ phân giải/cấu hình live. Phương án lấy trung bình ba điểm gallery cao nhất cũng được khảo sát trên cùng embedding: Top-1 đúng lần lượt 18, 20, 19; không cải thiện đều và chưa hiệu chỉnh ngưỡng cho cách gộp mới.

Đề nghị tiếp theo: hoàn tất nhãn, thu calibration webcam đại diện, thử giữ ảnh nguồn chất lượng cao/crop lớn khi mặt nhỏ trong profile riêng rồi đánh giá lại toàn bộ, không thay ArcFace hoặc thêm Test vào gallery. Không tăng độ tin cậy bằng cách chỉ giảm ngưỡng hoặc loại các ảnh khó khỏi báo cáo.

## Holdout và kích hoạt

Xem WEBCAM_HOLDOUT_GUIDE.md. Quy trình đã được sửa để chỉ inference calibration trước, khóa threshold/profile/chữ ký/mục tiêu trước inference holdout và ghi hash từng ảnh. Chặn retune, đổi nhãn và tái dùng tập con ảnh đã lộ. Một holdout thất bại phải được báo cáo; cần holdout mới sau cải thiện, không xóa registry. Mã này không chứng minh được độc lập vật lý của người/phiên nếu người vận hành khai sai.

Chưa có known/unknown webcam holdout, 50 khung chưa có nhãn, 30 UNKNOWN chưa có mã người. Không có thay đổi policy tự động chính thức. Lần đánh giá tiếp theo sau khi người dùng lưu nhãn sẽ tự cập nhật báo cáo phân phối, decisions và proposal vô hiệu. Chạy lại công cụ diagnostics sau nhãn mới để tái tạo báo cáo từng ca, không dùng kết quả cũ như còn hiệu lực.

## File và bằng chứng

- frontend/templates/test_labeling.html và frontend/static/js/test_labeling.js: thao tác nhãn/đối chiếu.
- backend/test_labeling.py: kiểm tra mã UNKNOWN và cập nhật phân tích sau đánh giá.
- backend/face_recognition/camera_pipeline.py: callback chẩn đoán tùy chọn, mặc định không lưu ảnh/tensor.
- backend/face_recognition/diagnose_labelled_faces.py: chạy thử thật, crop và báo cáo từng ca.
- backend/face_recognition/labelled_verification_report.py: proposal vô hiệu theo revision nhãn.
- backend/face_recognition/verification_workflow.py: khóa holdout trước inference, chống tái sử dụng.
- backend/face_recognition/collect_webcam_validation.py: kiểm tra mã sinh viên/UNKNOWN trước mở camera.
- tests/test_holdout_freeze.py, tests/test_group_labeling.py, tests/group_labeling_browser.py: kiểm thử thay đổi.

Đầu ra thực tế: outputs/test_labeling/verification_analysis/diagnostics/{diagnostics.json,cases.csv,cases.html,crop JPEG}. Snapshot các file gallery/SQLite/nhãn/policy không đổi trong lúc thử. Tất cả nằm trong outputs đã bị .gitignore loại trừ.

Không tuyên bố đã kiểm chứng VERIFIED tự động dương tính với webcam: policy khóa, không giả mạo nhận diện để bật. Bộ test điều kiện service/SQLite kiểm tra đúng nghiệp vụ với fixture riêng; API thật và trình duyệt dùng ảnh thật trên DB tạm kiểm tra không tự ghi ứng viên, manual 0/2→1/2, chống trùng, persistence và sự kiện frontend cập nhật theo dữ liệu SQL. Cần webcam thật sau policy đạt điều kiện.

Kiểm thử lượt này: 19/19 unittest đạt; browser công cụ gắn nhãn đạt cả bộ lọc và bảng 30 UNKNOWN, responsive, không lỗi JS/không đổi nhãn thật. Đã chạy lại real_auto_attendance_trace.py với inference thật: trong lớp, ngoài lớp, ảnh trống, mất dấu khi inference đang chạy, tắt camera trước phản hồi, manual/SQL/UI/persistence đều đạt trên DB tạm; snapshot dữ liệu bảo vệ không đổi. Đã kiểm tra compile và --help công cụ webcam, chưa chụp webcam vật lý. Flask được khởi động lại tại cổng 5055 sau các bài kiểm thử.
