# Khôi phục xác nhận thủ công và đồng bộ phiên bản

Workspace D:/AI/AI/FACE_RECOGNITION đang ở nhánh ThaoThu, HEAD 5758316. Trước sửa, git status sạch nhưng các module thêm ở lần trước như backend/attendance/face_states.py và backend/verification_capture.py đã không còn. Reflog có checkout/reset; Flask cũ vẫn trả HTTP 200 cho /verification-capture dù mã nguồn route này không còn trên nhánh hiện tại. Đây là bằng chứng backend chạy mã đã import trong bộ nhớ, trong khi frontend đã chuyển sang bản khác. Không tự đổi nhánh hoặc reset lại Git trong lần sửa này.

Nút trên thẻ ứng viên còn yêu cầu identity_confirmed=True, vô tình dùng khóa tự động để chặn thao tác xác nhận bởi con người. Đã bỏ điều kiện này cho xác nhận MANUAL, vẫn yêu cầu đúng buổi đang mở, ứng viên có hồ sơ, thuộc roster và thao tác xác nhận rõ ràng. Có thể tiếp tục xác nhận từ danh sách lớp. Không tự biến tên gợi ý thành danh tính đã xác minh hoặc bật AI_VERIFIED.

Các file sửa: frontend/static/js/recognition_display.js thêm quyết định cho phép xác nhận thủ công; attendance.js dùng quyết định này và nhãn nút rõ ràng; backend/app.py thêm version query cho JS/CSS, health nêu trạng thái manual và kiểm tra fingerprint mã backend đã nạp; camera.js cảnh báo/dừng khi đổi mã nguồn mà chưa restart. Khóa tự ghi không bị bỏ. Test bổ sung trong tests/test_attendance.py, tests/test_camera_api.py và tests/test_recognition_display.cjs.

43 kiểm thử Python, 6 tình huống hiển thị và 5 tình huống xác nhận thủ công Node đạt. Kiểm thử SQL dùng DB tạm, chứng minh xác nhận khi AI chưa sẵn sàng vẫn chuyển 0/2 -> 1/2 với source=manual, không ghi trùng. Kiểm thử source mismatch yêu cầu restart và không cho tự ghi.

Đã restart đúng Flask của dự án; health source_in_sync, manual enabled. Đã phát lại ảnh validation thật Thu Thảo qua pipeline: raw top-1 có DEMO-SV005, eligible_for_confirmation=True, identity_confirmed=False. Đây chỉ là kiểm tra gợi ý/luồng giao diện, không phải bằng chứng xác minh webcam. Không tạo hay sửa điểm danh thật để test. Chi tiết outputs/manual_confirmation_regression_checks.json.

Kiểm tra: mở http://127.0.0.1:5055/attendance và Ctrl+F5; chọn đúng lớp TKW cho Thu Thảo, mở/tiếp tục buổi, bật camera, đối chiếu gợi ý rồi bấm Xác nhận Có mặt (thủ công). Bạn cũng có thể chọn đúng người trong danh sách lớp và xác nhận. Hộp thoại là xác nhận của con người; khung Không xác định không có nghĩa mất chức năng điểm danh thủ công. Tự xác minh/tự ghi AI vẫn khóa khi thiếu policy/bằng chứng, không được báo là đã sửa xong phần tự động.
