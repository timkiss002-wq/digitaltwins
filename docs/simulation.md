# Mô phỏng quản lý bãi xe

Module mô phỏng 4 nhà xe (A, BC, D, KTX) × 4 làn (L1–L4), chạy song song với bảng điều khiển
camera trong cùng ứng dụng Flask. Toàn bộ logic mô phỏng nằm trong `simulation/` và **không**
phụ thuộc `ultralytics`/`opencv`.

- Trang mô phỏng: `http://127.0.0.1:5000/sim`
- Bảng điều khiển camera (giữ nguyên): `http://127.0.0.1:5000/`

## Chạy nhanh

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install flask pytest            # chỉ cần cho phần mô phỏng
ENABLE_CV=0 python main.py                    # không cần cài thư viện thị giác
# mở http://127.0.0.1:5000/sim
```

Chạy kiểm thử:

```bash
.venv/bin/python -m pytest tests -q
```

## Chạy kiểm tra chấp nhận (acceptance)

Kịch bản này bắt đầu một lượt chạy thật qua HTTP rồi kiểm tra: đồng hồ chạy theo thời gian thực,
cảnh báo tắc nghẽn làn, chuyển hướng làn (chờ xả hết), điều hướng khi hết chỗ, từ chối khi toàn hệ
thống hết chỗ, tạm dừng/tiếp tục, dừng + tổng kết, và log trong SQLite (đúng 16 dòng làn/giây):

```bash
# Dùng dung tích thu nhỏ để thấy "hết chỗ" trong ~2 phút (xem SIM_CAPACITY_SCALE bên dưới)
SIM_CAPACITY_SCALE=0.01 ENABLE_CV=0 PORT=5055 .venv/bin/python main.py &
.venv/bin/python tools/acceptance_simulation.py --base http://127.0.0.1:5055
```

## Hệ số dung tích cho demo: `SIM_CAPACITY_SCALE`

Với `τ = 4` giây/xe và 2 làn vào, một nhà xe 1100 chỗ cần **ít nhất ~18 phút** mới đầy (tối đa
4 làn vào: `1100 × 4 / 4 = 1100` giây). Vì *Description.md* quy định 1 giây mô phỏng = 1 giây thực,
trạng thái "hết chỗ → điều hướng/từ chối" không thể quan sát nhanh ở dung tích thật.

`SIM_CAPACITY_SCALE` (mặc định `1.0` = đúng số liệu *Description.md*) cho phép thu nhỏ dung tích
để demo/kiểm thử, ví dụ `0.01` → A=11, BC=17, D=17, KTX=20 (đầy sau ~1-2 phút). Dung tích thực tế
đang dùng luôn được ghi vào `sim_lot_metrics.capacity` và `params_json` không đổi, nên có thể biết
một lượt chạy có dùng chế độ demo hay không qua cột `capacity`.

## Thông số miền

| Thông số | Giá trị | Ý nghĩa |
|---|---|---|
| `PROCESSING_TIME_S` | 4,0 giây | τ — thời gian xử lý 1 xe tại cổng (= 15 xe/phút, theo `parking_lot_simulation.md`) |
| `OVERCROWD_THRESHOLD` | 20 xe | Làn bị coi là tắc nghẽn khi số xe **chờ trong làn** ≥ 20 |
| `NEAR_FULL_RATIO` | 0,90 | Nhà xe bị coi là sắp đầy khi tỉ lệ lấp đầy ≥ 90% |
| `TICK_S` | 1,0 giây | 1 giây mô phỏng = 1 giây thực |
| `LAMBDA_MIN` / `LAMBDA_MAX` | 0,01 / 2,0 xe/giây/làn | Khoảng λ hợp lệ |
| `MAX_REDIRECT_HOPS` | 3 | Số lần điều hướng tối đa trước khi từ chối xe |

Dung tích: A = 1100, BC = 1750, D = 1750, KTX = 2000 xe.

## Kịch bản có sẵn

| Mã | Tên | λ vào / λ ra (mỗi làn) |
|---|---|---|
| `binh_thuong` | Bình thường (ngoài giờ cao điểm) | 0,15 / 0,15 |
| `cao_diem_vao` | Cao điểm đầu tiết 1–2 (xe vào) | 0,50 / 0,05 |
| `cao_diem_ra` | Cao điểm cuối tiết 3–4 (xe ra) | 0,05 / 0,50 |
| `qua_tai_A` | Nhà xe A quá tải (kiểm tra điều hướng) | 0,10 / 0,10; riêng A: 2,00 vào |
| `mot_lan_vao` | Nhà xe KTX chỉ còn 1 làn vào (L1 vào, L2–L4 ra) | 0,40 / 0,10 |

> **Thời gian quan sát:** vì 1 giây mô phỏng = 1 giây thực, một nhà xe 1100 chỗ cần vài phút
> mới đầy. Để thấy **điều hướng** và **từ chối xe**, dùng `qua_tai_A` (λ vào 2 xe/giây/làn,
> năng lực xử lý chỉ 0,25 xe/giây/làn) và chạy vài phút. `cao_diem_vao` đủ để thấy tắc nghẽn làn
> (λ 0,5 > năng lực 0,25) sau ~40 giây, nhưng chưa làm đầy nhà xe.

## Giao diện (tiếng Việt, cập nhật bằng polling 2 giây)
- Đồng hồ mô phỏng, trạng thái lượt chạy, nút **Bắt đầu / Tạm dừng / Tiếp tục / Dừng & tổng kết**.
- 6 chỉ số toàn hệ thống: xe đã vào, xe đã ra, xe đang chờ, chờ trung bình, điều hướng, bị từ chối.
- 4 thẻ nhà xe: vòng sức chứa (%), tồn kho, số xe vào/ra/điều hướng/từ chối.
- Bảng làn: hướng (VÀO/RA), trạng thái (`ĐANG MỞ`, `ĐÃ ĐÓNG`, `ĐANG XẢ HÀNG CHỜ`,
  `CHỜ CHUYỂN HƯỚNG`), xe chờ, tốc độ đến, tốc độ xử lý, thời gian chờ trung bình, đã xử lý;
  nút **Đóng/Mở làn** và **Chuyển thành làn vào/ra**.
- Cảnh báo tắc nghẽn làn (≥ 20 xe) và nhà xe sắp đầy (≥ 90%).
- Nhật ký điều phối và biểu đồ hàng chờ theo làn (cửa sổ 120 giây).
- Hộp thoại tổng kết cuối lượt.

## API

| Method | Đường dẫn | Mô tả |
|---|---|---|
| GET | `/api/sim/scenarios` | Danh sách kịch bản `{scenarios:[{id,name,description}]}` |
| POST | `/api/sim/runs` | Bắt đầu lượt: `{"scenario_id": "..."}` hoặc cấu hình tự chọn (kèm `confirm_lopsided`) |
| GET | `/api/sim/runs/current` | Payload polling: `{status, run_id, params, snapshot, paused_seconds}` hoặc `{"status":"idle"}` |
| GET | `/api/sim/runs/current/events?limit=200` | Nhật ký sự kiện của lượt đang chạy |
| GET | `/api/sim/runs` | Lịch sử các lượt |
| POST | `/api/sim/runs/<id>/pause` \| `/resume` | Tạm dừng / tiếp tục |
| POST | `/api/sim/runs/<id>/stop` | Dừng và trả về bản tổng kết |
| POST | `/api/sim/runs/<id>/lanes/<lot>/<lane>/open` \| `/close` | Mở/đóng làn (đóng làn cuối cùng → 409) |
| POST | `/api/sim/runs/<id>/lanes/<lot>/<lane>/convert` | `{"target":"in"\|"out"}` — chỉ đổi hướng khi làn đã xả hết |
| GET | `/api/sim/runs/<id>/summary` | Bản tổng kết đã lưu (404 nếu lượt chưa kết thúc) |
| GET | `/api/sim/runs/<id>/series?lot_id=A&lane_id=L1&window=120` | Chuỗi `{labels, values}` cho biểu đồ |

Lỗi trả về dạng `{"error_code": "...", "message": "..." (tiếng Việt), "details": {...}}`.
Mã lỗi: `unknown_lot`, `unknown_lane`, `unknown_scenario`, `duplicate_lane`,
`lambda_out_of_range`, `min_open_lanes`, `lopsided_needs_confirm`, `conversion_pending`,
`already_direction`, `bad_direction`, `no_active_run`, `run_already_active`.

Tên nhà xe trong tham số được chuẩn hoá: `BCD → BC`, `E → D`, `1 → A`, `2 → BC`, `3 → KTX`, `4 → D`.

## Cơ sở dữ liệu

Năm bảng mới trong `traffic_monitor.db` (bảng camera giữ nguyên):

| Bảng | Nội dung |
|---|---|
| `sim_runs` | Tham số (`params_json`), trạng thái (`running`/`paused`/`stopped`/`interrupted`), thời lượng, tổng kết (`summary_json`), lý do dừng |
| `sim_lane_metrics` | 1 dòng / làn / giây: hướng, trạng thái cổng, xe chờ, đã xử lý, tốc độ đến, chờ TB, cờ tắc nghẽn |
| `sim_lot_metrics` | 1 dòng / nhà xe / giây: tồn kho, sức chứa, tỉ lệ lấp đầy, cờ sắp đầy |
| `sim_events` | Sự kiện rời rạc: `start`, `pause`, `resume`, `stop`, `open`, `close`, `convert_request`, `convert_done`, `redirect`, `reject`, `overcrowd_start/end`, `near_full_start/end` |
| `sim_vehicles` | 1 dòng / xe xử lý xong: thời gian chờ, thời gian xử lý, tổng thời gian |

Kiểm tra nhanh:

```bash
sqlite3 traffic_monitor.db "SELECT COUNT(*) FROM sim_lane_metrics WHERE run_id = 1"   -- = 16 × số giây
sqlite3 traffic_monitor.db "SELECT type, COUNT(*) FROM sim_events WHERE run_id = 1 GROUP BY type"
```

Dọn dữ liệu cũ (giữ 7 ngày):

```sql
DELETE FROM sim_lane_metrics WHERE run_id IN (SELECT id FROM sim_runs WHERE started_at < date('now','-7 day'));
DELETE FROM sim_lot_metrics  WHERE run_id IN (SELECT id FROM sim_runs WHERE started_at < date('now','-7 day'));
DELETE FROM sim_vehicles     WHERE run_id IN (SELECT id FROM sim_runs WHERE started_at < date('now','-7 day'));
```

## Quyết định thiết kế

- **Đóng làn = ngừng nhận xe mới, vẫn xả hết hàng chờ** (trạng thái `đang xả hàng chờ`), để không
  bỏ sót xe và để quy tắc "mỗi nhà xe giữ ít nhất 1 làn mở" luôn có nghĩa.
- **Chuyển hướng làn** chỉ hoàn tất khi làn không còn xe chờ và không còn xe tại cổng; UI hiển thị
  `CHỜ CHUYỂN HƯỚNG` trong lúc xả.
- **Điều hướng xe**: khi nhà xe đích hết chỗ, xe được đưa vào làn vào ngắn nhất của nhà xe còn chỗ
  nhất (tỉ lệ lấp đầy thấp nhất), **giữ nguyên mốc thời gian xếp hàng** nên thời gian chờ vẫn được
  tính liên tục; tối đa 3 lần điều hướng rồi **từ chối** và ghi log.
- **Sức chứa**: khi quyết định nhận xe, phần chỗ trống đã trừ cả xe đang trong 4 giây xử lý tại
  cổng, nên tồn kho không bao giờ vượt sức chứa.
- **Tắc nghẽn làn** tính theo số xe **đang chờ** trong làn; xe đang ở cổng được báo là `đang xử lý`.
- **λ của làn ra** bị chặn theo số xe nhà xe đang thực chứa (không thể xuất nhiều xe hơn số đang có).
- **Trạng thái mô phỏng nằm trong bộ nhớ**: khởi động lại server thì lượt đang chạy bị đánh dấu
  `interrupted` (tham số và số liệu đã ghi vẫn còn trong DB). Chưa hỗ trợ chạy tiếp sau khi restart.
- **Ghi log**: mỗi giây một transaction (16 dòng làn + 4 dòng nhà xe + sự kiện + xe xong);
  WAL + `busy_timeout` để không tranh chấp với worker camera.
