"""Kiểm thử tầng dịch vụ (simulation/service.py): ActiveRun + RunManager.

Không sleep thật: ``ActiveRun`` được dựng với ``NullThread`` (không thực sự chạy) và các tick
được lái thủ công bằng ``step()``; nhịp thời gian thực được kiểm tra bằng một đồng hồ giả
(``FakeClock``) nên vẫn tất định. Mọi test chạm DB đều dùng fixture ``temp_db``.
"""

from __future__ import annotations

import threading

import pytest

import simulation.engine as E
from simulation import config as C
from simulation import service, store
from simulation.models import IN, OUT, LaneConfig, LotConfig
from simulation.service import ActiveRun, RunConflict, RunManager
from simulation.validation import ValidationError
from tests.conftest import build_params


# --------------------------------------------------------------------------- helpers
class NullThread:
    """Thread giả: ghi nhận ``start()`` nhưng không chạy gì (không có vòng lặp thật)."""

    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        self.daemon = daemon
        self.name = name
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


class FakeClock:
    """Đồng hồ giả cho ``_loop()``: ``sleep`` đẩy thời gian tới trước, không block thật.

    ``taps``  : các cú nhảy thời gian bất thường (mô phỏng máy bận) sau mỗi lần ngủ.
    ``stop_after`` / ``on_stop``: dừng vòng lặp để test không chạy vô hạn.
    """

    def __init__(self, taps=(), stop_after=None, on_stop=None):
        self.t = 0.0
        self.taps = list(taps)
        self.stop_after = stop_after
        self.on_stop = on_stop

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(seconds, 1e-6)
        if self.taps:
            self.t += self.taps.pop(0)
        if self.stop_after is not None and self.t >= self.stop_after and self.on_stop:
            self.on_stop()


def use_fake_clock(monkeypatch, clock: FakeClock) -> None:
    monkeypatch.setattr(service.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(service.time, "sleep", clock.sleep)


def valid_params(**overrides):
    """RunParams hợp lệ (λ = LAMBDA_MIN ≥ ngưỡng tối thiểu).

    ``tests/conftest.build_params`` dùng λ = 0 (tiện cho test động cơ) nên KHÔNG qua được
    ``validate_run_params`` — RunManager.start() kiểm tra hợp lệ nên cần bộ tham số này.
    """
    lots = tuple(
        LotConfig(
            lot_id=lot_id,
            lanes=tuple(
                LaneConfig(lane_id, C.DEFAULT_DIRECTIONS[lane_id], C.LAMBDA_MIN, C.LAMBDA_MIN, True)
                for lane_id in C.LANES_PER_LOT
            ),
        )
        for lot_id in C.LOT_CAPACITIES
    )
    return build_params(lots=lots, **overrides)


def make_run(params=None, thread_factory=NullThread):
    """Ghi 1 dòng sim_runs rồi dựng ActiveRun với thread giả (không chạy vòng lặp)."""
    params = params if params is not None else valid_params()
    run_id = store.create_run(params, "test")
    return run_id, ActiveRun(run_id, E.SimulationEngine(params), thread_factory=thread_factory)


def params_with_lambda_in(lot_id: str, lane_id: str, value: float):
    """RunParams đủ 4 nhà xe, riêng λ vào của 1 làn bị đổi (để thử kiểm tra hợp lệ)."""
    base = valid_params()
    lots = []
    for lot in base.lots:
        if lot.lot_id != lot_id:
            lots.append(lot)
            continue
        lots.append(
            LotConfig(
                lot_id=lot.lot_id,
                lanes=tuple(
                    LaneConfig(
                        lane.lane_id,
                        lane.direction,
                        value if lane.lane_id == lane_id else lane.lambda_in,
                        lane.lambda_out,
                        lane.open,
                    )
                    for lane in lot.lanes
                ),
            )
        )
    return build_params(lots=tuple(lots))


def count_lane_rows(run_id: int) -> int:
    from database import get_connection

    conn = get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sim_lane_metrics WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def event_types(run_id: int) -> list[str]:
    return [event["type"] for event in store.list_events(run_id)]


# --------------------------------------------------------------------------- step()
def test_step_advances_one_simulated_second_and_writes_metrics(temp_db):
    run_id, run = make_run(thread_factory=threading.Thread)   # thread thật nhưng KHÔNG start()

    snapshot = {}
    for _ in range(3):
        snapshot = run.step()

    assert run.engine.sim_time == 3
    assert snapshot["sim_time"] == 3
    assert run.snapshot["sim_time"] == 3
    assert count_lane_rows(run_id) == 3 * 16             # 4 nhà xe × 4 làn × 3 giây
    assert run.is_alive() is False                       # chưa start() thì vòng lặp không chạy


def test_start_leaves_thread_unstarted_when_not_requested(temp_db):
    _, run = make_run()                                  # NullThread
    assert run._thread.started is False
    run.start()
    assert run._thread.started is True


# --------------------------------------------------------------------------- vòng lặp
def test_loop_ticks_one_simulated_second_at_a_time(temp_db, monkeypatch):
    run_id, run = make_run()
    clock = FakeClock(stop_after=2.5, on_stop=run._stop.set)
    use_fake_clock(monkeypatch, clock)

    run._loop()

    assert run.engine.sim_time == 2                      # 2,5 giây giả ⇒ 2 tick, không dồn
    assert count_lane_rows(run_id) == 32
    assert clock.t >= 2.5


def test_loop_pause_freezes_the_clock(temp_db, monkeypatch):
    run_id, run = make_run(build_params(max_sim_seconds=2))
    run.pause()

    clock = FakeClock(stop_after=1.0, on_stop=run._stop.set)
    use_fake_clock(monkeypatch, clock)

    assert run.paused is True
    assert store.get_run(run_id)["status"] == store.STATUS_PAUSED
    assert event_types(run_id) == ["pause"]

    run._loop()                                          # 1 giây giả trôi qua trong lúc tạm dừng

    assert run.engine.sim_time == 0                      # đồng hồ mô phỏng đứng yên
    assert count_lane_rows(run_id) == 0
    assert clock.t >= 1.0


def test_loop_stops_automatically_at_max_sim_seconds(temp_db, monkeypatch):
    run_id, run = make_run(build_params(max_sim_seconds=3))
    use_fake_clock(monkeypatch, FakeClock())

    run._loop()

    assert run.engine.sim_time == 3
    assert run.stop_reason == "max_duration"
    row = store.get_run(run_id)
    assert row["status"] == store.STATUS_STOPPED
    assert row["stop_reason"] == "max_duration"
    assert row["summary"]["sim_seconds"] == 3


def test_loop_does_not_batch_catch_up_after_a_long_stall(temp_db, monkeypatch):
    """Máy bận (đồng hồ nhảy 10 s) thì bỏ qua phần trễ, mỗi vòng vẫn chỉ 1 tick."""
    run_id, run = make_run(build_params(max_sim_seconds=3))
    use_fake_clock(monkeypatch, FakeClock(taps=[10.0]))

    run._loop()

    assert run.engine.sim_time == 3                      # không dồn 10 tick bù
    assert count_lane_rows(run_id) == 48


# --------------------------------------------------------------------------- pause/resume
def test_pause_logs_event_and_flips_db_status(temp_db):
    _, run = make_run()

    assert run.pause() == {"status": "paused", "sim_time": 0}
    assert run.paused is True
    assert run.pause() == {"status": "paused", "sim_time": 0}     # gọi lại không ghi thêm
    assert event_types(run.run_id) == ["pause"]


def test_resume_logs_event_and_accumulates_paused_seconds(temp_db):
    run_id, run = make_run()
    run.pause()
    run.paused_since -= 5.0                                       # coi như đã tạm dừng 5 giây

    assert run.resume() == {"status": "running"}

    assert run.paused is False
    assert run.paused_seconds == pytest.approx(5.0, abs=0.5)
    assert store.get_run(run_id)["status"] == store.STATUS_RUNNING
    assert sorted(event_types(run_id)) == ["pause", "resume"]
    recorded = run.paused_seconds
    run.resume()                                                  # gọi lại không cộng thêm
    assert run.paused_seconds == recorded


def test_pause_on_stopped_run_raises_no_active_run(temp_db):
    _, run = make_run()
    run.stop()
    with pytest.raises(RunConflict) as exc:
        run.pause()
    assert exc.value.code == "no_active_run"


# --------------------------------------------------------------------------- stop
def test_stop_returns_summary_and_persists_it(temp_db):
    run_id, run = make_run()
    run.step()

    summary = run.stop()

    assert run.stop_reason == "user_request"
    assert summary["stop_reason"] == "user_request"
    assert summary["sim_seconds"] == 1
    row = store.get_run(run_id)
    assert row["status"] == store.STATUS_STOPPED
    assert row["stop_reason"] == "user_request"
    assert row["sim_seconds"] == 1
    assert row["summary"] == summary
    assert "stop" in event_types(run_id)


# --------------------------------------------------------------------------- RunManager
def test_manager_start_step_and_current(temp_db):
    manager = RunManager()

    assert manager.current() == {"status": "idle"}

    run_id = manager.start(valid_params(), scenario_id="test", start_thread=False)
    assert isinstance(run_id, int)

    state = manager.current()
    assert state["status"] == "running"
    assert state["run_id"] == run_id
    assert state["paused_seconds"] == 0.0
    assert state["snapshot"]["sim_time"] == 0
    assert state["params"]["scenario_id"] == "test"
    assert len(state["snapshot"]["lots"]) == 4

    run = manager._run                       # lượt đang chạy (để lái tick thủ công)
    for _ in range(3):
        run.step()
    assert run.engine.params.name == "test"
    assert manager.current()["snapshot"]["sim_time"] == 3    # step() cập nhật self.snapshot


def test_manager_second_start_while_running_raises(temp_db):
    manager = RunManager()
    manager.start(valid_params(), start_thread=False)

    with pytest.raises(RunConflict) as exc:
        manager.start(valid_params(), start_thread=False)

    assert exc.value.code == "run_already_active"
    assert exc.value.details["run_id"] is not None


def test_manager_start_validates_before_creating_any_run(temp_db):
    manager = RunManager()
    bad = params_with_lambda_in("A", "L1", 99.0)

    with pytest.raises(ValidationError) as exc:
        manager.start(bad, start_thread=False)

    assert exc.value.code == "lambda_out_of_range"
    assert store.list_runs() == []           # không có dòng sim_runs nào được tạo
    assert manager.current() == {"status": "idle"}


def test_manager_pause_resume_stop_lifecycle(temp_db):
    manager = RunManager()
    run_id = manager.start(valid_params(), start_thread=False)

    assert manager.pause(run_id) == {"status": "paused", "sim_time": 0}
    assert manager.current()["status"] == "paused"
    assert manager.resume(run_id) == {"status": "running"}
    assert manager.current()["status"] == "running"

    summary = manager.stop(run_id)

    assert summary["sim_seconds"] == 0
    assert manager.current() == {"status": "idle"}            # không còn lượt nào
    with pytest.raises(RunConflict) as exc:
        manager.pause(run_id)
    assert exc.value.code == "no_active_run"


def test_manager_reset_clears_the_active_run(temp_db):
    manager = RunManager()
    run_id = manager.start(valid_params(), start_thread=False)

    manager.reset()

    assert manager.current() == {"status": "idle"}
    with pytest.raises(RunConflict) as exc:
        manager.lane_action(run_id, "A", "L1", "open")
    assert exc.value.code == "no_active_run"
    assert manager.start(valid_params(), start_thread=False) != run_id


def test_manager_lane_action_requires_active_run(temp_db):
    manager = RunManager()
    manager.start(valid_params(), start_thread=False)

    for stale_id in (999, 0):
        with pytest.raises(RunConflict) as exc:
            manager.lane_action(stale_id, "A", "L1", "open")
        assert exc.value.code == "no_active_run"
        assert exc.value.details["run_id"] == stale_id


def test_manager_lane_action_open_close_convert(temp_db):
    manager = RunManager()
    run_id = manager.start(valid_params(), start_thread=False)
    engine = manager._run.engine

    gate = manager.lane_action(run_id, "A", "L1", "close")
    assert gate["lane_id"] == "L1"
    assert gate["open"] is False and gate["gate_state"] == "closed"
    assert engine.lots["A"].lanes["L1"].open is False

    gate = manager.lane_action(run_id, "1", "l1", "open")     # alias '1' + chữ thường
    assert gate["open"] is True and gate["gate_state"] == "open"
    assert engine.lots["A"].lanes["L1"].open is True

    gate = manager.lane_action(run_id, "A", "L1", "convert", target=OUT)
    assert gate["pending_direction"] == OUT
    assert gate["gate_state"] == "pending_conversion"
    assert engine.lots["A"].lanes["L1"].pending_direction == OUT
    assert manager._run.snapshot["sim_time"] == 0            # snapshot được làm mới

    assert {"close", "open", "convert_request"} <= set(event_types(run_id))
    recent = manager.events(run_id, limit=2)
    assert len(recent) == 2                                  # tôn trọng limit
    assert recent[0]["type"] == "convert_request"            # mới nhất trước


def test_manager_lane_action_unknown_action_raises(temp_db):
    manager = RunManager()
    run_id = manager.start(valid_params(), start_thread=False)

    with pytest.raises(RunConflict) as exc:
        manager.lane_action(run_id, "A", "L1", "explode")

    assert exc.value.code == "invalid_action"


def test_manager_lane_action_unknown_lot_or_lane_raises_validation_error(temp_db):
    manager = RunManager()
    run_id = manager.start(valid_params(), start_thread=False)

    with pytest.raises(ValidationError) as exc:
        manager.lane_action(run_id, "ZZ", "L1", "open")
    assert exc.value.code == "unknown_lot"

    with pytest.raises(ValidationError) as exc:
        manager.lane_action(run_id, "A", "L9", "open")
    assert exc.value.code == "unknown_lane"


def test_min_open_lanes_is_enforced_by_the_engine(temp_db):
    manager = RunManager()
    run_id = manager.start(valid_params(), start_thread=False)

    for lane_id in ("L1", "L2", "L3"):
        assert manager.lane_action(run_id, "A", lane_id, "close")["open"] is False
    with pytest.raises(ValidationError) as exc:
        manager.lane_action(run_id, "A", "L4", "close")
    assert exc.value.code == "min_open_lanes"


def test_manager_events_returns_newest_first(temp_db):
    manager = RunManager()
    run_id = manager.start(valid_params(), start_thread=False)
    manager.lane_action(run_id, "A", "L1", "close")

    types = [event["type"] for event in manager.events(run_id, limit=10)]

    assert "close" in types and "start" in types
    assert types[0] == "close"                               # mới nhất trước
