"""Test động cơ mô phỏng: Poisson, đồng hồ, snapshot, cổng, sức chứa, điều khiển làn, cảnh báo.

Mọi test đều tất định (λ = 0 hoặc monkeypatch `poisson`) và không sleep — gọi tick() trực tiếp.
"""

import random

import pytest

import simulation.engine as E
from simulation import config as C
from simulation.models import IN, OUT, GATE_CLOSED, GATE_DRAINING, GATE_OPEN, GATE_PENDING
from simulation.models import LotConfig, Vehicle
from simulation.validation import ValidationError
from tests.conftest import build_lane, build_params

# --------------------------------------------------------------------------- helpers

GATE_KEYS = {
    "lane_id", "direction", "gate_state", "open", "pending_direction", "waiting",
    "processing", "processing_rate", "arrival_rate", "avg_wait_s", "processed_total", "overloaded",
}
LOT_KEYS = {
    "lot_id", "label", "capacity", "occupancy", "fill_ratio", "admitted", "departed",
    "redirected_in", "redirected_out", "rejected", "gates",
}
TOTAL_KEYS = {"admitted", "departed", "redirected", "rejected", "waiting", "vehicles_processed"}


def params_with(lambda_in=0.0, lambda_out=0.0, **kw):
    """RunParams 4 nhà xe × 4 làn với λ cho trước (mặc định 0 ⇒ tất định)."""
    lots = []
    for lot_id in C.LOT_CAPACITIES:
        lanes = tuple(
            build_lane(lane, C.DEFAULT_DIRECTIONS[lane], lam_in=lambda_in, lam_out=lambda_out)
            for lane in C.LANES_PER_LOT
        )
        lots.append(LotConfig(lot_id=lot_id, lanes=lanes))
    kw.setdefault("allow_lopsided", True)
    return build_params(lots=tuple(lots), **kw)


def queue_vehicles(engine, lot_id, lane_id, count, enqueue_time=0, direction=IN):
    """Đặt sẵn `count` xe vào hàng chờ của làn (thay cho việc sinh xe ngẫu nhiên)."""
    lane = engine.lots[lot_id].lanes[lane_id]
    for _ in range(count):
        lane.queue.append(Vehicle(engine.next_vehicle_id, enqueue_time, direction, lot_id))
        engine.next_vehicle_id += 1
    return lane


def run_ticks(engine, n):
    """Chạy n tick, gom toàn bộ sự kiện sinh ra."""
    events = []
    for _ in range(n):
        engine.tick()
        events.extend(engine.events)
    return events


def force_redirect(monkeypatch, capacities, **kwargs):
    """Patch dung tích TRƯỚC khi tạo engine (engine đọc config lúc khởi tạo)."""
    monkeypatch.setattr(C, "LOT_CAPACITIES", capacities)
    monkeypatch.setattr(E, "poisson", lambda lam, rng: kwargs.get("n", 1))
    return E.SimulationEngine(kwargs.get("params") or build_params())


# --------------------------------------------------------------------------- Poisson

def test_poisson_zero_lambda_yields_zero():
    assert E.poisson(0.0, random.Random(1)) == 0
    assert E.poisson(-1.0, random.Random(1)) == 0


def test_poisson_mean_is_close_to_lambda():
    rng = random.Random(7)
    n = 20000
    mean = sum(E.poisson(0.5, rng) for _ in range(n)) / n
    assert abs(mean - 0.5) < 0.02


def test_poisson_is_deterministic_for_a_seed():
    a = [E.poisson(1.5, random.Random(42)) for _ in range(5)]
    b = [E.poisson(1.5, random.Random(42)) for _ in range(5)]
    assert a == b


def test_poisson_returns_integers_and_never_negative():
    rng = random.Random(3)
    draws = [E.poisson(2.0, rng) for _ in range(200)]
    assert all(isinstance(d, int) and d >= 0 for d in draws)
    assert max(draws) < 30


# ----------------------------------------------------------------- đơn vị / đồng hồ

def test_engine_constructs_every_lot_and_lane(params):
    eng = E.SimulationEngine(params)
    assert set(eng.lots) == set(C.LOT_CAPACITIES)
    for lot_id, lot in eng.lots.items():
        assert lot.capacity == C.LOT_CAPACITIES[lot_id]
        assert set(lot.lanes) == set(C.LANES_PER_LOT)
    assert eng.sim_time == 0
    assert eng.events == [] and eng.completed == []


def test_tick_advances_one_simulated_second(params):
    eng = E.SimulationEngine(params)
    snap = eng.tick()
    assert snap["sim_time"] == 1
    assert eng.sim_time == 1
    assert eng.tick()["sim_time"] == 2


def test_clock_label_formats_hours_minutes_seconds(params):
    eng = E.SimulationEngine(params)
    assert eng.snapshot()["clock"] == "00:00:00"
    eng.sim_time = 90
    assert eng.snapshot()["clock"] == "00:01:30"
    eng.sim_time = 3725
    assert eng.snapshot()["clock"] == "01:02:05"


def test_snapshot_contract_keys(params):
    eng = E.SimulationEngine(params)
    snap = eng.snapshot()
    assert set(snap) == {"sim_time", "clock", "totals", "lots", "warnings"}
    assert set(snap["totals"]) == TOTAL_KEYS
    assert len(snap["lots"]) == 4
    lot = snap["lots"][0]
    assert set(lot) == LOT_KEYS
    assert len(lot["gates"]) == 4
    for gate in lot["gates"]:
        assert set(gate) == GATE_KEYS
    assert snap["lots"][0]["lot_id"] == "A"
    assert snap["lots"][0]["label"] == C.LOT_LABELS["A"]
    assert snap["lots"][0]["capacity"] == C.LOT_CAPACITIES["A"]
    assert snap["lots"][0]["fill_ratio"] == 0.0
    assert snap["warnings"] == []


def test_snapshot_gate_reports_lane_configuration(params):
    eng = E.SimulationEngine(params)
    gates = {g["lane_id"]: g for g in eng.snapshot()["lots"][0]["gates"]}
    assert gates["L1"]["direction"] == IN
    assert gates["L3"]["direction"] == OUT
    assert gates["L1"]["gate_state"] == GATE_OPEN
    assert gates["L1"]["open"] is True
    assert gates["L1"]["pending_direction"] is None
    assert gates["L1"]["processing"] is False


# ---------------------------------------------------------------- sinh xe theo làn

def test_open_incoming_lane_receives_sampled_vehicles(monkeypatch, params):
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 2)
    eng = E.SimulationEngine(params)
    eng.tick()
    lane = eng.lots["A"].lanes["L1"]
    assert len(lane.queue) == 1          # 1 xe vào cổng ngay, 1 xe còn chờ
    assert lane.current is not None
    assert eng.sim_time == 1


def test_closed_lane_receives_nothing(monkeypatch, params):
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 5)
    eng = E.SimulationEngine(params)
    eng.close_lane("A", "L1")            # hành động người dùng trước khi tick
    eng.tick()
    assert len(eng.lots["A"].lanes["L1"].queue) == 0
    assert eng.lots["A"].lanes["L1"].current is None
    assert len(eng.lots["A"].lanes["L2"].queue) == 4   # làn còn mở vẫn nhận xe


def test_pending_conversion_lane_receives_nothing(monkeypatch, params):
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 3)
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", 1)            # 1 xe giữ làn ở trạng thái chờ chuyển
    eng.request_conversion("A", "L1", OUT)
    eng.tick()
    lane = eng.lots["A"].lanes["L1"]
    assert len(lane.queue) == 0
    assert lane.current is not None              # xe cũ vẫn được xử lý tiếp
    assert lane.gate_state == GATE_PENDING


def test_outgoing_arrivals_capped_by_occupancy(monkeypatch, params):
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 9)
    eng = E.SimulationEngine(params)
    eng.lots["A"].occupancy = 1
    eng.tick()
    out_lane = eng.lots["A"].lanes["L3"]
    assert len(out_lane.queue) + (1 if out_lane.current else 0) <= 1
    assert len(eng.lots["A"].lanes["L4"].queue) == 0


def test_outgoing_lane_emits_nothing_when_lot_is_empty(monkeypatch, params):
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 9)
    eng = E.SimulationEngine(params)
    eng.tick()
    for lane_id in ("L3", "L4"):
        lane = eng.lots["A"].lanes[lane_id]
        assert len(lane.queue) + (1 if lane.current else 0) == 0
    assert eng.snapshot()["lots"][0]["departed"] == 0


def test_incoming_and_outgoing_draws_are_independent(monkeypatch, params):
    eng = E.SimulationEngine(params)
    eng.lots["A"].occupancy = 5                  # có xe trong bãi mới được phép sinh xe ra
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 1)
    eng.tick()
    snapshot = {g["lane_id"]: g for g in eng.snapshot()["lots"][0]["gates"]}
    assert snapshot["L1"]["arrival_rate"] == 1.0
    assert snapshot["L3"]["arrival_rate"] == 1.0


# --------------------------------------------------------------- cổng & thời gian

def test_vehicle_enters_lot_after_exactly_four_seconds(params):
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", 1)
    for _ in range(4):
        eng.tick()
    assert eng.lots["A"].occupancy == 0          # chưa đủ 4 giây xử lý
    assert eng.lots["A"].lanes["L1"].processed_total == 0
    eng.tick()
    assert eng.lots["A"].occupancy == 1
    assert eng.lots["A"].admitted == 1
    assert eng.lots["A"].lanes["L1"].processed_total == 1
    assert eng.completed[-1]["processing_s"] == C.PROCESSING_TIME_S
    assert eng.completed[-1]["sim_time"] == 4    # bắt đầu ở t = 0, xong ở t = 4


def test_two_vehicles_serialise(params):
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", 2)
    run_ticks(eng, 8)
    assert eng.lots["A"].lanes["L1"].processed_total == 1
    run_ticks(eng, 1)
    assert eng.lots["A"].lanes["L1"].processed_total == 2
    assert eng.lots["A"].occupancy == 2


def test_processing_rate_is_quarter_per_second_while_open_and_zero_when_closed(params):
    eng = E.SimulationEngine(params)
    gates = {g["lane_id"]: g for g in eng.snapshot()["lots"][0]["gates"]}
    assert gates["L1"]["processing_rate"] == 0.25
    eng.close_lane("A", "L2")
    gates = {g["lane_id"]: g for g in eng.snapshot()["lots"][0]["gates"]}
    assert gates["L2"]["processing_rate"] == 0.0
    assert gates["L2"]["gate_state"] == GATE_CLOSED


def test_outgoing_vehicle_decrements_occupancy(params):
    eng = E.SimulationEngine(params)
    eng.lots["A"].occupancy = 3
    queue_vehicles(eng, "A", "L3", 1, direction=OUT)
    run_ticks(eng, 5)
    assert eng.lots["A"].occupancy == 2
    assert eng.lots["A"].departed == 1
    assert eng.completed[-1]["lot_id"] == "A"
    assert eng.completed[-1]["direction"] == OUT


# ------------------------------------------------------------ thời gian chờ

def test_waiting_time_grows_with_queue_position(params):
    eng = E.SimulationEngine(params)
    lane = queue_vehicles(eng, "A", "L1", 3)     # 3 xe xếp hàng tại t = 0
    run_ticks(eng, 13)
    assert lane.processed_total == 3
    assert lane.wait_samples == 3
    assert lane.wait_sum_s == 0 + 4 + 8          # xe 1 vào ngay, xe 2 chờ 4 s, xe 3 chờ 8 s
    gate = eng.snapshot()["lots"][0]["gates"][0]
    assert gate["avg_wait_s"] == pytest.approx(4.0)
    assert eng.lots["A"].occupancy == 3


def test_total_waiting_time_is_wait_plus_processing(params):
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", 1)
    run_ticks(eng, 5)
    rec = eng.completed[-1]
    assert rec["total_s"] == rec["wait_s"] + 4.0
    assert rec["wait_s"] == 0.0
    assert rec["direction"] == IN
    assert rec["vehicle_id"] == 1


def test_average_wait_is_none_before_any_vehicle_is_processed(params):
    eng = E.SimulationEngine(params)
    for gate in eng.snapshot()["lots"][0]["gates"]:
        assert gate["avg_wait_s"] is None
    queue_vehicles(eng, "A", "L1", 1)
    run_ticks(eng, 5)
    gate = eng.snapshot()["lots"][0]["gates"][0]
    assert gate["avg_wait_s"] == 0.0


# -------------------------------------------- sức chứa, điều hướng, từ chối

def test_full_lot_redirects_to_emptiest_lot_with_capacity(monkeypatch, params):
    monkeypatch.setattr(C, "LOT_CAPACITIES", {"A": 1, "BC": 5, "D": 5, "KTX": 5})
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 1)
    eng = E.SimulationEngine(params)
    eng.lots["A"].occupancy = 1                  # A đã đầy
    eng.tick()
    # A có 2 làn vào, mỗi làn 1 xe -> cả 2 bị điều hướng sang nhà xe trống nhất (BC)
    assert eng.lots["A"].redirected_out == 2
    assert eng.lots["BC"].redirected_in == 2
    assert eng.lots["D"].redirected_in == 0
    redirects = [e for e in eng.events if e["type"] == "redirect"]
    assert len(redirects) == 2
    assert {e["payload"]["to_lot"] for e in redirects} == {"BC"}
    assert {e["payload"]["from_lot"] for e in redirects} == {"A"}
    assert all(e["payload"]["hops"] == 1 for e in redirects)
    assert all(e["reason"] == "lot_full" for e in redirects)


def test_redirect_preserves_original_enqueue_time(monkeypatch, params):
    monkeypatch.setattr(C, "LOT_CAPACITIES", {"A": 1, "BC": 5, "D": 5, "KTX": 5})
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 1)
    eng = E.SimulationEngine(params)
    eng.lots["A"].occupancy = 1
    eng.tick()
    moved = eng.lots["BC"].lanes["L1"].queue[0]
    assert moved.enqueue_time == 0               # vẫn tính thời gian chờ từ lúc đến
    assert moved.hops == 1
    assert moved.dest_lot == "BC"
    assert moved.entry_lot == ""


def test_no_capacity_anywhere_rejects_and_logs(monkeypatch, params):
    monkeypatch.setattr(C, "LOT_CAPACITIES", {k: 0 for k in C.LOT_CAPACITIES})
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 1)
    eng = E.SimulationEngine(params)
    eng.tick()
    assert eng.lots["A"].rejected == 2           # 2 làn vào của A
    rejects = [e for e in eng.events if e["type"] == "reject"]
    assert len(rejects) == 8                     # 4 nhà xe × 2 làn vào
    assert all(e["reason"] == "no_capacity" for e in rejects)
    assert {e["lot_id"] for e in rejects} == set(C.LOT_CAPACITIES)
    assert eng.snapshot()["totals"]["rejected"] == 8
    assert eng.snapshot()["totals"]["admitted"] == 0


def test_redirect_hops_are_capped_by_max_hops(monkeypatch, params):
    monkeypatch.setattr(C, "LOT_CAPACITIES", {"A": 0, "BC": 0, "D": 0, "KTX": 0})
    eng = E.SimulationEngine(params)              # λ = 0 nên chỉ có xe do test đặt vào
    vehicle = Vehicle(999, 0, IN, "A", hops=C.MAX_REDIRECT_HOPS)
    lane = eng.lots["A"].lanes["L1"]
    lane.queue.append(vehicle)
    eng.tick()
    assert eng.lots["A"].rejected == 1
    assert eng.lots["A"].redirected_out == 0
    assert lane.current is None
    assert vehicle.hops == C.MAX_REDIRECT_HOPS


def test_occupancy_never_exceeds_capacity(monkeypatch, params):
    monkeypatch.setattr(C, "LOT_CAPACITIES", {"A": 3, "BC": 0, "D": 0, "KTX": 0})
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 1)
    eng = E.SimulationEngine(params)
    for _ in range(40):
        eng.tick()
        assert eng.lots["A"].occupancy <= 3
        assert eng.snapshot()["lots"][0]["fill_ratio"] <= 1.0
    assert eng.lots["A"].occupancy >= 1
    assert eng.lots["A"].rejected > 0            # phần vượt sức chứa bị từ chối


def test_free_slots_subtracts_pending_in_flight(monkeypatch, params):
    monkeypatch.setattr(C, "LOT_CAPACITIES", {"A": 1, "BC": 0, "D": 0, "KTX": 0})
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 1)
    eng = E.SimulationEngine(params)
    eng.tick()
    lot = eng.lots["A"]
    # L1 nhận xe vào cổng (pending) nên L2 thấy hết chỗ -> từ chối
    pending = sum(
        1 for l in lot.lanes.values()
        if l.direction == IN and l.current is not None and l.current.entry_lot == "A"
    )
    assert pending == 1
    assert eng._free_slots(lot) == 1 - 0 - 1
    assert lot.rejected == 1
    assert lot.redirected_out == 0
    assert lot.occupancy == 0                    # chưa xong 4 giây xử lý


# ------------------------------------------------------- điều khiển làn thủ công

def test_conversion_waits_until_lane_is_empty(params):
    eng = E.SimulationEngine(params)
    lane = queue_vehicles(eng, "A", "L1", 2)
    eng.request_conversion("A", "L1", OUT)
    assert lane.pending_direction == OUT
    assert lane.gate_state == GATE_PENDING

    eng.tick()
    assert lane.gate_state == GATE_PENDING
    assert lane.current is not None              # xe đang xử lý tiếp tục chạy
    assert len(lane.queue) == 1

    events = run_ticks(eng, 8)                   # 2 xe × 4 s
    assert lane.pending_direction is None
    assert lane.direction == OUT
    assert lane.gate_state == GATE_OPEN
    done = [e for e in events if e["type"] == "convert_done"]
    assert len(done) == 1
    assert done[0]["payload"] == {"from_direction": IN, "to_direction": OUT}


def test_pending_conversion_blocks_new_arrivals_while_draining(monkeypatch, params):
    eng = E.SimulationEngine(params)
    lane = queue_vehicles(eng, "A", "L1", 4)
    eng.request_conversion("A", "L1", OUT)
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 5)
    initial = len(lane.queue)
    for _ in range(20):                          # 4 xe × 4 giây = 16 giây
        eng.tick()
        assert len(lane.queue) <= initial
    assert lane.pending_direction is None
    assert lane.direction == OUT
    assert lane.processed_total == 4


def test_converted_lane_starts_accepting_new_direction(monkeypatch, params):
    eng = E.SimulationEngine(params)
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 1)
    eng.request_conversion("A", "L1", OUT)
    eng.tick()                                   # làn rỗng -> chuyển hướng ngay trong tick này
    lane = eng.lots["A"].lanes["L1"]
    assert lane.direction == OUT
    assert lane.pending_direction is None
    eng.lots["A"].occupancy = 5                  # có xe trong bãi -> làn ra mới sinh được xe
    eng.tick()
    assert lane.arrivals_window[-1] == (1, 1)    # nhận 1 xe ra ở giây t = 1


def test_close_lane_drains_then_reports_closed(params):
    eng = E.SimulationEngine(params)
    lane = queue_vehicles(eng, "A", "L2", 1)
    eng.close_lane("A", "L2")
    assert lane.open is False
    eng.tick()
    assert lane.gate_state == GATE_DRAINING
    run_ticks(eng, 4)
    assert lane.processed_total == 1
    assert lane.gate_state == GATE_CLOSED
    assert eng.lots["A"].occupancy == 1


def test_close_lane_logs_event_and_open_lane_logs_when_reopened(params):
    eng = E.SimulationEngine(params)
    eng.close_lane("A", "L1")
    assert [e["type"] for e in eng.events] == ["close"]
    assert eng.events[0]["lot_id"] == "A" and eng.events[0]["lane_id"] == "L1"
    eng.events = []
    eng.open_lane("A", "L1")
    assert [e["type"] for e in eng.events] == ["open"]
    assert eng.lots["A"].lanes["L1"].open is True
    eng.events = []
    eng.open_lane("A", "L1")                     # đã mở -> không log thêm
    assert eng.events == []


def test_closing_last_open_lane_is_rejected(params):
    eng = E.SimulationEngine(params)
    eng.close_lane("A", "L2")
    eng.close_lane("A", "L3")
    eng.close_lane("A", "L4")
    assert sum(1 for l in eng.lots["A"].lanes.values() if l.open) == 1
    with pytest.raises(ValidationError) as err:
        eng.close_lane("A", "L1")
    assert err.value.code == "min_open_lanes"
    assert "ít nhất 1 làn mở" in err.value.message
    assert eng.lots["A"].lanes["L1"].open is True
    # nhà xe khác không bị ảnh hưởng
    assert sum(1 for l in eng.lots["BC"].lanes.values() if l.open) == 4


def test_request_conversion_rejects_bad_direction(params):
    eng = E.SimulationEngine(params)
    with pytest.raises(ValidationError) as err:
        eng.request_conversion("A", "L1", "sideways")
    assert err.value.code == "bad_direction"
    assert eng.lots["A"].lanes["L1"].pending_direction is None


def test_request_conversion_rejects_same_direction(params):
    eng = E.SimulationEngine(params)
    with pytest.raises(ValidationError) as err:
        eng.request_conversion("A", "L3", OUT)
    assert err.value.code == "already_direction"
    assert "đã ở hướng này" in err.value.message


def test_request_conversion_rejects_pending_request(params):
    eng = E.SimulationEngine(params)
    eng.request_conversion("A", "L1", OUT)
    with pytest.raises(ValidationError) as err:
        eng.request_conversion("A", "L1", IN)
    assert err.value.code == "conversion_pending"
    assert [e["type"] for e in eng.events] == ["convert_request"]


def test_unknown_lot_and_lane_raise(params):
    eng = E.SimulationEngine(params)
    with pytest.raises(ValidationError) as err:
        eng.close_lane("ZZZ", "L1")
    assert err.value.code == "unknown_lot"
    with pytest.raises(ValidationError) as err:
        eng.close_lane("A", "L9")
    assert err.value.code == "unknown_lane"


# ------------------------------------------------------------------ cảnh báo

def test_lane_overcrowd_events_and_duration(monkeypatch, params):
    monkeypatch.setattr(C, "OVERCROWD_THRESHOLD", 3)
    eng = E.SimulationEngine(params)
    lane = queue_vehicles(eng, "A", "L1", 4)
    events = run_ticks(eng, 6)

    starts = [e for e in events if e["type"] == "overcrowd_start"]
    ends = [e for e in events if e["type"] == "overcrowd_end"]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0]["sim_time"] == 0
    assert starts[0]["payload"]["waiting"] == 3
    assert starts[0]["payload"]["threshold"] == 3
    assert starts[0]["reason"] == "lane_overcrowded"
    assert ends[0]["payload"]["waiting"] == 2    # dưới ngưỡng sau khi xử lý 1 xe
    assert lane.overload_events == 1
    assert lane.overload_open is False
    assert lane.overload_seconds == 4            # 4 giây liên tục còn tắc
    assert lane.waiting == 2


def test_lane_overcrowd_surfaces_as_overloaded_gate(monkeypatch, params):
    monkeypatch.setattr(C, "OVERCROWD_THRESHOLD", 3)
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", 3)
    gate = {g["lane_id"]: g for g in eng.snapshot()["lots"][0]["gates"]}["L1"]
    assert gate["overloaded"] is True
    assert gate["waiting"] == 3


def test_lot_near_full_uses_configured_ratio(monkeypatch, params):
    monkeypatch.setattr(C, "LOT_CAPACITIES", {"A": 10, "BC": 10, "D": 10, "KTX": 10})
    monkeypatch.setattr(C, "NEAR_FULL_RATIO", 0.5)
    eng = E.SimulationEngine(params)
    eng.lots["A"].occupancy = 5
    events = run_ticks(eng, 1)
    starts = [e for e in events if e["type"] == "near_full_start"]
    assert len(starts) == 1
    assert starts[0]["lot_id"] == "A"
    assert starts[0]["payload"]["fill_ratio"] == 0.5
    assert starts[0]["payload"]["occupancy"] == 5
    assert starts[0]["payload"]["capacity"] == 10
    assert eng.lots["A"].near_full_events == 1
    assert eng.lots["A"].near_full_open is True
    assert eng.lots["A"].near_full_seconds == 1
    assert eng.snapshot()["lots"][0]["fill_ratio"] == 0.5


def test_lot_near_full_ends_when_occupancy_drops(monkeypatch, params):
    monkeypatch.setattr(C, "LOT_CAPACITIES", {"A": 10, "BC": 10, "D": 10, "KTX": 10})
    monkeypatch.setattr(C, "NEAR_FULL_RATIO", 0.5)
    eng = E.SimulationEngine(params)
    eng.lots["A"].occupancy = 5
    events = run_ticks(eng, 1)
    assert eng.lots["A"].near_full_open is True
    eng.lots["A"].occupancy = 1
    events += run_ticks(eng, 1)
    ends = [e for e in events if e["type"] == "near_full_end"]
    assert len(ends) == 1
    assert ends[0]["payload"]["occupancy"] == 1
    assert eng.lots["A"].near_full_events == 1
    assert eng.lots["A"].near_full_seconds == 1


def test_warnings_surface_in_snapshot_with_vietnamese_messages(params):
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", C.OVERCROWD_THRESHOLD)
    eng.lots["A"].occupancy = 1012               # 1012/1100 = 92 %
    warnings = eng.snapshot()["warnings"]
    assert warnings == [
        {
            "level": "lane", "lot_id": "A", "lane_id": "L1",
            "message": "Làn A/L1 đang tắc nghẽn (≥20 xe chờ)",
        },
        {
            "level": "lot", "lot_id": "A", "lane_id": None,
            "message": "Nhà xe A sắp đầy (92% ≥ 90%)",
        },
    ]


def test_snapshot_has_no_warnings_below_thresholds(params):
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", C.OVERCROWD_THRESHOLD - 1)
    eng.lots["A"].occupancy = 989                # 989/1100 = 89,9 % < 90 %
    assert eng.snapshot()["warnings"] == []


# ------------------------------------------------------- snapshot & tốc độ đến

def test_totals_aggregate_across_lots(monkeypatch, params):
    monkeypatch.setattr(E, "poisson", lambda lam, rng: 2)
    eng = E.SimulationEngine(params)
    run_ticks(eng, 6)
    totals = eng.snapshot()["totals"]
    lots = eng.snapshot()["lots"]
    assert totals["waiting"] == sum(g["waiting"] for lot in lots for g in lot["gates"])
    assert totals["vehicles_processed"] == sum(g["processed_total"] for lot in lots for g in lot["gates"])
    assert totals["admitted"] == sum(lot["admitted"] for lot in lots)
    assert totals["departed"] == sum(lot["departed"] for lot in lots)
    assert totals["redirected"] == sum(lot["redirected_in"] for lot in lots)
    assert totals["rejected"] == sum(lot["rejected"] for lot in lots)
    assert totals["vehicles_processed"] > 0


def test_observed_arrival_rate_matches_lambda_over_window(params):
    eng = E.SimulationEngine(params_with(lambda_in=0.5), rng=random.Random(99))
    run_ticks(eng, 300)
    gate = {g["lane_id"]: g for g in eng.snapshot()["lots"][0]["gates"]}["L1"]
    assert 0.3 <= gate["arrival_rate"] <= 0.7
    lane = eng.lots["A"].lanes["L1"]
    # cửa sổ chỉ giữ ARRIVAL_WINDOW_S giây gần nhất; tick hiện tại ghi ở t = sim_time - 1
    assert len(lane.arrivals_window) == C.ARRIVAL_WINDOW_S - 1
    assert lane.arrivals_window[0][0] == eng.sim_time - C.ARRIVAL_WINDOW_S + 1
    assert lane.arrivals_window[-1][0] == eng.sim_time - 1


def test_observed_arrival_rate_is_zero_for_idle_lane(params):
    eng = E.SimulationEngine(params)
    run_ticks(eng, 3)
    gate = {g["lane_id"]: g for g in eng.snapshot()["lots"][0]["gates"]}["L1"]
    assert gate["arrival_rate"] == 0.0
    assert len(eng.lots["A"].lanes["L1"].arrivals_window) == 3


def test_events_have_the_contract_shape(params):
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", 1)
    eng.tick()
    assert eng.events == []
    eng.close_lane("A", "L2")
    event = eng.events[-1]
    assert set(event) == {"type", "sim_time", "lot_id", "lane_id", "reason", "payload"}
    assert event["type"] == "close"
    assert event["sim_time"] == eng.sim_time


def test_completed_records_have_the_contract_shape(params):
    eng = E.SimulationEngine(params)
    queue_vehicles(eng, "A", "L1", 1)
    run_ticks(eng, 5)
    rec = eng.completed[-1]
    assert set(rec) == {
        "vehicle_id", "sim_time", "lot_id", "lane_id", "direction", "wait_s", "processing_s", "total_s",
    }
    assert rec["lot_id"] == "A" and rec["lane_id"] == "L1"
