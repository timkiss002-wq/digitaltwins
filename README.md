# Bảng điều khiển giám sát giao thông Digital Twin

Bảng điều khiển giám sát giao thông được xây dựng bằng Flask, sử dụng YOLOv8 để theo dõi đối tượng và OpenCV để đếm các phương tiện đi qua một đường ảo. Thông tin phương tiện được phát hiện cùng các chỉ số giao thông theo thời gian được lưu trong SQLite và hiển thị trên trình duyệt.


## Tính năng

- Phát hiện và theo dõi phương tiện bằng YOLOv8.
- Phân loại ô tô, xe máy, xe buýt và xe tải.
- Đếm phương tiện đi vào khu vực giám sát bằng đường ảo.
- Lưu các chỉ số giao thông theo từng giây vào SQLite.
- Biểu đồ giao thông trực tiếp thoeo thời gian thực.
- Hiển thị dữ liệu cho bốn khu vực đỗ xe.
- Tùy chọn mở cửa sổ xem trước OpenCV để giám sát cục bộ.
- API Flask gọn nhẹ cung cấp dữ liệu giao thông.

## Công nghệ sử dụng

- Python 3.9 trở lên
- Flask
- Ultralytics YOLOv8
- OpenCV
- SQLite
- HTML, CSS và JavaScript
- Chart.js và Lucide Icons thông qua CDN

## Cấu trúc dự án

```text
.
├── dashboard.py                         # Route Flask và worker xử lý YOLO
├── database.py                          # Kết nối SQLite và các hàm lưu trữ dữ liệu
├── main.py                              # Điểm khởi chạy ứng dụng
├── yolov8n.pt                           # Trọng số mô hình YOLOv8
├── Vehicle Dataset Sample 2 [JqhdBCCUVyQ].mp4
├── traffic_monitor.db                   # Cơ sở dữ liệu SQLite được tạo tự động
├── static/
│   ├── script.js                        # Xử lý giao diện và biểu đồ
│   └── style.css                        # Kiểu hiển thị của bảng điều khiển
└── templates/
    └── web.html                         # Trang bảng điều khiển
```

## Yêu cầu trước khi cài đặt

1. Cài đặt Python 3.9 trở lên.
2. Đảm bảo dự án có file mô hình YOLO và file video đầu vào, hoặc cung cấp đường dẫn thay thế thông qua biến môi trường.
3. Trên Windows, cài đặt các thư viện Python trong môi trường ảo để giữ môi trường dự án độc lập.

## Cài đặt

Từ thư mục dự án, tạo và kích hoạt môi trường ảo:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Cài đặt các gói cần thiết:

```powershell
python -m pip install --upgrade pip
python -m pip install flask opencv-python ultralytics
```

## Chạy ứng dụng

Khởi động ứng dụng bằng lệnh:

```powershell
python main.py
```

Theo mặc định, máy chủ lắng nghe tại cổng `5000`. Mở bảng điều khiển tại địa chỉ:

```text
http://127.0.0.1:5000
```

Khi khởi động, ứng dụng sẽ khởi tạo cơ sở dữ liệu, xóa các chỉ số giao thông của lần chạy trước và chạy worker xử lý YOLO ở chế độ nền.

## Cấu hình

Ứng dụng hỗ trợ các biến môi trường sau:

| Biến | Giá trị mặc định | Mô tả |
| --- | --- | --- |
| `PORT` | `5000` | Cổng được máy chủ Flask sử dụng. |
| `TRAFFIC_VIDEO` | `Vehicle Dataset Sample 2 [JqhdBCCUVyQ].mp4` | Đường dẫn đến video giao thông đầu vào. |
| `YOLO_MODEL` | `yolov8n.pt` | Đường dẫn đến trọng số mô hình YOLO. |
| `SHOW_VIDEO` | `0` | Đặt thành `1` để hiển thị cửa sổ xem trước OpenCV. |

Ví dụ:

```powershell
$env:PORT = "8000"
$env:SHOW_VIDEO = "1"
python main.py
```

Với đường dẫn chứa khoảng trắng, hãy đặt giá trị trong dấu ngoặc kép:

```powershell
$env:TRAFFIC_VIDEO = "C:\data\traffic footage.mp4"
```

## API

### `GET /`

Cung cấp trang bảng điều khiển trên trình duyệt.

### `GET /api/traffic-data`

Trả về các chỉ số giao thông được ghi nhận trong hai giờ gần nhất:

```json
{
  "labels": ["09:00:01", "09:00:02"],
  "values": [4, 6]
}
```

Giao diện gọi endpoint này mỗi 2,5 giây và hiển thị 60 điểm dữ liệu mới nhất.

## Cơ sở dữ liệu

Dữ liệu SQLite được lưu trong `traffic_monitor.db` tại thư mục dự án. Ứng dụng tự động tạo hai bảng:

- `vehicle_entries`: thông tin từng lượt phương tiện đi qua, gồm ID theo dõi, loại phương tiện và thời điểm ghi nhận.
- `traffic_metrics`: tổng số phương tiện được ghi nhận khoảng mỗi giây.

Các chỉ số giao thông được xóa khi `main.py` khởi động. Lịch sử phương tiện vẫn được giữ lại, trừ khi cơ sở dữ liệu bị xóa hoặc được dọn dẹp thủ công.

## Cơ chế phát hiện

Worker theo dõi các lớp YOLO sau:

- Ô tô
- Xe máy
- Xe đạp


Một phương tiện được tính khi tâm của nó vượt qua đường ngang tại `y = 400` hoặc đi vào vùng biên được cấu hình quanh đường. Mỗi ID theo dõi chỉ được tính một lần trong một lần xử lý.

## Xử lý sự cố

- **Không thể mở video:** Kiểm tra `TRAFFIC_VIDEO`, xác nhận file tồn tại và đảm bảo OpenCV đọc được định dạng của file.
- **Không thể tải mô hình:** Kiểm tra `YOLO_MODEL` và đảm bảo file trọng số mô hình tồn tại, có thể đọc được.
- **Bảng điều khiển không có dữ liệu biểu đồ mới:** Xác nhận worker đang chạy và video có các khung hình đọc được.
- **Không xuất hiện cửa sổ xem trước OpenCV:** Đặt `SHOW_VIDEO` thành `1` và chạy ứng dụng trong phiên làm việc có giao diện máy tính.
- **Xử lý chậm:** Sử dụng video đầu vào nhỏ hơn, mô hình nhẹ hơn hoặc máy tính có GPU phù hợp.

## Ghi chú phát triển

Dự án này phục vụ mục đích trình demo. Khi triển khai thực tế, cần bổ sung xác thực, ghi log có cấu trúc, kiểm tra dữ liệu đầu vào, quản lý vòng đời mô hình và video, máy chủ WSGI dành cho môi trường production.
