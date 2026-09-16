# Biên bản kiểm tra chấp nhận — Mô phỏng quản lý bãi xe

Ngày: 2026-09-17 · Môi trường: Linux, Python 3.11.15, Flask, SQLite (`traffic_monitor.db`)

## 1. Kiểm thử tự động

```bash
$ .venv/bin/python -m pytest tests -q
184 passed in 0.83s
```

Bao gồm: engine (49 test), store (13), validation (16), scenarios, summary, service, API,
kế thừa cấu hình và **hợp đồng API ↔ giao diện** (khoá payload mà `static/simulation.js` đọc).

## 2. Kiểm tra chấp nhận end-to-end qua HTTP

Chạy server ở chế độ demo dung tích thu nhỏ rồi chạy `tools/acceptance_simulation.py`:

```bash
SIM_CAPACITY_SCALE=0.01 ENABLE_CV=0 PORT=5055 .venv/bin/python main.py &
.venv/bin/python tools/acceptance_simulation.py --base http://127.0.0.1:5055
```

Kết quả: **33/33 bước đạt** (`TẤT CẢ BƯỚC KIỂM TRA CHẤP NHẬN ĐỀU ĐẠT`). Trích log:

```
1) Trạng thái ban đầu và danh sách kịch bản
  [ĐẠT ] polling trả 200 khi chưa có lượt chạy — {'status': 'idle'}
  [ĐẠT ] có 5 kịch bản dựng sẵn — binh_thuong,cao_diem_vao,cao_diem_ra,qua_tai_A,mot_lan_vao
2) Bắt đầu lượt với kịch bản qua_tai_A
  [ĐẠT ] POST /runs trả 201 + run_id — {'run_id': 1, 'status': 'running'}
3) Đồng hồ mô phỏng chạy theo thời gian thực
  [ĐẠT ] đồng hồ đã tiến (~5 giây) — sim_time=5
4) Cảnh báo tắc nghẽn làn (>= 20 xe chờ)
  [ĐẠT ] nhà xe A bị tắc nghẽn làn — sau 3s: ['A/L2=20']
  [ĐẠT ] cảnh báo tắc nghẽn xuất hiện trong snapshot — Làn A/L2 đang tắc nghẽn (≥20 xe chờ)
5) Chuyển hướng làn: phải xả hết hàng chờ rồi mới đổi
  [ĐẠT ] làn vào trạng thái chờ chuyển hướng — pending_conversion
  [ĐẠT ] làn A/L3 đã đổi thành làn VÀO sau khi xả hết — sau 1s
6) Hết chỗ -> điều hướng, rồi từ chối
  [ĐẠT ] có xe được điều hướng sang nhà xe khác — sau 8s, tổng điều hướng=1
  [ĐẠT ] nhà xe A đã đầy và không vượt sức chứa — 9/11
  [ĐẠT ] có xe bị từ chối khi toàn hệ thống hết chỗ — sau 56s, bị từ chối=1
7) Tạm dừng / tiếp tục
  [ĐẠT ] đồng hồ và hàng chờ đóng băng khi tạm dừng — sim_time 73 -> 73
  [ĐẠT ] tiếp tục chạy lại từ đúng trạng thái — sim_time=76
8) Dừng lượt và nhận bản tổng kết
  [ĐẠT ] tổng kết có đủ chỉ số yêu cầu — {"admitted": 102, "departed": 42, "near_full_events": 4,
        "near_full_seconds": 68, "overload_events": 6, "overload_seconds": 215,
        "redirected": 155, "rejected": 8}
9) Log trong SQLite
  [ĐẠT ] 1 dòng/làn/giây (16 × số giây) — 1216 dòng vs 16×76
  [ĐẠT ] 1 dòng/nhà xe/giây (4 × số giây) — 304 dòng vs 4×76
  [ĐẠT ] có log người dùng thao tác và điều hướng/từ chối — {"convert_done": 1,
        "convert_request": 1, "near_full_start": 4, "overcrowd_end": 1, "overcrowd_start": 7,
        "pause": 1, "redirect": 155, "reject": 8, "resume": 1, "start": 1, "stop": 1}
  [ĐẠT ] có log từng xe xử lý xong (thời gian chờ) — 144 xe
  [ĐẠT ] tồn kho trong log không vượt sức chứa — max=11
10) Nhật ký sự kiện qua API
  [ĐẠT ] API trả về nhật ký sự kiện — 50 sự kiện
```

## 3. Kiểm tra giao diện (trình duyệt thật, http://127.0.0.1:5055/sim)

Đã kiểm tra bằng trình duyệt Chromium qua CDP, chạy một lượt mô phỏng ngay trên UI:

| Bước | Kết quả |
|---|---|
| Trang tải, tiếng Việt, Chart.js nạp thành công từ CDN | ✔ `Mô Phỏng Quản Lý Bãi Xe`, `Chart.js: true` |
| Danh sách kịch bản trong dropdown | ✔ 5 kịch bản + dòng mô tả tham số khi chọn |
| Trạng thái khi chưa chạy | ✔ thẻ "CHƯA CÓ LƯỢT MÔ PHỎNG", các nút bị khoá hợp lý |
| Bấm **Bắt đầu** (kịch bản `qua_tai_A`) | ✔ trạng thái `Đang chạy`, đồng hồ `00:00:13` |
| 4 thẻ nhà xe + 16 làn hiển thị | ✔ 4 lot cards, 16 gate rows, 4 vòng sức chứa có số % |
| Cảnh báo tắc nghẽn làn | ✔ banner đỏ: "⚠ CẢNH BÁO (1) • Làn A/L2 đang tắc nghẽn (≥20 xe chờ)" |
| Đóng làn A/L2 (đang có xe chờ) | ✔ làn hiện `ĐANG XẢ HÀNG CHỜ`, tốc độ xử lý 0 xe/s, nút đổi thành `Mở làn` |
| Chuyển làn A/L3 (RA → VÀO) | ✔ hoàn tất sau khi làn rỗng, nút đổi thành `Chuyển thành làn RA` |
| Nhật ký điều phối | ✔ 50 dòng, nhãn tiếng Việt ("Làn bị tắc nghẽn · A/L3 (lane_overcrowded)") |
| Biểu đồ hàng chờ theo làn | ✔ 65 điểm dữ liệu (`Chart.getChart('sim-chart')`) |
| **Dừng & tổng kết** | ✔ hộp thoại mở với 3 bảng: theo nhà xe, chỉ số toàn hệ thống, chi tiết từng làn |
| Lỗi JavaScript | ✔ không có (`window.__errs == []`) |

Ví dụ bảng tổng kết hiển thị trên UI:

| Nhà xe | Vào | Ra | Tồn cuối | Điều hướng | Từ chối | Sự cố tắc nghẽn | Thời gian tắc nghẽn | Sự cố sắp đầy | Chờ TB |
|---|---|---|---|---|---|---|---|---|---|
| A | 13 | 2 | 11/11 | 0 | 0 | 3 | 114s | 1 | 9.8s |
| BC | 24 | 13 | 11/17 | 38 | 0 | 0 | 0s | 0 | 10.3s |
| D | 18 | 9 | 9/17 | 31 | 0 | 0 | 0s | 0 | 8.44s |
| KTX | 25 | 13 | 12/20 | 40 | 0 | 0 | 0s | 0 | 20.55s |

## 4. Ghi chú

- Bước "nhà xe A đã đầy" dùng `SIM_CAPACITY_SCALE=0.01` để hết chỗ trong ~1-2 phút; với dung tích
  thật (A = 1100) cần ~18 phút mô phỏng = ~18 phút thực theo quy định 1 giây = 1 giây.
- "Điều hướng" ở bảng theo nhà xe là số xe **được nhận từ nhà xe khác** (`redirected_in`);
  tổng toàn hệ thống đếm một lần cho mỗi xe được điều hướng.
- Kiểm thử giao diện hiện thực hiện thủ công/qua CDP; trong CI chỉ có `tests/test_frontend_contract.py`
  để bảo vệ tên khoá payload mà UI đọc.
