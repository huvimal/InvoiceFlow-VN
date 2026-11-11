# InvoiceFlow-VN v0.2 — Hướng dẫn nâng cấp

Code đã được sửa trực tiếp trong dự án hiện tại. File ảnh mẫu ban đầu và báo cáo
pilot cũ được giữ lại. Chưa tự commit hoặc đẩy thay đổi lên GitHub.

## 1. Kiểm tra bản nâng cấp

```powershell
cd "C:\Users\LOQ\Documents\Codex\2026-11-06\cj\outputs\InvoiceFlow-VN"
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe benchmark.py validate
```

Kỳ vọng: `Ran 40 tests`, `OK`, rồi `Valid dataset: 20 cases; synthetic=True`.
Không cần thư viện mới ngoài requirements.txt hiện có. Nếu tạo môi trường mới:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Hiểu bộ dữ liệu mới

Mở `data/benchmark/images/` và `data/benchmark/ground_truth.csv`:

- 20 hóa đơn tổng hợp với giá trị và số hóa đơn khác nhau.
- 5 bố cục: bảng thông thường, phiếu hẹp, tối giản, trang ngang đổi thứ tự cột,
  và khối tổng tiền tách riêng.
- 12 hóa đơn dev, 8 test. Bố cục ở test không xuất hiện trong dev.
- Có ảnh hơi nghiêng, mờ nhẹ, giảm tương phản; có thuế 0 và dòng hàng miễn phí.
- INV-007 thiếu ngày, INV-018 thiếu số hóa đơn, INV-011 có tổng tiền in sai.

Đáp án lấy từ dữ liệu nguồn dùng vẽ hóa đơn, chưa phải gán nhãn độc lập bởi người
thứ hai. Hãy đối chiếu ảnh và bảng chuẩn trước khi công bố kết quả. Mẫu tổng hợp
chưa thay thế được hóa đơn thực tế từ nhiều nhà cung cấp được phép sử dụng.
Không cần sửa các trường bị thiếu hoặc tổng sai trong đáp án để đạt điểm đẹp.
Model đúng là model giữ nguyên những gì in trên ảnh, kể cả con số in sai.

## 3. Chạy báo cáo offline, không cần API key

```powershell
.\.venv\Scripts\python.exe benchmark.py run --mode replay --split all
Start-Process reports/benchmark-replay-all.html
```

Kỳ vọng: xử lý đủ 20 case, trong đó 18 phản hồi hợp lệ, 1 lỗi request giả lập và
1 lỗi schema giả lập. Mã thoát `1` là có chủ đích vì bộ replay chứa các lỗi đó.
Đây không phải độ chính xác của Groq hay kết quả OCR thật.

Báo cáo chỉ rõ: sai trường nào, đọc thừa/thiếu dòng, tự điền trường không có,
trạng thái API/schema, điểm theo bố cục và số case chưa chạy. INV-003 đổi thứ tự
dòng nhưng nội dung giữ nguyên nên vẫn được tính đúng. INV-011 có thể trích xuất
đúng hoàn toàn mà vẫn cần người xem vì phép tính trên nguồn sai.

Nếu báo cáo cùng tên đã tồn tại, dùng `--resume` hoặc đường dẫn mới:

```powershell
.\.venv\Scripts\python.exe benchmark.py run --mode replay --split all --resume
```

## 4. Mở giao diện mới

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8504
```

Mở http://127.0.0.1:8504. Nếu server cũ đang chạy, dừng nó bằng Ctrl+C trước khi
khởi động lại. Nếu trình duyệt đang mở trang cũ, tải lại trang.

Giao diện mặc định có mẫu HD-0006:

1. Bấm **Nạp đáp án mẫu**. Đây là dữ liệu chuẩn bị sẵn, không gọi AI.
2. Đổi tổng từ 605000 thành 600000 rồi bấm **Lưu thay đổi**.
3. Kiểm tra cảnh báo; không thể xác nhận duyệt bản sai này.
4. Khôi phục 605000, bấm **Lưu thay đổi**.
5. Đối chiếu dữ liệu với ảnh, tích xác nhận và bấm **Xác nhận duyệt hóa đơn**.

Việc chỉ tích checkbox chưa duyệt hóa đơn; phải bấm nút xác nhận. Nếu sửa dữ liệu
đã lưu sau đó, trạng thái duyệt bị hủy và phải đối chiếu lại.

## 5. Thử nhiều hóa đơn và tải gói kết quả

Trong sidebar, chọn mẫu INV-005 rồi bấm **Thêm mẫu vào hàng đợi**. Nạp đáp án mẫu,
đối chiếu và duyệt mẫu này. Cũng có thể tải lên nhiều ảnh JPG/PNG được phép xử lý.
Ứng dụng chỉ gọi AI cho hóa đơn đang chọn khi bạn bấm nút đọc ảnh.

Phần cuối trang cho biết có bao nhiêu hóa đơn đã duyệt và còn bao nhiêu chưa xuất.
Bấm **Tải gói đã duyệt (ZIP)** để nhận:

- `invoices.csv`: một dòng mỗi hóa đơn, tổng tiền không lặp theo dòng hàng.
- `line_items.csv`: các dòng hàng có document_id liên kết với hóa đơn.
- `review_audit.json`: ảnh nguồn/hash, bản AI gốc nếu có, bản đã sửa, người duyệt,
  thời điểm và danh sách hóa đơn chưa xuất.

Nếu chỉ duyệt HD-0006 và INV-005, invoices.csv có 2 dòng hóa đơn, tổng cộng 770000
VND. Không cộng các cột tổng tiền lặp trong CSV đơn lẻ kiểu cũ.

Ảnh giống hệt nhau với tên file khác không được thêm thành bản thứ hai. Nếu hai
ảnh khác nhau lại có cùng người bán/số/ngày hóa đơn, gói xuất sẽ bị chặn. Chọn bản
trùng và bấm **Hủy duyệt hóa đơn này** để loại nó khỏi gói đã duyệt.

Bản nháp nằm trong phiên Streamlit, chưa có cơ sở dữ liệu lưu lâu dài. Tải ZIP trước
khi kết thúc phiên. Tên người duyệt là nhãn tự khai báo, chưa có đăng nhập xác thực.

## 6. Thử trích xuất ảnh thật bằng Groq

Nhập API key và model hỗ trợ ảnh trong sidebar. Bấm **Đọc ảnh bằng Groq**. Ảnh
được gửi tới Groq và dùng hạn mức tài khoản; không có lệnh nào tự gửi cả hàng đợi.

Phần **Bản AI gốc và những trường đã sửa** cho thấy JSON trích xuất trước chỉnh sửa.
Sửa dữ liệu không làm thay đổi bản gốc. Khi lần gọi mới lỗi, bản nháp cũ được giữ,
thông báo lỗi xuất hiện và trạng thái duyệt bị hủy; đó không phải một kết quả AI mới.

Thiếu ngày/số hóa đơn hoặc tổng in sai cần xem nguồn bổ sung hay loại khỏi xuất;
không tự bịa dữ liệu chỉ để vượt qua kiểm tra.

## 7. Đánh giá live trên 12 hóa đơn dev

Đây là bước cần làm để có kết quả trích xuất thật. Chưa có điểm live trên bộ 20
hóa đơn được tạo ra trong quá trình nâng cấp này.

```powershell
$invoiceKey = Read-Host "Groq API key" -AsSecureString
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', $invoiceKey).Password
.\.venv\Scripts\python.exe benchmark.py run --mode live --split dev --max-requests 3 --output reports/live-dev-v2.json
```

Mỗi lần chỉ gọi tối đa 3 request mới. Xem processed_cases, not_run và coverage;
3/12 chưa phải kết quả đầy đủ cho 12 case. Chạy tiếp, giữ nguyên các tham số:

```powershell
.\.venv\Scripts\python.exe benchmark.py run --mode live --split dev --max-requests 3 --output reports/live-dev-v2.json --resume
```

Các case đã xử lý không bị gọi lại, kể cả case lỗi. Gặp HTTP 429 thì công cụ lưu
tiến độ và dừng ngay. Kiểm tra hạn mức/thời gian khôi phục trên Groq trước khi thử lại.
Muốn thử lại những case lỗi một cách có chủ đích:

```powershell
.\.venv\Scripts\python.exe benchmark.py run --mode live --split dev --max-requests 3 --output reports/live-dev-v2.json --resume --retry-errors
```

Lịch sử lần thử trước vẫn được giữ. Không gọi lại case đã thành công để chọn kết
quả đẹp hơn. Khi đổi model/prompt/code/đáp án, dùng file output mới; resume sẽ từ
chối trộn hai cấu hình khác nhau.

```powershell
Start-Process reports/live-dev-v2.html
Get-Content reports/live-dev-v2.json -Encoding utf8
```

Quan sát hai loại số đo riêng:

- Tỷ lệ đúng toàn hóa đơn trên các case đã xử lý, bao gồm cả case gọi API lỗi.
- Điểm từng trường chỉ trên phản hồi hợp lệ, kèm số request/schema lỗi và coverage.

Không lấy điểm từng trường cao rồi bỏ qua các hóa đơn mà API không xử lý được.

## 8. Sau khi sửa lỗi trên dev, kiểm tra test

Cố định code/model/prompt, rồi chạy bộ test 8 hóa đơn:

```powershell
.\.venv\Scripts\python.exe benchmark.py run --mode live --split test --max-requests 2 --output reports/live-test-v2.json
.\.venv\Scripts\python.exe benchmark.py run --mode live --split test --max-requests 2 --output reports/live-test-v2.json --resume
```

Tiếp tục resume tới khi not_run bằng 0. Không dùng điểm test để sửa prompt rồi
tuyên bố vẫn đang đánh giá trên một tập chưa thấy; nếu đã dùng test để sửa thì cần
một tập kiểm tra mới. Bộ này vẫn nhỏ và tổng hợp, phải ghi rõ khi viết CV/case study.

Sau khi xong:

```powershell
Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue
Remove-Variable invoiceKey -ErrorAction SilentlyContinue
```

## 9. Những gì có thể ghi vào portfolio lúc này

Đã triển khai: hàng đợi 20 ảnh, kiểm tra số tiền, bản AI gốc/bản sửa, duyệt gắn với
nội dung, xuất CSV tách bảng, 20 hóa đơn tổng hợp/5 bố cục, benchmark có resume và
40 tests. Chưa được tuyên bố đạt độ chính xác OCR cụ thể trên bộ mới trước khi có
báo cáo live hoàn chỉnh. Chưa hỗ trợ PDF, phân quyền nhiều người hay lưu trữ lâu dài.

Tài liệu nguồn và báo cáo có thể chứa nội dung nhạy cảm. reports/ và private_data/
đã bị loại khỏi Git; CI chỉ dùng dữ liệu giả lập có sẵn.
