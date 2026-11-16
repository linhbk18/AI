# Tích hợp năm sinh viên và 92 embedding đã có

Đã nhập vào database SQLite hiện tại tại `F:\FACE_RECOGNITION\outputs\management.sqlite3`, không tạo lại database, không chạy lại ArcFace cho 92 ảnh. Website đang chạy tại **http://127.0.0.1:5055/students**.

## 1. Danh sách đã tích hợp

| Mã hiện tại | Tên hiển thị | Nhãn dataset giữ nguyên | Embedding gốc đã liên kết | Loại mã |
|---|---|---|---:|---|
| DEMO-SV001 | Bùi Kiều Linh | Bùi Kiều Linh | 20 | Mã tạm · demo |
| DEMO-SV002 | Hà Vi | Hà Vi | 17 | Mã tạm · demo |
| DEMO-SV003 | Nhi | Nhi | 20 | Mã tạm · demo |
| DEMO-SV004 | PThao | PThao | 16 | Mã tạm · demo |
| DEMO-SV005 | Thu Thảo | Thu Thảo | 19 | Mã tạm · demo |
| **Tổng** | **5 hồ sơ** | **5 nhãn** | **92** | **Chưa có mã sinh viên chính thức** |

Các mã DEMO chỉ dùng để demo, không được khẳng định là mã sinh viên thật. Tất cả hồ sơ hiển thị trạng thái đã đăng ký theo số tham chiếu thực tế trong SQLite, không gắn trạng thái giả.

## 2. Dữ liệu thực tế trước nhập và yêu cầu xóa bổ sung

Đọc `PHASE_1_REPORT.md`, mã nguồn và SQLite thực tế cho thấy database đã có:

- Lớp `MIS – 50K`.
- Hồ sơ `SV1 – BÙI KIỀU LINH`, một embedding đăng ký riêng trong SQLite và một liên kết vào lớp MIS.
- Chưa có `legacy_mappings` và chưa có tham chiếu nào tới 92 vector JSON.

Không tự gộp hồ sơ SV1 với nhãn Bùi Kiều Linh theo họ tên. Sau câu hỏi làm rõ, người dùng yêu cầu **“hãy xoá dữ liệu SV1-BÙI KIỀU LINH đó đi”**. Theo yêu cầu mới này, đã xóa riêng hồ sơ SV1, một embedding đăng ký riêng và một liên kết lớp của SV1. Không xóa lớp MIS và không xóa/chỉnh sửa ảnh hay vector trong JSON gốc. Thao tác được thực hiện trong transaction có khóa ngoại và kiểm tra đúng mã/tên trước khi xóa.

Có bản sao SQLite nhất quán trước xử lý ở `outputs/student_import/before_import.sqlite3`; bản sao vẫn chứa dữ liệu SV1 cũ để phục hồi nếu cần và không được tự động khôi phục vào database đang dùng. Nhật ký phạm vi xóa: `outputs/student_import/requested_removal.json`. Các tệp này nằm trong outputs đã bị Git ignore, không xuất lên frontend hoặc Internet.

Đây là ngoại lệ xóa được người dùng yêu cầu rõ sau khi kiểm tra. Ngoài phạm vi đó, dữ liệu lớp và các tệp gốc được bảo toàn. Database thật hiện có đúng năm hồ sơ nhập từ dataset, lớp MIS còn nguyên và chưa tự phân năm sinh viên vào lớp nào.

## 3. Cơ chế nhập và ánh xạ

- Gallery thực tế là danh sách 92 record trong `outputs/face_embeddings.json`, gồm nhãn `student_id`, provenance `source`, vector 512 chiều và cấu hình ArcFace/RetinaFace.
- Trước ghi, kiểm tra đủ năm nhãn, số mẫu 20/17/20/16/19, chiều 512, hữu hạn, không zero, không trùng nguồn và nguồn thuộc `DATASET/registered/<đúng nhãn>`. So sánh cấu hình với `face_config.json`.
- Danh sách nhãn, tên hiển thị và mã tạm được khai báo rõ trong `backend/database/legacy_student_roster.json`, không suy ra từ thứ tự file.
- Tiếp tục cơ chế Prompt 1: `legacy_mappings` nối nhãn dataset với ID hồ sơ; `face_embeddings.embedding_reference` tham chiếu `legacy:<index>`. Chỉ số chỉ là địa chỉ tham chiếu theo kiến trúc hiện có; chủ sở hữu được quyết định từ `record.student_id`, không từ vị trí.
- Dùng chung hàm liên kết với chức năng ánh xạ thủ công. Không sao chép vector JSON vào `embedding_json` và không nhân đôi gallery.
- Import toàn bộ trong một transaction `BEGIN IMMEDIATE`; mâu thuẫn mã/ánh xạ sẽ rollback, không tự gộp hoặc ghi đè.
- Nếu nhãn đã ánh xạ, giữ ID, mã và tên đang có. Trùng họ tên nhưng chưa ánh xạ thì dừng để yêu cầu lựa chọn rõ `--link LABEL=ID` hoặc `--new LABEL`; không tự quyết định từ tên/similarity.
- Chạy nhập thực lần đầu: tạo 5 hồ sơ, 5 ánh xạ, 92 tham chiếu. Chạy lại: **0 hồ sơ mới, 0 thay đổi tham chiếu/ánh xạ**, không tăng gallery revision.
- Migration bổ sung `students.student_code_is_temporary` và tăng schema_version lên 2; không drop/reset các bảng. Khi đổi mã sang mã không có tiền tố DEMO-, nhãn tạm được bỏ; chỉnh tên hoặc giữ nguyên mã tạm không làm mất cờ tạm.
- Gallery runtime sử dụng cơ chế revision đã có ở Prompt 1. Sau khởi động lại, health xác nhận **92 vector, 5 danh tính, một lần nạp mô hình**. Không sửa pipeline camera, model, cấu hình, ngưỡng hay API nhận diện.

## 4. File tạo và chỉnh sửa

Tạo mới:

- `backend/database/legacy_student_roster.json`
- `backend/database/import_legacy_students.py`
- `tests/test_student_import.py`
- `tests/student_import_browser_qa.py`
- `tests/verify_student_import.py`
- `STUDENT_DATA_IMPORT_REPORT.md`
- `outputs/student_import/`: backup, nhật ký xóa theo yêu cầu, dry-run, kết quả import/chạy lại, kiểm thử giao diện và xác minh cuối.

Chỉnh sửa:

- `backend/database/db.py`: migration cờ mã tạm, cập nhật cờ khi sửa mã, tách hàm liên kết transaction dùng chung và có khả năng chạy lại.
- `frontend/static/js/management.js`: thêm nhãn “Mã tạm · demo” ở danh sách/chi tiết và giải thích trong hộp sửa hồ sơ. Không xây dựng lại trang quản lý.
- Dữ liệu trong `outputs/management.sqlite3` theo phạm vi đã mô tả.

Không sửa `runtime_service.py`, `face_service.py`, `camera_pipeline.py`, `app.py`, `camera.js`, các profile camera, DATASET, JSON gallery, config/threshold, mô hình, hoặc báo cáo AI cũ. Không cài thư viện, không tạo lớp thật mới, không xây dựng điểm danh.

## 5. Kiểm thử và kết quả

### Unit/API: 30/30 đạt

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_student_import tests.test_management tests.test_camera_api
```

Gồm 12 ca import mới và 18 ca quản lý/camera cũ. Bao phủ:

- Đúng 5 hồ sơ và 92 tham chiếu theo đúng nhãn; chạy lại không trùng.
- Giữ mã/tên của hồ sơ đã ánh xạ; không gộp họ tên chưa xác nhận.
- Liên kết rõ ràng tới hồ sơ có sẵn giữ upload/lớp; lựa chọn giữ riêng không gộp.
- Đổi mã và tên hiển thị vẫn giữ đủ embedding và nhãn dataset.
- Trùng mã giữa chừng rollback toàn bộ; dry-run không tạo hồ sơ/tham chiếu.
- Gallery thiếu, vector NaN hoặc provenance sai bị từ chối, không bịa mẫu.
- Đảo thứ tự gallery trước nhập vẫn ánh xạ theo nhãn; tham chiếu sai chủ sở hữu bị phát hiện.
- Thêm cả năm sinh viên vào một lớp trên database tạm, mở lại SQLite vẫn đủ dữ liệu.
- Các hợp đồng API camera hiện có tiếp tục đạt.

Log ERROR trong ca giả lập lỗi camera là nhánh kiểm thử có chủ ý. Có ResourceWarning từ tệp multipart tạm của bộ kiểm thử upload; các assertion đều đạt và các database kiểm thử được dọn.

### Website: 5/5 kiểm tra đạt

```powershell
& .\.venv\Scripts\python.exe tests/student_import_browser_qa.py
```

Chrome truy cập **website thật** `/students`, kiểm tra đủ năm dòng, mã/tên, nhãn mã tạm, số vector 20/17/20/16/19 và nút Sửa. Không còn hồ sơ SV1 cũ.

Phần ghi kiểm thử dùng database tạm riêng với Flask/API/SQLite thật: sửa mã/tên qua giao diện vẫn giữ 20 vector và bỏ cờ tạm; tạo lớp thử, thêm từng người qua giao diện, thấy đủ năm sinh viên trong danh sách lớp. Không ghi lớp thử hoặc mã thử vào database thật. Không có lỗi JavaScript.

Ảnh chụp kiểm tra trang sinh viên: `.venv/student_import_students.png`; kết quả `outputs/student_import/browser_tests.json`.

### Sau khởi động lại Flask và API nhận diện thật

Đã dừng Flask cũ, nhập dữ liệu và khởi động lại bằng `python -m backend.app` trên cổng 5055. Chạy:

```powershell
& .\.venv\Scripts\python.exe tests/verify_student_import.py
```

Kết quả:

- SQLite đủ 5 hồ sơ, 5 ánh xạ và 92 tham chiếu, không có vector JSON bị sao chép vào SQLite.
- Không thiếu/trùng/sai chủ sở hữu; kiểm tra khóa ngoại không có lỗi và `integrity_check=ok`.
- `/api/students` và `/api/legacy-gallery` khớp SQLite về ID hồ sơ, số vector và nhãn; trạng thái mặt là dữ liệu thật.
- Gallery runtime đủ 92 vector / 5 người sau restart. Kiểm tra so khớp trực tiếp một vector gốc cho mỗi nhãn trả đúng hồ sơ đã ánh xạ; đây là kiểm tra liên kết, không phải đánh giá accuracy mới.
- Một yêu cầu HTTP `/api/recognize` dùng ảnh registered và mô hình ArcFace/RetinaFace thật trả đúng ứng viên `DEMO-SV001 · Bùi Kiều Linh`, vẫn `identity_confirmed=false`, `attendance_enabled=false`.
- Không tạo/lưu embedding registered mới trong lần kiểm tra API này. Không chạy lại quy trình trích xuất 92 ảnh.
- Chưa kiểm thử camera vật lý hay nhận diện người thật trong lượt tích hợp dữ liệu này; không kế thừa hoặc khẳng định kết quả kiểm thử người thật.

Chi tiết: `outputs/student_import/final_verification.json`, `import_result.json`, `repeat_import_result.json`.

## 6. Bảo toàn dữ liệu

Đối chiếu SHA-256 của **202 tệp DATASET và output đã có trước lượt làm việc này** (không bao gồm SQLite đang được cập nhật): **0 tệp thay đổi**. Bao gồm toàn bộ ảnh gốc, gallery 92 vector, cấu hình/threshold, weights và các kết quả kiểm thử AI đã có.

Lớp MIS giữ nguyên toàn bộ trường dữ liệu so với snapshot trước nhập. Không tự tạo hay phân lớp vào database thật. Việc xóa hồ sơ SV1, vector đăng ký riêng và liên kết lớp là theo yêu cầu bổ sung rõ ràng của người dùng, không phải xóa nhãn Bùi Kiều Linh khỏi DATASET/gallery.

`/outputs/`, DATASET, SQLite và embedding đã có trong `.gitignore`. Không tạo route tải ảnh/vector, không gửi embedding xuống trình duyệt, không đẩy dữ liệu lên GitHub hoặc triển khai Internet.

## 7. Cách chạy lại

Từ `F:\FACE_RECOGNITION`:

```powershell
# Kiểm tra trước, rollback phần dữ liệu nhập
& .\.venv\Scripts\python.exe -m backend.database.import_legacy_students --dry-run --report outputs/student_import/check_again.json

# Nhập/kiểm tra idempotent; nhãn đã ánh xạ sẽ giữ nguyên mã và tên
& .\.venv\Scripts\python.exe -m backend.database.import_legacy_students --report outputs/student_import/latest_import.json

# Chỉ khởi động khi chưa có server đang chạy
& .\.venv\Scripts\python.exe -m backend.app
```

Dry-run có thể áp dụng migration cấu trúc bổ sung nếu database còn schema cũ, nhưng không commit hồ sơ, mapping hoặc vector. Công cụ yêu cầu database đã tồn tại. Công cụ không tự xóa SV1 hay hồ sơ nào khác; thao tác xóa theo yêu cầu đã thực hiện riêng một lần và được ghi nhận rõ.

## 8. Đổi mã tạm thành mã thật

1. Mở **http://127.0.0.1:5055/students**, Ctrl+F5 nếu tab còn giao diện cũ.
2. Bấm **Sửa** ở dòng sinh viên cần đổi.
3. Nhập mã sinh viên chính thức do người dùng cung cấp; có thể chỉnh họ tên hiển thị.
4. Bấm **Lưu thông tin**. ID hồ sơ và nhãn dataset không đổi, embedding vẫn liên kết đủ; không cần đăng ký lại ảnh.
5. Mã khác tiền tố `DEMO-` sẽ không còn nhãn “Mã tạm · demo”. Việc nhập mã không có nghĩa hệ thống đã kiểm chứng mã đó với nhà trường.

Để thử lớp: vào lớp MIS hoặc tạo lớp theo thông tin thực tế, chọn và thêm năm sinh viên. Chưa có thao tác điểm danh tự động.

## 9. Giới hạn còn lại

- Năm mã hiện tại vẫn là mã tạm, cần người dùng cập nhật khi có mã thật.
- Liên kết dựa trên nhãn dataset đã được người dùng chỉ định, không phải xác minh danh tính hoặc độ đúng nhãn ảnh bằng AI.
- Chưa có ngưỡng từ chối người lạ chính thức; các kết quả nhận diện vẫn là ứng viên chưa xác nhận.
- Không thay đổi giới hạn tốc độ RetinaFace trên laptop, không chạy lại benchmark/validation và không tuyên bố accuracy mới.
- Chưa triển khai phiên điểm danh, Có mặt/Vắng mặt, lịch sử hoặc báo cáo chuyên cần của Prompt 2.
