"""Kiểm thử tầng lưu trữ SQLite của mô phỏng bãi xe (simulation/store.py).

Mọi test đều dùng fixture ``temp_db`` (tests/conftest.py) để chỉ ghi vào file DB tạm,
không bao giờ chạm vào traffic_monitor.db thật. Snapshot/event/xe hoàn tất được dựng
THỦ CÔNG ở đây — không import simulation.engine.
"""

from __future__ import annotations

import json
import re
import sqlite3

from simulation import config as C
from simulation import store
from simulation.models import IN, OUT, LaneConfig, LotConfig, RunParams

EXPECTED_TABLES = {"sim_runs", "sim_lane_metrics", "sim_lot_metrics", "sim_events", "sim_vehicles"}
EXPECTED_INDEXES = {"idx_sim_events_run", "idx_sim_vehicles_run"}

LAMBDA_IN = 0.5      # λ vào mỗi làn vào (xe/giây) - giờ cao điểm
LAMBDA_OUT = 0.15    # λ ra mỗi làn ra (xe/giây)

CLOCK_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")


# --------------------------------------------------------------------------- #
# Dữ liệu dựng tay
# --------------------------------------------------------------------------- #
def build_params(lam_in: float = LAMBDA_IN, lam_out: float = LAMBDA_OUT) -> RunParams:
    """4 nhà xe × 4 làn với λ khác 0 để kiểm tra round-trip params_json."""
    lots = []
    for lot_id in C.LOT_CAPACITIES:
        lanes = tuple(
            LaneConfig(
                lane_id=lane_id,
                direction=C.DEFAULT_DIRECTIONS[lane_id],
                lambda_in=lam_in if C.DEFAULT_DIRECTIONS[lane_id] == IN else 0.0,
                lambda_out=lam_out if C.DEFAULT_DIRECTIONS[lane_id] == OUT else 0.0,
                open=True,
            )
            for lane_id in C.LANES_PER_LOT
        )
        lots.append(LotConfig(lot_id=lot_id, lanes=lanes))
    return RunParams(name="cao diem vao", scenario_id="cao_diem_vao", lots=tuple(lots),
                     processing_time_s=4.0, seed=4242, allow_lopsided=True, max_sim_seconds=None)


def build_gate(lane_id: str, direction: str, **overrides) -> dict:
    """1 gate đúng hợp đồng snapshot mà engine sẽ trả về."""
    gate = {
        "lane_id": lane_id, "direction": direction, "gate_state": "open", "open": True,
        "pending_direction": None, "waiting": 0, "processing": False, "processing_rate": 0.25,
        "arrival_rate": 0.0, "avg_wait_s": None, "processed_total": 0, "overloaded": False,
    }
    gate.update(overrides)
    return gate


def build_snapshot(sim_time: int, *, waiting: int = 0, occupancy: int = 0,
                   processed_total: int = 0) -> dict:
    """Snapshot giả cho 1 giây mô phỏng: 4 nhà xe × 4 làn."""
    lots = []
    for lot_id, capacity in C.LOT_CAPACITIES.items():
        gates = [
            build_gate(lane_id, C.DEFAULT_DIRECTIONS[lane_id], waiting=waiting,
                       processed_total=processed_total, overloaded=waiting >= C.OVERCROWD_THRESHOLD)
            for lane_id in C.LANES_PER_LOT
        ]
        lots.append({
            "lot_id": lot_id, "label": C.LOT_LABELS[lot_id], "capacity": capacity,
            "occupancy": occupancy, "fill_ratio": round(occupancy / capacity, 4),
            "admitted": occupancy, "departed": 0, "redirected_in": 0, "redirected_out": 0,
            "rejected": 0, "gates": gates,
        })
    return {
        "sim_time": sim_time, "clock": store.clock_label(sim_time), "totals": {},
        "lots": lots, "warnings": [],
    }


def build_event(event_type: str, sim_time: int, **overrides) -> dict:
    event = {"type": event_type, "sim_time": sim_time, "lot_id": None, "lane_id": None,
             "reason": None, "payload": {}}
    event.update(overrides)
    return event


def build_vehicle(vehicle_id: int, sim_time: int, wait_s: float = 8.0) -> dict:
    return {"vehicle_id": vehicle_id, "sim_time": sim_time, "lot_id": "A", "lane_id": "L1",
            "direction": IN, "wait_s": wait_s, "processing_s": 4.0, "total_s": wait_s + 4.0}


def scalar(db_path, sql: str, args: tuple = ()):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def rows(db_path, sql: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def object_names(db_path, kind: str) -> set[str]:
    return {name for (name,) in rows(db_path, "SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


# --------------------------------------------------------------------------- #
# Task 2.1 - Schema
# --------------------------------------------------------------------------- #
def test_init_creates_all_tables_and_indexes(temp_db):
    tables = object_names(temp_db, "table")
    assert EXPECTED_TABLES <= tables
    assert EXPECTED_INDEXES <= object_names(temp_db, "index")


def test_init_sim_db_is_idempotent(temp_db):
    store.init_sim_db()
    store.init_sim_db()                        # gọi lại nhiều lần không được ném lỗi
    assert EXPECTED_TABLES <= object_names(temp_db, "table")
    assert scalar(temp_db, "SELECT COUNT(*) FROM sim_runs") == 0


# --------------------------------------------------------------------------- #
# Task 2.2 - Vòng đời run
# --------------------------------------------------------------------------- #
def test_create_run_then_get_run_round_trips_params(temp_db):
    params = build_params()
    run_id = store.create_run(params, "cao_diem_vao")

    run = store.get_run(run_id)
    assert run["id"] == run_id
    assert run["status"] == "running"
    assert run["scenario_id"] == "cao_diem_vao"
    assert run["name"] == params.name
    assert run["seed"] == params.seed
    assert run["summary"] is None and run["ended_at"] is None
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", run["started_at"])

    stored = json.loads(scalar(temp_db, "SELECT params_json FROM sim_runs WHERE id = ?", (run_id,)))
    assert stored == params.to_json()
    lanes = {lane["lane_id"]: lane for lane in stored["lots"][0]["lanes"]}
    assert lanes["L1"]["lambda_in"] == LAMBDA_IN
    assert lanes["L1"]["lambda_out"] == 0.0
    assert lanes["L3"]["lambda_out"] == LAMBDA_OUT
    assert [lot["lot_id"] for lot in stored["lots"]] == list(C.LOT_CAPACITIES)
    assert store.get_run(9999) is None


def test_set_run_status_updates_only_that_run(temp_db):
    first = store.create_run(build_params(), "cao_diem_vao")
    second = store.create_run(build_params(), "binh_thuong")

    store.set_run_status(first, "paused")
    assert store.get_run(first)["status"] == "paused"
    assert store.get_run(second)["status"] == "running"

    store.set_run_status(first, "running")
    assert store.get_run(first)["status"] == "running"
    assert scalar(temp_db, "SELECT status FROM sim_runs WHERE id = ?", (first,)) == "running"


def test_log_event_parses_payload_and_honours_limit_ordering(temp_db):
    run_id = store.create_run(build_params(), "cao_diem_vao")
    store.log_event(run_id, 0, "start", reason="user_request", seed=4242, name="cao diem vao")
    for tick in range(1, 5):
        store.log_event(run_id, tick, "pause" if tick % 2 else "resume", lot_id="A", lane_id="L1",
                        reason="user_request", tick=tick)

    events = store.list_events(run_id)
    assert len(events) == 5
    assert [e["sim_time"] for e in events] == [4, 3, 2, 1, 0]          # mới nhất trước
    assert events[0]["type"] == "resume"
    assert events[0]["payload"] == {"tick": 4}                          # payload -> dict
    assert all(isinstance(e["payload"], dict) for e in events)
    assert events[-1]["type"] == "start"
    assert events[-1]["payload"] == {"seed": 4242, "name": "cao diem vao"}
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", events[-1]["wall_time"])
    assert events[1]["lot_id"] == "A" and events[1]["lane_id"] == "L1"
    assert events[1]["payload"]["tick"] == 3

    limited = store.list_events(run_id, limit=2)
    assert [e["sim_time"] for e in limited] == [4, 3]
    assert store.list_events(9999) == []


def test_save_summary_writes_run_fields_and_summary_for_returns_dict(temp_db):
    run_id = store.create_run(build_params(), "cao_diem_vao")
    summary = {"stop_reason": "user_request", "sim_seconds": 42, "paused_seconds": 3.5,
               "totals": {"admitted": 120, "rejected": 4}, "lots": [{"lot_id": "A"}]}

    store.save_summary(run_id, summary, 42, 45.5, 3.5, "user_request")

    run = store.get_run(run_id)
    assert run["status"] == "stopped"
    assert run["sim_seconds"] == 42
    assert run["paused_seconds"] == 3.5
    assert run["stop_reason"] == "user_request"
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", run["ended_at"])
    assert run["summary"] == summary
    assert store.summary_for(run_id) == summary


def test_summary_for_is_none_while_run_has_not_stopped(temp_db):
    run_id = store.create_run(build_params(), "cao_diem_vao")
    assert store.summary_for(run_id) is None
    assert store.summary_for(9999) is None


def test_list_runs_is_newest_first_and_capped(temp_db):
    ids = [store.create_run(build_params(), "cao_diem_vao") for _ in range(3)]
    store.save_summary(ids[0], {"totals": {"admitted": 1}}, 10, 10.5, 0.0, "user_request")

    listed = store.list_runs()
    assert [run["id"] for run in listed] == list(reversed(ids))
    assert listed[-1]["summary"] == {"totals": {"admitted": 1}}
    assert listed[0]["summary"] is None
    assert [run["id"] for run in store.list_runs(limit=2)] == [ids[2], ids[1]]


def test_recover_interrupted_runs_flips_live_rows_only(temp_db):
    running = store.create_run(build_params(), "cao_diem_vao")
    paused = store.create_run(build_params(), "cao_diem_vao", status="paused")
    stopped = store.create_run(build_params(), "binh_thuong")
    store.save_summary(stopped, {"totals": {}}, 30, 31.0, 0.0, "user_request")

    assert store.recover_interrupted_runs() == 2

    for run_id in (running, paused):
        run = store.get_run(run_id)
        assert run["status"] == "interrupted"
        assert run["stop_reason"] == "server_restart"
        assert run["ended_at"] is not None
    done = store.get_run(stopped)
    assert done["status"] == "stopped"
    assert done["stop_reason"] == "user_request"
    assert store.recover_interrupted_runs() == 0        # gọi lại không đổi gì thêm


# --------------------------------------------------------------------------- #
# Task 2.3 - Ghi số liệu mỗi tick
# --------------------------------------------------------------------------- #
def test_write_tick_writes_16_lane_and_4_lot_rows_per_tick(temp_db):
    run_id = store.create_run(build_params(), "cao_diem_vao")
    for sim_time in range(5):
        store.write_tick(run_id, build_snapshot(sim_time, waiting=sim_time, occupancy=100), [], [])

    assert scalar(temp_db, "SELECT COUNT(*) FROM sim_lane_metrics WHERE run_id = ?", (run_id,)) == 80
    assert scalar(temp_db, "SELECT COUNT(*) FROM sim_lot_metrics WHERE run_id = ?", (run_id,)) == 20
    assert scalar(temp_db, "SELECT COUNT(DISTINCT lot_id) FROM sim_lane_metrics WHERE run_id = ?",
                  (run_id,)) == 4
    assert scalar(temp_db, "SELECT COUNT(*) FROM sim_lane_metrics WHERE run_id = ? AND lot_id = 'A'"
                           " AND lane_id = 'L1'", (run_id,)) == 5

    sample = rows(temp_db, "SELECT direction, gate_state, waiting, processed_total, arrival_rate,"
                           " overloaded, avg_wait_s FROM sim_lane_metrics"
                           " WHERE run_id = ? AND sim_time = 3 AND lot_id = 'A' AND lane_id = 'L3'",
                  (run_id,))[0]
    assert sample == (OUT, "open", 3, 0, 0.0, 0, None)

    lot_row = rows(temp_db, "SELECT occupancy, capacity, fill_ratio, near_full FROM sim_lot_metrics"
                            " WHERE run_id = ? AND sim_time = 4 AND lot_id = 'A'", (run_id,))[0]
    assert lot_row == (100, C.LOT_CAPACITIES["A"], round(100 / C.LOT_CAPACITIES["A"], 4), 0)

    store.write_tick(run_id, build_snapshot(5, waiting=99, occupancy=0), [], [])
    assert scalar(temp_db, "SELECT COUNT(*) FROM sim_lane_metrics WHERE run_id = ?", (run_id,)) == 96


def test_write_tick_upserts_same_sim_time_instead_of_duplicating(temp_db):
    run_id = store.create_run(build_params(), "cao_diem_vao")
    store.write_tick(run_id, build_snapshot(7, waiting=1), [], [])
    store.write_tick(run_id, build_snapshot(7, waiting=25), [], [])

    assert scalar(temp_db, "SELECT COUNT(*) FROM sim_lane_metrics WHERE run_id = ?", (run_id,)) == 16
    assert scalar(temp_db, "SELECT waiting FROM sim_lane_metrics WHERE run_id = ? AND sim_time = 7"
                           " AND lot_id = 'A' AND lane_id = 'L1'", (run_id,)) == 25
    assert scalar(temp_db, "SELECT overloaded FROM sim_lane_metrics WHERE run_id = ? AND sim_time = 7"
                           " AND lot_id = 'A' AND lane_id = 'L1'", (run_id,)) == 1


def test_write_tick_stores_events_and_completed_vehicles_with_sim_time(temp_db):
    run_id = store.create_run(build_params(), "cao_diem_vao")

    events = [
        build_event("redirect", 3, lot_id="A", lane_id="L1", reason="lot_full",
                    payload={"vehicle_id": 11, "to_lot": "BC"}),
        build_event("pause", 3, reason="user_request"),
    ]
    completed = [build_vehicle(11, 3, wait_s=8.0), build_vehicle(12, 3, wait_s=4.0)]
    store.write_tick(run_id, build_snapshot(3, waiting=2, processed_total=12), events, completed)

    assert scalar(temp_db, "SELECT COUNT(*) FROM sim_events WHERE run_id = ?", (run_id,)) == 2
    stored_events = store.list_events(run_id)
    assert {e["type"] for e in stored_events} == {"redirect", "pause"}
    assert all(e["sim_time"] == 3 for e in stored_events)
    assert store.list_events(run_id)[1]["payload"] == {"vehicle_id": 11, "to_lot": "BC"}

    vehicles = rows(temp_db, "SELECT vehicle_id, sim_time, lot_id, lane_id, direction, wait_s,"
                             " processing_s, total_s FROM sim_vehicles WHERE run_id = ?"
                             " ORDER BY vehicle_id", (run_id,))
    assert vehicles == [(11, 3, "A", "L1", IN, 8.0, 4.0, 12.0), (12, 3, "A", "L1", IN, 4.0, 4.0, 8.0)]
    assert all(row[7] == row[5] + row[6] for row in vehicles)

    store.write_tick(run_id, build_snapshot(4), [], [])          # tick không có gì đặc biệt
    assert scalar(temp_db, "SELECT COUNT(*) FROM sim_vehicles WHERE run_id = ?", (run_id,)) == 2


# --------------------------------------------------------------------------- #
# Task 2.4 - Đọc dữ liệu cho UI
# --------------------------------------------------------------------------- #
def test_recent_lane_series_returns_parallel_chronological_lists(temp_db):
    run_id = store.create_run(build_params(), "cao_diem_vao")
    for sim_time in range(130):
        store.write_tick(run_id, build_snapshot(sim_time, waiting=sim_time), [], [])

    series = store.recent_lane_series(run_id, "A", "L1", window=120)
    assert set(series) == {"labels", "values"}
    assert len(series["labels"]) == len(series["values"]) == 120
    assert series["values"] == list(range(10, 130))                 # cửa sổ cuối, cũ -> mới
    assert all(CLOCK_RE.match(label) for label in series["labels"])
    assert series["labels"][0] == "00:00:10"
    assert series["labels"][-1] == "00:02:09"

    short = store.recent_lane_series(run_id, "A", "L1", window=3)
    assert short["labels"] == ["00:02:07", "00:02:08", "00:02:09"]
    assert short["values"] == [127, 128, 129]

    other_lane = store.recent_lane_series(run_id, "KTX", "L4")
    assert len(other_lane["values"]) == 120                         # mặc định window=120
    empty = store.recent_lane_series(run_id, "A", "L9")
    assert empty == {"labels": [], "values": []}
