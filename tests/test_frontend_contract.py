"""Kiểm tra hợp đồng giữa API và giao diện (static/simulation.js).

Giao diện đọc trực tiếp các khoá dưới đây; nếu backend đổi tên khoá, UI sẽ vỡ âm thầm.
Test này khoá đúng những khoá đó lại (thay cho việc kiểm tra bằng trình duyệt trong CI).
"""

from __future__ import annotations

import pytest

from simulation.service import manager

# Khoá mà simulation.js đọc từ payload polling.
SNAPSHOT_KEYS = {"sim_time", "clock", "totals", "lots", "warnings"}
TOTALS_KEYS = {"admitted", "departed", "waiting", "rejected", "redirected"}
LOT_KEYS = {"lot_id", "label", "capacity", "occupancy", "fill_ratio", "admitted", "departed",
            "redirected_in", "redirected_out", "rejected", "gates"}
GATE_KEYS = {"lane_id", "direction", "gate_state", "open", "pending_direction", "waiting",
             "processing_rate", "arrival_rate", "avg_wait_s", "processed_total", "overloaded"}
WARNING_KEYS = {"level", "lot_id", "lane_id", "message"}
SUMMARY_KEYS = {"stop_reason", "sim_seconds", "paused_seconds", "totals", "lots"}


@pytest.fixture
def client(temp_db):
    """Flask test client dùng DB tạm, không chạy thread thời gian thực."""
    import dashboard

    dashboard.app.config["SIM_START_THREAD"] = False
    manager.reset()
    with dashboard.app.test_client() as test_client:
        yield test_client
    manager.reset()


def start_run(client, scenario_id="cao_diem_vao"):
    res = client.post("/api/sim/runs", json={"scenario_id": scenario_id})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["run_id"]


def test_polling_payload_has_every_key_the_ui_reads(client):
    start_run(client)
    state = client.get("/api/sim/runs/current").get_json()

    assert {"status", "run_id", "params", "snapshot", "paused_seconds"} <= set(state)
    snap = state["snapshot"]
    assert set(snap) >= SNAPSHOT_KEYS
    assert set(snap["totals"]) >= TOTALS_KEYS
    assert len(snap["lots"]) == 4
    for lot in snap["lots"]:
        assert set(lot) >= LOT_KEYS
        assert len(lot["gates"]) == 4
        for gate in lot["gates"]:
            assert set(gate) >= GATE_KEYS
            assert gate["gate_state"] in {"open", "closed", "draining", "pending_conversion"}
            assert gate["direction"] in {"in", "out"}
    for warning in snap["warnings"]:
        assert set(warning) >= WARNING_KEYS
    # params dùng cho dòng mô tả tham số trên UI
    assert state["params"]["scenario_id"] == "cao_diem_vao"
    assert state["params"]["lots"][0]["lanes"][0]["lane_id"] == "L1"


def test_events_payload_shape(client):
    run_id = start_run(client)
    events = client.get(f"/api/sim/runs/{run_id}/events?limit=10").get_json()["events"]
    assert events, "phải có sự kiện 'start'"
    for event in events:
        assert {"sim_time", "type", "lot_id", "lane_id", "reason"} <= set(event)


def test_idle_payload(capsys, client):
    assert client.get("/api/sim/runs/current").get_json() == {"status": "idle"}
    assert client.get("/api/sim/runs/current/events").get_json() == {"events": []}


def test_series_payload_matches_chartjs_expectations(client):
    run_id = start_run(client)
    series = client.get(
        f"/api/sim/runs/{run_id}/series?lot_id=A&lane_id=L1&window=120"
    ).get_json()
    assert set(series) == {"labels", "values"}
    assert isinstance(series["labels"], list) and isinstance(series["values"], list)
    assert len(series["labels"]) == len(series["values"])


def test_summary_payload_has_every_key_the_modal_reads(client):
    run_id = start_run(client)
    summary = client.post(f"/api/sim/runs/{run_id}/stop").get_json()
    assert set(summary) >= SUMMARY_KEYS
    assert len(summary["lots"]) == 4
    for lot in summary["lots"]:
        assert {"lot_id", "capacity", "final_occupancy", "admitted", "departed", "redirected_in",
                "redirected_out", "rejected", "near_full_events", "near_full_seconds",
                "avg_wait_s", "lanes"} <= set(lot)
        for lane in lot["lanes"]:
            assert {"lane_id", "direction", "processed_total", "avg_wait_s",
                    "overload_events", "overload_seconds"} <= set(lane)


def test_error_payload_shape_matches_ui_toast(client):
    res = client.post("/api/sim/runs", json={"scenario_id": "khong_ton_tai"})
    assert res.status_code == 400
    body = res.get_json()
    assert {"error_code", "message", "details"} <= set(body)
    assert body["error_code"] == "unknown_scenario"
    assert body["message"]  # thông báo tiếng Việt hiển thị trong toast
