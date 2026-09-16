"""Kiểm thử REST API ``/api/sim/*`` (Flask test client).

- Dùng fixture ``temp_db`` để mọi thao tác chỉ ghi vào DB tạm.
- ``SIM_START_THREAD=False``: lượt chạy không tự tick, test lái tick thủ công nên tất định.
- ``service.manager`` (singleton) được ``reset()`` giữa các test.
"""

from __future__ import annotations

import time

import pytest

import dashboard
from simulation import config as C
from simulation import service, store
from simulation.routes import sim_bp

# dashboard.py (phần camera) không đăng ký blueprint mô phỏng; test tự đăng ký trên app có sẵn.
if "sim" not in dashboard.app.blueprints:
    dashboard.app.register_blueprint(sim_bp)

API = "/api/sim"
SCENARIO_IDS = ["binh_thuong", "cao_diem_vao", "cao_diem_ra", "qua_tai_A", "mot_lan_vao"]


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
def client(temp_db):
    dashboard.app.config["SIM_START_THREAD"] = False
    service.manager.reset()
    with dashboard.app.test_client() as test_client:
        yield test_client
    service.manager.reset()


# --------------------------------------------------------------------------- helpers
def start_run(client, body=None) -> int:
    """POST /api/sim/runs và trả về run_id (assert 201)."""
    response = client.post(f"{API}/runs", json=body if body is not None else {"scenario_id": "binh_thuong"})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["run_id"]


def step_run(times: int = 1) -> None:
    """Lái vài tick tất định: thread không chạy nên phải tự gọi step() của lượt đang chạy."""
    run = service.manager._run
    assert run is not None, "chưa có lượt mô phỏng nào đang chạy"
    for _ in range(times):
        run.step()


def lane_spec(lane_id, direction, lam_in=0.1, lam_out=0.1, open_=True) -> dict:
    return {
        "lane_id": lane_id,
        "direction": direction,
        "lambda_in": lam_in,
        "lambda_out": lam_out,
        "open": open_,
    }


def all_in_lanes(lot_id) -> dict:
    """Nhà xe chỉ có làn vào ⇒ cấu hình lệch, cần xác nhận."""
    return {"lot_id": lot_id, "lanes": [lane_spec(lane, "in") for lane in C.LANES_PER_LOT]}


def custom_body(lots, **extra) -> dict:
    body: dict = {"name": "Tự chọn"}
    body.update(extra)
    body["lots"] = lots
    return body


def error_of(response) -> str:
    return response.get_json()["error_code"]


# --------------------------------------------------------------------------- kịch bản
def test_scenarios_endpoint_lists_five_scenarios(client):
    response = client.get(f"{API}/scenarios")

    assert response.status_code == 200
    items = response.get_json()["scenarios"]
    assert [item["id"] for item in items] == SCENARIO_IDS
    for item in items:
        assert set(item) == {"id", "name", "description"}
        assert item["name"] and item["description"]


# --------------------------------------------------------------------------- bắt đầu lượt
def test_start_run_returns_201_with_run_id(client):
    response = client.post(f"{API}/runs", json={"scenario_id": "cao_diem_vao"})

    assert response.status_code == 201
    payload = response.get_json()
    assert isinstance(payload["run_id"], int)
    assert payload["status"] == "running"
    assert store.get_run(payload["run_id"])["scenario_id"] == "cao_diem_vao"
    # SIM_START_THREAD=False ⇒ không có thread nhịp thời gian thực nào chạy
    assert service.manager._run._thread.is_alive() is False


def test_start_run_rejects_out_of_range_lambda(client):
    body = custom_body([{"lot_id": "A", "lanes": [lane_spec("L1", "in", lam_in=99.0)]}])

    response = client.post(f"{API}/runs", json=body)

    assert response.status_code == 400
    assert error_of(response) == "lambda_out_of_range"
    assert store.list_runs() == []


def test_start_run_lopsided_requires_confirmation(client):
    body = custom_body([all_in_lanes("A")])

    response = client.post(f"{API}/runs", json=body)

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error_code"] == "lopsided_needs_confirm"
    assert payload["details"]["requires_confirmation"] is True
    assert store.list_runs() == []

    confirmed = client.post(f"{API}/runs", json={**body, "confirm_lopsided": True})

    assert confirmed.status_code == 201
    run_id = confirmed.get_json()["run_id"]
    assert store.get_run(run_id)["params"]["allow_lopsided"] is True


def test_start_run_rejects_malformed_body(client):
    empty = client.post(f"{API}/runs", json={})
    assert empty.status_code == 400
    assert error_of(empty) == "invalid_params"

    unknown = client.post(f"{API}/runs", json={"scenario_id": "khong_ton_tai"})
    assert unknown.status_code == 400
    assert error_of(unknown) == "unknown_scenario"

    no_json = client.post(f"{API}/runs", data="not json", content_type="text/plain")
    assert no_json.status_code == 400


def test_second_start_run_while_running_returns_409(client):
    start_run(client)

    response = client.post(f"{API}/runs", json={"scenario_id": "binh_thuong"})

    assert response.status_code == 409
    assert error_of(response) == "run_already_active"


# --------------------------------------------------------------------------- polling
def test_current_run_returns_snapshot_contract(client):
    run_id = start_run(client, {"scenario_id": "cao_diem_vao"})

    response = client.get(f"{API}/runs/current")

    assert response.status_code == 200
    state = response.get_json()
    assert state["status"] == "running"
    assert state["run_id"] == run_id
    assert state["params"]["scenario_id"] == "cao_diem_vao"
    assert state["paused_seconds"] == 0.0
    assert state["snapshot"]["sim_time"] == 0
    assert len(state["snapshot"]["lots"]) == 4
    for lot in state["snapshot"]["lots"]:
        assert len(lot["gates"]) == 4
        assert {"lane_id", "direction", "gate_state", "waiting", "arrival_rate"} <= set(lot["gates"][0])


def test_current_is_idle_and_events_empty_when_nothing_runs(client):
    assert client.get(f"{API}/runs/current").get_json() == {"status": "idle"}

    events = client.get(f"{API}/runs/current/events?limit=200")

    assert events.status_code == 200
    assert events.get_json() == {"events": []}


def test_events_endpoints_return_the_start_event(client):
    run_id = start_run(client)

    current_events = client.get(f"{API}/runs/current/events?limit=10").get_json()["events"]
    row_events = client.get(f"{API}/runs/{run_id}/events?limit=10").get_json()["events"]

    assert [event["type"] for event in current_events] == ["start"]
    assert [event["type"] for event in row_events] == ["start"]
    assert current_events[0]["payload"]["scenario_id"] == "binh_thuong"


def test_runs_history_endpoint(client):
    run_id = start_run(client)

    response = client.get(f"{API}/runs")

    assert response.status_code == 200
    runs = response.get_json()["runs"]
    assert [run["id"] for run in runs] == [run_id]
    assert runs[0]["status"] == "running"


def test_current_poll_responds_quickly(client):
    start_run(client, {"scenario_id": "cao_diem_vao"})
    client.get(f"{API}/runs/current")                 # làm nóng trước khi đo

    durations = []
    for _ in range(5):
        started = time.perf_counter()
        assert client.get(f"{API}/runs/current").status_code == 200
        durations.append(time.perf_counter() - started)

    assert max(durations) < 0.05, f"poll quá chậm: {max(durations) * 1000:.1f} ms"


# --------------------------------------------------------------------------- pause/resume/stop
def test_pause_resume_and_stop_endpoints(client):
    run_id = start_run(client)

    paused = client.post(f"{API}/runs/{run_id}/pause")
    assert paused.status_code == 200
    assert paused.get_json() == {"status": "paused", "sim_time": 0}
    assert client.get(f"{API}/runs/current").get_json()["status"] == "paused"

    resumed = client.post(f"{API}/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.get_json() == {"status": "running"}
    assert client.get(f"{API}/runs/current").get_json()["status"] == "running"

    step_run(2)
    stopped = client.post(f"{API}/runs/{run_id}/stop")
    assert stopped.status_code == 200
    summary = stopped.get_json()
    assert set(summary) == {"stop_reason", "sim_seconds", "paused_seconds", "totals", "lots"}
    assert summary["sim_seconds"] == 2
    assert summary["stop_reason"] == "user_request"
    assert len(summary["lots"]) == 4
    assert client.get(f"{API}/runs/current").get_json() == {"status": "idle"}

    stored = client.get(f"{API}/runs/{run_id}/summary")
    assert stored.status_code == 200
    assert stored.get_json() == summary


def test_summary_before_stop_returns_404(client):
    run_id = start_run(client)

    response = client.get(f"{API}/runs/{run_id}/summary")

    assert response.status_code == 404
    payload = response.get_json()
    assert payload["error_code"] == "summary_not_ready"
    assert payload["message"] == "Lượt mô phỏng chưa kết thúc."


def test_unknown_run_id_on_control_routes_returns_409(client):
    start_run(client)

    for path in ("pause", "resume", "stop"):
        response = client.post(f"{API}/runs/9999/{path}")
        assert response.status_code == 409, path
        assert error_of(response) == "no_active_run"


def test_control_on_idle_returns_409(client):
    response = client.post(f"{API}/runs/1/pause")

    assert response.status_code == 409
    assert error_of(response) == "no_active_run"


# --------------------------------------------------------------------------- làn
def test_lane_open_close_and_min_open_lanes(client):
    run_id = start_run(client)

    closed = client.post(f"{API}/runs/{run_id}/lanes/A/L1/close")
    assert closed.status_code == 200
    assert closed.get_json()["lane"]["open"] is False
    assert closed.get_json()["lane"]["gate_state"] == "closed"

    opened = client.post(f"{API}/runs/{run_id}/lanes/BCD/L1/open")     # alias BCD -> BC
    assert opened.status_code == 200

    for lane_id in ("L2", "L3"):
        assert client.post(f"{API}/runs/{run_id}/lanes/A/{lane_id}/close").status_code == 200

    last = client.post(f"{API}/runs/{run_id}/lanes/A/L4/close")
    assert last.status_code == 409
    assert error_of(last) == "min_open_lanes"

    reopened = client.post(f"{API}/runs/{run_id}/lanes/a/l1/open")     # chữ thường
    assert reopened.status_code == 200
    assert reopened.get_json()["lane"]["open"] is True


def test_lane_convert_and_pending_conversion(client):
    run_id = start_run(client)

    first = client.post(f"{API}/runs/{run_id}/lanes/A/L1/convert", json={"target": "out"})
    assert first.status_code == 200
    lane = first.get_json()["lane"]
    assert lane["pending_direction"] == "out"
    assert lane["gate_state"] == "pending_conversion"

    second = client.post(f"{API}/runs/{run_id}/lanes/A/L1/convert", json={"target": "in"})
    assert second.status_code == 409
    assert error_of(second) == "conversion_pending"

    missing_target = client.post(f"{API}/runs/{run_id}/lanes/A/L2/convert", json={})
    assert missing_target.status_code == 400
    assert error_of(missing_target) == "bad_direction"

    same_direction = client.post(f"{API}/runs/{run_id}/lanes/A/L3/convert", json={"target": "out"})
    assert same_direction.status_code == 400
    assert error_of(same_direction) == "already_direction"

    types = [event["type"] for event in store.list_events(run_id)]
    assert "convert_request" in types


def test_lane_routes_reject_unknown_lot_and_lane(client):
    run_id = start_run(client)

    bogus_lot = client.post(f"{API}/runs/{run_id}/lanes/ZZ/L1/open")
    assert bogus_lot.status_code == 400
    assert error_of(bogus_lot) == "unknown_lot"

    bogus_lane = client.post(f"{API}/runs/{run_id}/lanes/A/L9/open")
    assert bogus_lane.status_code == 400
    assert error_of(bogus_lane) == "unknown_lane"


# --------------------------------------------------------------------------- chuỗi số liệu
def test_series_returns_equal_length_series(client):
    run_id = start_run(client)
    step_run(3)

    response = client.get(f"{API}/runs/{run_id}/series?lot_id=A&lane_id=L1&window=120")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["labels"]) == len(payload["values"]) == 3
    assert all(len(label) == 8 for label in payload["labels"])          # 'HH:MM:SS'

    clipped = client.get(f"{API}/runs/{run_id}/series?lot_id=BCD&lane_id=l1&window=2")
    assert clipped.status_code == 200
    assert len(clipped.get_json()["values"]) == 2


def test_series_rejects_unknown_lot_and_lane(client):
    run_id = start_run(client)

    bogus_lot = client.get(f"{API}/runs/{run_id}/series?lot_id=ZZ&lane_id=L1")
    assert bogus_lot.status_code == 400
    assert error_of(bogus_lot) == "unknown_lot"

    bogus_lane = client.get(f"{API}/runs/{run_id}/series?lot_id=A&lane_id=L9")
    assert bogus_lane.status_code == 400
    assert error_of(bogus_lane) == "unknown_lane"

    bad_window = client.get(f"{API}/runs/{run_id}/series?lot_id=A&lane_id=L1&window=abc")
    assert bad_window.status_code == 400
    assert error_of(bad_window) == "invalid_params"
