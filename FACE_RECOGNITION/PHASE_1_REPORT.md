# Giai đoạn 1 — Quản lý lớp học, sinh viên và đăng ký khuôn mặt

Đã triển khai trực tiếp trên `F:\FACE_RECOGNITION`, dùng Flask hiện có, SQLite, HTML/CSS/JavaScript. Không cài thêm thư viện, không thêm đăng nhập, không triển khai lên Internet, không viết nghiệp vụ điểm danh.

## 1. Rà soát trước triển khai

| Thành phần | Trạng thái ban đầu | Xử lý |
|---|---|---|
| Flask `backend/app.py`, cổng 5055 | Có, camera/API hoạt động | Bổ sung blueprint, giữ đường dẫn/hợp đồng camera |
| Camera frontend, ArcFace/RetinaFace, profile thử nghiệm | Có và hoạt động | Giữ nguyên pipeline/profile; chỉ thêm đường dẫn sang quản lý |
| Gallery 92 vector, 5 nhãn, cấu hình và báo cáo | Có | Đọc, bảo toàn nguyên trạng |
| `backend/database/db.py` | Chỉ là placeholder | Hoàn thiện SQLite |
| Tổng quan, quản lý lớp, quản lý sinh viên | Chưa có | Tạo mới |
| Phân lớp, đăng ký khuôn mặt, ánh xạ hồ sơ cũ | Chưa có | Tạo mới |
| `attendance_service.py`, `attendance.html` | Placeholder | Giữ nguyên; chưa triển khai |

Kiểm tra thực tế gallery là danh sách các record có `student_id`, `source`, `embedding` 512 chiều và metadata pipeline. Các nhãn cũ: Bùi Kiều Linh (20), Hà Vi (17), Nhi (20), PThao (16), Thu Thảo (19). Đây là nhãn dataset, không phải mã sinh viên thật. Không tự tạo hồ sơ cho năm nhãn này.

## 2. Chức năng đã hoàn thành

- Tổng quan có ba số liệu lấy từ SQLite: lớp hoạt động, sinh viên hoạt động, sinh viên đã có dữ liệu khuôn mặt.
- Lớp: danh sách, tìm mã/tên, thêm, sửa, xem chi tiết, xóa có bảo vệ, thêm/gỡ sinh viên có sẵn.
- Sinh viên: mã/họ tên bắt buộc, danh sách, tìm kiếm, thêm/sửa, xem lớp tham gia, chọn nhiều lớp, trạng thái khuôn mặt và chi tiết đăng ký.
- Xóa yêu cầu xác nhận trên giao diện. Chặn xóa lớp/sinh viên còn liên kết; chặn xóa sinh viên đã có khuôn mặt. Với hồ sơ được phép xóa, đặt `deleted_at`, không xóa vật lý. Mã của hồ sơ lưu trữ được giữ lại và không tái sử dụng.
- Đăng ký bằng upload hoặc chụp camera. Chỉ nhận ảnh tĩnh JPEG/PNG/WebP thực, tối đa 4 MB, từ 32 đến 4096 px mỗi cạnh. Giới hạn request multipart 5 MB riêng endpoint này; giới hạn camera cũ 1 MB không đổi. Có xử lý EXIF orientation.
- Cần checkbox xác nhận ảnh thuộc hồ sơ đã chọn. Đây là xác nhận thủ công của người vận hành, không phải kết luận sinh trắc học của AI.
- Đăng ký dùng RetinaFace gốc, căn chỉnh theo cấu hình đã đăng ký; chỉ khi đúng một khuôn mặt mới gọi ArcFace tạo vector. Kiểm tra 512 chiều, hữu hạn, không zero. Ảnh không lưu trên đĩa; vector, cấu hình và SHA-256 ảnh lưu SQLite. Cùng bytes ảnh không được đăng ký lặp hoặc cho hồ sơ khác.
- Một sinh viên nhận nhiều embedding; không tái tạo dữ liệu người khác. Khuôn mặt mới thêm vào gallery runtime, không ghi đè JSON 92 vector.
- Ánh xạ thủ công nhãn dataset sang hồ sơ đã nhập mã thật; mỗi nhãn/hồ sơ chỉ có một liên kết. Các vector cũ được tham chiếu theo chỉ số `legacy:<index>`, không sao chép lại embedding vào SQLite hay nhân đôi mẫu trong gallery.
- Gallery kết hợp giữ 92 vector gốc, đổi nhãn hiển thị cho phần đã ánh xạ và thêm vector SQLite. Nhãn hồ sơ hiển thị `MÃ · Họ tên`. Sửa hồ sơ cũng cập nhật nhãn runtime. Không suy đoán liên kết từ similarity.
- Cập nhật gallery sử dụng revision SQLite và khóa suy luận hiện có, không tải lại mô hình. Nếu AI bận/chưa sẵn sàng, dữ liệu vẫn lưu bền vững và được áp dụng khi tải AI hoặc trước lượt camera kế tiếp. API trả `gallery_updated` để phân biệt.
- Gallery thay đổi sẽ không kế thừa ngưỡng từ chối của gallery trước. Không sửa `recognition_threshold.json`; mọi kết quả vẫn chưa xác nhận, không ghi điểm danh.
- Sidebar tiếng Việt, responsive desktop/mobile; các mục điểm danh/lịch sử/báo cáo chỉ ghi “Chưa triển khai”, không dẫn đến trang không tồn tại.

## 3. File tạo và sửa

Tạo mới:

- `backend/management.py`
- `frontend/templates/management.html`
- `frontend/static/css/management.css`
- `frontend/static/js/management.js`
- `tests/test_management.py`
- `tests/phase1_real_ai.py`
- `tests/phase1_browser_qa.py`
- `tests/phase1_verify.py`
- `outputs/management.sqlite3` — database thật, ban đầu không có lớp/sinh viên giả.
- `outputs/phase1/real_ai_tests.json`, `browser_tests.json` và kết quả xác minh cuối cùng.
- `PHASE_1_REPORT.md`

Sửa:

- `backend/database/db.py`: thay placeholder bằng module SQLite.
- `backend/app.py`: gắn store/blueprint, mở rộng bảo vệ token/origin cho PUT/PATCH, no-store cho trang quản lý. Không đổi API camera.
- `backend/face_recognition/runtime_service.py`: bổ sung đăng ký và nạp gallery SQLite, tái sử dụng mô hình và khóa suy luận; số vector health phản ánh gallery kết hợp.
- `frontend/templates/index.html`: thêm liên kết quản lý, giữ nguyên camera.
- `tests/test_camera_api.py`: chuyển database kiểm thử sang thư mục tạm; các hợp đồng camera vẫn được kiểm thử như trước.

Không sửa model, cấu hình pipeline/profile camera, mã tối ưu camera, DATASET, embedding JSON gốc, ngưỡng, requirements hoặc frontend camera.js. `.gitignore` hiện có đã loại `/outputs/`, SQLite, embedding và DATASET nên không cần thay đổi.

## 4. Thiết kế SQLite

Database: `outputs/management.sqlite3`. Mỗi kết nối bật `PRAGMA foreign_keys=ON`, thao tác ghi dùng transaction. Khởi tạo chỉ `CREATE TABLE IF NOT EXISTS`, không drop, reset hoặc seed dữ liệu. Chuỗi thời gian lưu UTC ISO-8601.

| Bảng | Trường chính / ràng buộc |
|---|---|
| `students` | id PK, student_code UNIQUE không phân biệt hoa thường, full_name, created_at, updated_at, deleted_at |
| `classes` | id PK, class_code UNIQUE, class_name, description, created_at, updated_at, deleted_at |
| `class_students` | id PK, class_id FK RESTRICT, student_id FK RESTRICT, UNIQUE(class_id,student_id) |
| `face_embeddings` | id PK, student_id FK RESTRICT, embedding_reference UNIQUE hoặc embedding_json (chính xác một trong hai), pipeline_json, image_sha256 UNIQUE, created_at, attribution_confirmed_at |
| `legacy_mappings` | dataset_label PK, student_id FK UNIQUE |
| `management_meta` | key PK, value; schema_version=1, gallery_revision |

Vector không trả qua API quản lý/camera. Endpoint hồ sơ chỉ trả số lượng, ID và tham chiếu, thời gian. Database/ảnh gốc không có route tải công khai. Các lệnh ghi cần token trang và cùng origin; đây là bảo vệ yêu cầu trên localhost, không phải đăng nhập/phân quyền.

Chuẩn bị prompt 2: dùng FK đến ID ổn định của `classes`/`students`; có thể bổ sung `attendance_sessions(id,class_id,started_at,ended_at,...)` và `attendance_records(id,session_id,student_id,status,...,UNIQUE(session_id,student_id))`. Chưa tạo các bảng hay API này. Khi bổ sung phải giữ soft deletion, chính sách lịch sử và migrations có phiên bản.

## 5. Trang và API

Trang: `/overview`, `/classes`, `/classes/<id>`, `/students`, `/students/<id>`. Camera cũ tại `/`.

| Phương thức | API | Nội dung |
|---|---|---|
| GET | `/api/overview` | Số liệu SQLite |
| GET/POST | `/api/classes` | Danh sách `?q=`, tạo lớp |
| GET/PUT/DELETE | `/api/classes/<id>` | Chi tiết/sửa/lưu trữ lớp |
| POST | `/api/classes/<id>/students` | JSON `{student_id}` |
| DELETE | `/api/classes/<id>/students/<student_id>` | Gỡ liên kết, không xóa sinh viên |
| GET/POST | `/api/students` | Danh sách `?q=`, tạo hồ sơ |
| GET/PUT/DELETE | `/api/students/<id>` | Chi tiết/sửa/lưu trữ hồ sơ |
| GET | `/api/legacy-gallery` | Nhãn gốc, số vector, hồ sơ đã liên kết |
| POST | `/api/students/<id>/legacy-mapping` | JSON `{dataset_label,confirmed:true}` |
| POST | `/api/students/<id>/faces` | Multipart `image`, `confirmed=true` |

API camera giữ nguyên: GET `/api/health`, POST `/api/camera/sessions`, DELETE `/api/camera/sessions/<id>`, POST `/api/recognize`. Cổng vẫn 5055. Health có thể tăng số vector/hồ sơ sau đăng ký; 92 vector gốc luôn tồn tại.

## 6. Kiểm thử thực tế

### API và SQLite

Chạy `.venv\Scripts\python.exe -m unittest tests.test_management tests.test_camera_api`: **18/18 đạt**, gồm 10 ca quản lý và 8 ca camera cũ. Bao phủ thêm/sửa/tìm, trùng mã, nhiều lớp, gán trùng, gỡ không xóa hồ sơ, bảo vệ xóa, khóa ngoại, xác nhận ảnh, giới hạn upload, lỗi AI, nhiều vector, vector sai/NaN, ánh xạ và bảo toàn JSON gốc. Ca persistence khởi chạy Python mới, dựng lại Flask với cùng SQLite và xác nhận hồ sơ, lớp, vector vẫn còn. Toàn bộ dữ liệu giả nằm ở database tạm riêng trong `.venv`, tự dọn sau kiểm thử.

Log ERROR cố ý trong ca kiểm thử camera là nhánh giả lập lỗi suy luận, không phải lỗi AI khi chạy thật.

### AI thật

`tests/phase1_real_ai.py` dùng mô hình thật và database tạm riêng:

- Ảnh registered một người: tạo/lưu vector 512 chiều thành công, khoảng **17,37 giây**.
- So với `DeepFace.represent()` gốc trên cùng ảnh: cosine **0,9999999999999999**. Đây là đối chiếu một mẫu cho tiền xử lý đăng ký, không phải đánh giá lại toàn bộ validation.
- Ảnh trống: bị từ chối, khoảng **13,68 giây**.
- `DATASET/Test/Group10.jpg`: nhiều khuôn mặt, bị từ chối, khoảng **18,92 giây**. Không thêm vector từ ảnh Test.
- Gallery lên 93 vector sau đăng ký; ánh xạ đổi nhãn mà không nhân đôi 92 mẫu.
- Camera API thật thấy hồ sơ vừa thêm/ánh xạ, vẫn `identity_confirmed=false`, `attendance_enabled=false`.
- App/SQLite mở lại thấy vector đã lưu; mô hình chỉ nạp một lần, thời gian khởi động khoảng **42,18 giây**.
- Chuyển về database thật trước khi phục vụ localhost: **0 lớp, 0 sinh viên, 0 hồ sơ đăng ký trong SQLite**, gallery gốc vẫn **92 vector / 5 nhãn**.

Chi tiết: `outputs/phase1/real_ai_tests.json`.

### Camera vật lý và khởi động lại

Đã chạy `tests/camera_physical_smoke.py` trên Chrome không dùng thiết bị giả: mở được camera 640×480, gửi/xử lý được 1 frame HTTP với mô hình thật, frame này không phát hiện khuôn mặt. Dừng camera kết thúc tracks và xóa kết quả. Không lưu ảnh, không xác nhận danh tính. Lần đo này mất khoảng **50,4 giây**, cho thấy giới hạn hiệu năng laptop vẫn tồn tại; không phải kết quả tối ưu mới. Không có người được bố trí để kiểm thử danh tính/nhiều người và chưa đăng ký hồ sơ thật bằng camera vật lý. Kết quả: `outputs/phase1/physical_camera_smoke.json`.

Sau kiểm thử đã dừng server QA và khởi động lại bằng lệnh chuẩn `python -m backend.app`, dùng lại database thật tại cổng 5055. Việc tồn tại dữ liệu không rỗng qua tiến trình mới đã được kiểm thử riêng bằng SQLite tạm, tránh ghi hồ sơ giả vào database thật.

### Giao diện

`tests/phase1_browser_qa.py`: **10/10 kiểm tra đạt** bằng Chrome + Flask/API/SQLite thật trong database tạm; riêng AI dùng test double. Bao phủ CRUD, lỗi trùng lớp, tìm sinh viên, phân lớp hai chiều, upload có xác nhận, chụp bằng thiết bị giả lập và dừng tracks, ánh xạ, bảo vệ xóa, số liệu tổng quan, mobile 390px không tràn ngang và không lỗi JavaScript.

Chạy lại `tests/camera_optimization_browser_qa.py`: **9/9 đạt** (camera giả lập): video liên tục, không hàng đợi, tối đa một request, đổi người/xóa ứng viên cũ, dừng bỏ kết quả muộn, không mặt không hiện tên. Không chạy lại benchmark hay thay profile tối ưu.

Ảnh chụp kiểm tra giao diện lưu cục bộ trong `.venv/phase1_*.png`; không chứa ảnh camera vật lý. Báo cáo giao diện tại `outputs/phase1/browser_tests.json`.

### Bảo toàn dữ liệu

Đối chiếu SHA-256 **197 file dữ liệu/báo cáo đã có trước giai đoạn này**: không thay đổi. Bao gồm DATASET, face_embeddings.json, face_config.json, recognition_threshold.json và kết quả kiểm thử cũ. Database thật không có sinh viên/lớp giả.

## 7. Giới hạn còn lại

- Đăng ký RetinaFace gốc vẫn mất hàng chục giây trên laptop; hiển thị trạng thái chờ, khóa nút gửi và báo lỗi khi AI bận. Giai đoạn này không tối ưu lại camera.
- Chưa có ngưỡng từ chối chính thức/người lạ có nhãn; đăng ký và liên kết không làm các kết quả camera trở thành danh tính đã xác minh.
- Xác nhận ảnh/ánh xạ phụ thuộc người vận hành; chưa có liveness hay quy trình xác minh danh tính. Checkbox không phải bằng chứng tự động.
- Không hỗ trợ sửa/gỡ liên kết nhãn cũ hoặc xóa vector qua giao diện trong phạm vi này để tránh mất dữ liệu; phải đối chiếu kỹ trước khi xác nhận.
- Không tạo dữ liệu thật thay người dùng: cần nhập mã sinh viên thật và ánh xạ thủ công. Do đó tổng quan SQLite ban đầu bằng 0 dù gallery cũ còn đủ 92 vector.
- Chưa có kiểm thử nhiều người thật lần lượt đứng trước camera hoặc chất lượng ảnh đăng ký bằng camera vật lý. Kiểm thử camera thật một khung, nếu có, được ghi riêng ở phần xác minh cuối.
- Chưa xây dựng đăng nhập/phân quyền, không dùng website này trên Internet công khai.

## 8. Hướng dẫn sử dụng

Chạy từ thư mục dự án, không cần kích hoạt venv:

```powershell
& .\.venv\Scripts\python.exe -m backend.app
```

Truy cập **http://127.0.0.1:5055/overview**. Camera cũ vẫn tại **http://127.0.0.1:5055/**. Không chạy hai Flask cùng lúc trên cổng này. Sau khi sửa frontend, Ctrl+F5 để tải mới.

1. Tạo lớp tại Quản lý lớp học.
2. Tạo hồ sơ bằng mã/họ tên thật tại Quản lý sinh viên.
3. Mở chi tiết để phân lớp.
4. Với một trong năm người đã có dữ liệu, chọn đúng nhãn dataset và xác nhận liên kết thủ công.
5. Đăng ký bổ sung bằng upload/chụp ảnh; xác nhận đúng chủ thể, bấm lưu và chờ hoàn tất. Tắt camera nhận diện ở tab khác để tránh AI bận.
6. Tắt/mở Flask vẫn dùng lại `outputs/management.sqlite3`; không xóa database khi khởi động.

## 9. Chuyển sang prompt 2

Chưa triển khai tạo phiên điểm danh, Có mặt/Vắng mặt, lịch sử, báo cáo chuyên cần, xuất báo cáo hay tự xác nhận danh tính. Prompt 2 cần làm rõ quy tắc lớp/phiên, xử lý trùng, khóa lịch sử và điều kiện xác nhận trước khi ghi nhận. Khả năng nhận diện người lạ cần dữ liệu/đánh giá riêng, không được tự tạo ngưỡng từ kết quả prompt 1.
