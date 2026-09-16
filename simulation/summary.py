"""Bản tổng kết cuối lượt mô phỏng (``build_summary``).

Payload trả về cho ``POST /api/sim/runs/<id>/stop`` và ``GET /api/sim/runs/<id>/summary``,
đồng thời được lưu vào cột ``sim_runs.summary_json``. Mọi giá trị đều JSON-serialisable.
"""

from __future__ import annotations

from . import config as C


def _round_or_none(value: float, samples: int) -> float | None:
    """Thời gian chờ trung bình đã làm tròn, ``None`` khi chưa xử lý xe nào."""
    return round(value / samples, 2) if samples else None


def _lot_avg_wait(lot) -> float | None:
    """Chờ trung bình của nhà xe — BÌNH QUÂN CÓ TRỌNG SỐ theo số xe đã xử lý.

    Cộng toàn bộ ``wait_sum_s`` / ``wait_samples`` của các làn trong nhà xe (không phải
    trung bình của các trung bình), nên làn xử lý nhiều xe có ảnh hưởng đúng mức.
    ``None`` nếu nhà xe chưa xử lý xe nào.
    """
    samples = sum(lane.wait_samples for lane in lot.lanes.values())
    if not samples:
        return None
    return round(sum(lane.wait_sum_s for lane in lot.lanes.values()) / samples, 2)


def build_summary(engine, paused_seconds: float, stop_reason: str) -> dict:
    """Dựng bản tổng kết từ trạng thái hiện tại của ``engine`` (đọc, không sửa)."""
    lots = []
    for lot in engine.lots.values():
        lanes = []
        for lane in lot.lanes.values():
            lanes.append(
                {
                    "lane_id": lane.lane_id,
                    "direction": lane.direction,
                    "processed_total": lane.processed_total,
                    "avg_wait_s": _round_or_none(lane.wait_sum_s, lane.wait_samples),
                    "overload_events": lane.overload_events,
                    "overload_seconds": lane.overload_seconds,
                }
            )
        lots.append(
            {
                "lot_id": lot.lot_id,
                "label": C.LOT_LABELS[lot.lot_id],
                "capacity": lot.capacity,
                "final_occupancy": lot.occupancy,
                "admitted": lot.admitted,
                "departed": lot.departed,
                "redirected_in": lot.redirected_in,
                "redirected_out": lot.redirected_out,
                "rejected": lot.rejected,
                "near_full_events": lot.near_full_events,
                "near_full_seconds": lot.near_full_seconds,
                "avg_wait_s": _lot_avg_wait(lot),
                "lanes": lanes,
            }
        )
    return {
        "stop_reason": stop_reason,
        "sim_seconds": int(engine.sim_time),
        "paused_seconds": round(float(paused_seconds or 0.0), 2),
        "totals": {
            "admitted": sum(lot["admitted"] for lot in lots),
            "departed": sum(lot["departed"] for lot in lots),
            "rejected": sum(lot["rejected"] for lot in lots),
            "redirected": sum(lot["redirected_in"] for lot in lots),
            "overload_events": sum(
                lane["overload_events"] for lot in lots for lane in lot["lanes"]
            ),
            "overload_seconds": sum(
                lane["overload_seconds"] for lot in lots for lane in lot["lanes"]
            ),
            "near_full_events": sum(lot["near_full_events"] for lot in lots),
            "near_full_seconds": sum(lot["near_full_seconds"] for lot in lots),
        },
        "lots": lots,
    }
