"""Hằng số và cấu hình miền cho mô phỏng bãi xe (không phụ thuộc thư viện ngoài).

Nguồn: Description.md (4 nhà xe, dung tích, quy tắc) và parking_lot_simulation.md
(τ = 4 giây/xe = 15 xe/phút/chốt, λ ≈ 0,5 xe/giây/làn trong giờ cao điểm).
"""

# ID chuẩn của 4 nhà xe và dung tích (Description.md)
LOT_CAPACITIES = {"A": 1100, "BC": 1750, "D": 1750, "KTX": 2000}

LOT_LABELS = {
    "A": "Nhà xe A (khu trung tâm)",
    "BC": "Nhà xe BC (khu giảng đường)",
    "D": "Nhà xe D (khu thể thao)",
    "KTX": "Nhà xe KTX (khu ký túc xá)",
}

LANES_PER_LOT = ("L1", "L2", "L3", "L4")
DEFAULT_DIRECTIONS = {"L1": "in", "L2": "in", "L3": "out", "L4": "out"}

# Tên gọi khác trong tài liệu/UI cũ -> ID chuẩn
LEGACY_LOT_ALIASES = {"BCD": "BC", "E": "D", "1": "A", "2": "BC", "3": "KTX", "4": "D"}

PROCESSING_TIME_S = 4.0      # τ: thời gian xử lý 1 xe tại cổng (15 xe/phút)
OVERCROWD_THRESHOLD = 20     # làn bị coi là tắc nghẽn khi có >= 20 xe chờ
NEAR_FULL_RATIO = 0.90       # nhà xe sắp đầy khi đạt >= 90% sức chứa
TICK_S = 1.0                 # 1 giây mô phỏng = 1 giây thực
LAMBDA_MIN = 0.01            # xe/giây/làn
LAMBDA_MAX = 2.0             # xe/giây/làn
ARRIVAL_WINDOW_S = 60        # cửa sổ tính tốc độ xe đến quan sát được
MAX_REDIRECT_HOPS = 3        # số lần điều hướng tối đa trước khi từ chối
POLL_INTERVAL_MS = 2000      # chu kỳ polling của frontend
EVENT_RETENTION_ROWS = 5000  # giới hạn bản ghi sự kiện trả về API
RECOMMEND_SAFETY = 1.0       # hệ số an toàn khi đề xuất số làn cần mở
