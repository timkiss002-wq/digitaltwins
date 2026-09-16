"""Kiểm thử bản tổng kết cuối lượt (simulation/summary.py).

Dựng engine từ ``tests/conftest.build_params`` (λ = 0 nên tất định) và chỉnh tay các bộ đếm,
hoặc lái vài tick thật — không sleep, không chạm DB.
"""

from __future__ import annotations

import json

import pytest

import simulation.engine as E
from simulation import config as C
from simulation.models import IN, Vehicle
from simulation.summary import build_summary
from tests.conftest import build_params

SUMMARY_KEYS = {"stop_reason", "sim_seconds", "paused_seconds", "totals", "lots"}
TOTAL_KEYS = {
    "admitted", "departed", "rejected", "redirected",
    "overload_events", "overload_seconds", "near_full_events", "near_full_seconds",
}
LOT_KEYS = {
    "lot_id", "label", "capacity", "final_occupancy", "admitted", "departed",
    "redirected_in", "redirected_out", "rejected", "near_full_events", "near_full_seconds",
    "avg_wait_s", "lanes",
}
LANE_KEYS = {
    "lane_id", "direction", "processed_total", "avg_wait_s", "overload_events", "overload_seconds",
}


def make_engine(**kwargs) -> E.SimulationEngine:
    return E.SimulationEngine(build_params(**kwargs))


def lot_summary(summary: dict, lot_id: str) -> dict:
    return next(lot for lot in summary["lots"] if lot["lot_id"] == lot_id)


def lanes_by_id(lot: dict) -> dict[str, dict]:
    return {lane["lane_id"]: lane for lane in lot["lanes"]}


# --------------------------------------------------------------------------- hình dạng
def test_summary_shape_and_lot_order():
    engine = make_engine()
    summary = build_summary(engine, 0.0, "user_request")

    assert set(summary) == SUMMARY_KEYS
    assert set(summary["totals"]) == TOTAL_KEYS
    assert summary["stop_reason"] == "user_request"
    assert summary["sim_seconds"] == 0
    assert summary["paused_seconds"] == 0.0
    assert [lot["lot_id"] for lot in summary["lots"]] == list(C.LOT_CAPACITIES)

    for lot in summary["lots"]:
        assert set(lot) == LOT_KEYS
        assert lot["label"] == C.LOT_LABELS[lot["lot_id"]]
        assert lot["capacity"] == C.LOT_CAPACITIES[lot["lot_id"]]
        assert len(lot["lanes"]) == len(C.LANES_PER_LOT)
        for lane in lot["lanes"]:
            assert set(lane) == LANE_KEYS


def test_totals_add_up_across_lots_and_lanes():
    engine = make_engine()
    a, bc = engine.lots["A"], engine.lots["BC"]

    a.admitted, a.departed, a.rejected = 3, 2, 1
    a.redirected_in, a.redirected_out = 2, 5
    a.near_full_events, a.near_full_seconds = 1, 5
    bc.admitted, bc.departed, bc.rejected = 7, 4, 0
    bc.redirected_in, bc.redirected_out = 3, 0
    bc.near_full_events, bc.near_full_seconds = 2, 11
    a.lanes["L1"].overload_events, a.lanes["L1"].overload_seconds = 2, 6
    bc.lanes["L3"].overload_events, bc.lanes["L3"].overload_seconds = 1, 3

    summary = build_summary(engine, 1.5, "user_request")

    assert summary["totals"] == {
        "admitted": 10,          # 3 + 7
        "departed": 6,           # 2 + 4
        "rejected": 1,
        "redirected": 5,         # tổng redirected_in
        "overload_events": 3,
        "overload_seconds": 9,
        "near_full_events": 3,
        "near_full_seconds": 16,
    }
    assert summary["paused_seconds"] == 1.5
    assert lot_summary(summary, "A")["final_occupancy"] == 0
    assert lot_summary(summary, "A")["redirected_out"] == 5


# --------------------------------------------------------------------------- đồng hồ
def test_sim_seconds_is_frozen_at_stop():
    engine = make_engine()
    for _ in range(3):
        engine.tick()

    summary = build_summary(engine, 0.0, "user_request")
    assert summary["sim_seconds"] == 3

    for _ in range(2):                                # dù engine chạy thêm...
        engine.tick()
    assert summary["sim_seconds"] == 3                # ...bản tổng kết đã đóng băng
    assert build_summary(engine, 0.0, "user_request")["sim_seconds"] == 5


def test_paused_seconds_is_carried_and_rounded():
    engine = make_engine()
    assert build_summary(engine, 12.345, "user_request")["paused_seconds"] == 12.35
    assert build_summary(engine, None, "user_request")["paused_seconds"] == 0.0  # type: ignore[arg-type]


def test_stop_reason_is_carried():
    assert build_summary(make_engine(), 0.0, "max_duration")["stop_reason"] == "max_duration"


# --------------------------------------------------------------------------- chờ trung bình
def test_lot_avg_wait_is_weighted_by_processed_vehicles():
    """1 xe chờ 0 s ở L1 + 3 xe chờ 4 s ở L2 ⇒ 12/4 = 3,0 s (KHÔNG phải (0+4)/2 = 2,0 s)."""
    engine = make_engine()
    a = engine.lots["A"]
    a.lanes["L1"].wait_samples, a.lanes["L1"].wait_sum_s = 1, 0.0
    a.lanes["L2"].wait_samples, a.lanes["L2"].wait_sum_s = 3, 12.0

    lot = lot_summary(build_summary(engine, 0.0, "user_request"), "A")

    assert lot["avg_wait_s"] == 3.0
    assert lot["avg_wait_s"] != pytest.approx(2.0)
    lanes = lanes_by_id(lot)
    assert lanes["L1"]["avg_wait_s"] == 0.0
    assert lanes["L2"]["avg_wait_s"] == 4.0


def test_avg_wait_is_none_when_nothing_was_processed():
    summary = build_summary(make_engine(), 0.0, "user_request")
    assert all(lot["avg_wait_s"] is None for lot in summary["lots"])
    assert all(
        lane["avg_wait_s"] is None for lot in summary["lots"] for lane in lot["lanes"]
    )


def test_summary_after_real_ticks_reflects_processed_vehicle():
    engine = make_engine()
    lane = engine.lots["A"].lanes["L1"]
    lane.queue.append(Vehicle(1, 0, IN, "A"))         # 1 xe xếp hàng ở t = 0

    for _ in range(5):                                # vào cổng ở t = 0, xong ở t = 4
        engine.tick()

    summary = build_summary(engine, 0.0, "stop_test")
    lot = lot_summary(summary, "A")
    l1 = lanes_by_id(lot)["L1"]

    assert summary["sim_seconds"] == 5
    assert lot["admitted"] == 1
    assert lot["final_occupancy"] == 1
    assert l1["processed_total"] == 1
    assert l1["avg_wait_s"] == 0.0                    # vào cổng ngay nên chờ 0 s
    assert lot["avg_wait_s"] == 0.0
    assert summary["totals"]["admitted"] == 1


def test_lane_overload_counters_are_per_lane():
    engine = make_engine()
    a = engine.lots["A"]
    a.lanes["L2"].overload_events, a.lanes["L2"].overload_seconds = 4, 30

    lot = lot_summary(build_summary(engine, 0.0, "user_request"), "A")
    assert lanes_by_id(lot)["L2"]["overload_events"] == 4
    assert lanes_by_id(lot)["L2"]["overload_seconds"] == 30
    assert lanes_by_id(lot)["L1"]["overload_events"] == 0


# --------------------------------------------------------------------------- JSON
def test_summary_is_json_serialisable():
    engine = make_engine()
    engine.lots["A"].admitted = 5
    engine.lots["A"].lanes["L1"].wait_samples, engine.lots["A"].lanes["L1"].wait_sum_s = 2, 8.0

    text = json.dumps(build_summary(engine, 3.5, "user_request"), ensure_ascii=False)

    assert "user_request" in text
    assert '"avg_wait_s": 4.0' in text
