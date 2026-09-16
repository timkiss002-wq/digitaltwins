"""Kiểm tra hợp lệ tham số run + chuẩn hoá ID nhà xe (chỉ dùng thư viện chuẩn).

Mỗi quy tắc có thông báo tiếng Việt (hiển thị cho người dùng) và một mã máy
(mã lỗi) để route/UI xử lý: unknown_lot, duplicate_lane, lambda_out_of_range,
min_open_lanes, lopsided_needs_confirm.
"""

from __future__ import annotations

from . import config as C
from .models import IN


class ValidationError(Exception):
    """Lỗi tham số/kích thước không hợp lệ, kèm mã lỗi và chi tiết."""

    def __init__(self, message: str, code: str = "invalid_params", details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


def normalise_lot_id(raw: str) -> str:
    """Đưa tên nhà xe về ID chuẩn (BCD -> BC, E -> D, 1..4 -> A/BC/D/KTX)."""
    key = str(raw).strip().upper()
    return C.LEGACY_LOT_ALIASES.get(key, key)


def validate_run_params(params) -> None:
    """Kiểm tra toàn bộ quy tắc của Description.md; raise ValidationError nếu sai."""
    ids = [lot.lot_id for lot in params.lots]
    if sorted(ids) != sorted(C.LOT_CAPACITIES):
        raise ValidationError(
            "Cấu hình phải bao gồm đúng 4 nhà xe: A, BC, D, KTX.",
            "unknown_lot",
            {"lot_ids": ids},
        )
    for lot in params.lots:
        seen: set[str] = set()
        for lane in lot.lanes:
            if lane.lane_id not in C.LANES_PER_LOT:
                raise ValidationError(
                    f"Nhà xe {lot.lot_id} không có làn {lane.lane_id} "
                    f"(chỉ có {', '.join(C.LANES_PER_LOT)}).",
                    "unknown_lane",
                    {"lot_id": lot.lot_id, "lane_id": lane.lane_id},
                )
            if lane.lane_id in seen:
                raise ValidationError(
                    f"Làn trùng trong nhà xe {lot.lot_id}: {lane.lane_id}.",
                    "duplicate_lane",
                    {"lot_id": lot.lot_id, "lane_id": lane.lane_id},
                )
            seen.add(lane.lane_id)
            for value, label in ((lane.lambda_in, "λ vào"), (lane.lambda_out, "λ ra")):
                if not (C.LAMBDA_MIN <= value <= C.LAMBDA_MAX):
                    raise ValidationError(
                        f"{label} của làn {lot.lot_id}/{lane.lane_id} phải nằm trong khoảng "
                        f"{C.LAMBDA_MIN}–{C.LAMBDA_MAX} xe/giây.",
                        "lambda_out_of_range",
                        {"lot_id": lot.lot_id, "lane_id": lane.lane_id, "value": value},
                    )
        open_lanes = [lane for lane in lot.lanes if lane.open]
        if not open_lanes:
            raise ValidationError(
                f"Nhà xe {lot.lot_id} phải giữ ít nhất 1 làn mở.",
                "min_open_lanes",
                {"lot_id": lot.lot_id},
            )
        directions = {lane.direction for lane in open_lanes}
        if len(directions) < 2 and not params.allow_lopsided:
            raise ValidationError(
                f"Nhà xe {lot.lot_id} chỉ có làn {'vào' if IN in directions else 'ra'} đang mở. "
                "Cần xác nhận rõ ràng cho cấu hình lệch.",
                "lopsided_needs_confirm",
                {"lot_id": lot.lot_id, "requires_confirmation": True},
            )
