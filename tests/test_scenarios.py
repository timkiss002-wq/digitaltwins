"""Kiểm thử thư viện kịch bản (simulation/scenarios.py).

Không cần DB: chỉ kiểm tra preset, phần ghi đè và việc dựng RunParams từ body của API.
"""

from __future__ import annotations

import dataclasses

import pytest

from simulation import config as C
from simulation import scenarios
from simulation.models import IN, OUT, LaneConfig, LotConfig
from simulation.validation import ValidationError, normalise_lot_id, validate_run_params

SCENARIO_IDS = ["binh_thuong", "cao_diem_vao", "cao_diem_ra", "qua_tai_A", "mot_lan_vao"]


# --------------------------------------------------------------------------- helpers
def lot_by_id(params, lot_id: str) -> LotConfig:
    return next(lot for lot in params.lots if lot.lot_id == lot_id)


def lanes_of(params, lot_id: str) -> dict[str, LaneConfig]:
    return {lane.lane_id: lane for lane in lot_by_id(params, lot_id).lanes}


def lane_spec(lane_id, direction, lam_in=0.2, lam_out=0.2, open_=True) -> dict:
    return {
        "lane_id": lane_id,
        "direction": direction,
        "lambda_in": lam_in,
        "lambda_out": lam_out,
        "open": open_,
    }


def custom_body(**extra) -> dict:
    """Body tự chọn đầy đủ 4 nhà xe × 4 làn với λ = 0,2 và hướng mặc định."""
    lots = [
        {
            "lot_id": lot_id,
            "lanes": [lane_spec(lane, C.DEFAULT_DIRECTIONS[lane]) for lane in C.LANES_PER_LOT],
        }
        for lot_id in C.LOT_CAPACITIES
    ]
    body = {"name": "Tự chọn", "seed": 5, "lots": lots}
    body.update(extra)
    return body


# --------------------------------------------------------------------------- catalog
def test_list_scenarios_returns_five_ids_with_required_keys():
    items = scenarios.list_scenarios()
    assert [item["id"] for item in items] == SCENARIO_IDS
    for item in items:
        assert set(item) == {"id", "name", "description"}
        assert item["name"] and item["description"]


def test_every_scenario_builds_valid_params():
    for scenario_id in SCENARIO_IDS:
        params = scenarios.build_params(scenario_id)
        validate_run_params(params)                     # không raise
        assert params.scenario_id == scenario_id
        assert [lot.lot_id for lot in params.lots] == list(C.LOT_CAPACITIES)
        for lot in params.lots:
            assert [lane.lane_id for lane in lot.lanes] == list(C.LANES_PER_LOT)
            assert all(lane.open for lane in lot.lanes)


def test_scenario_params_are_frozen():
    params = scenarios.build_params("binh_thuong")
    with pytest.raises(dataclasses.FrozenInstanceError):
        params.name = "đổi tên"                        # type: ignore[misc]


def test_unknown_scenario_id_raises_unknown_scenario():
    with pytest.raises(ValidationError) as exc:
        scenarios.build_params("khong_ton_tai")
    assert exc.value.code == "unknown_scenario"
    assert "khong_ton_tai" in exc.value.message


# --------------------------------------------------------------------------- presets
def test_binh_thuong_uses_off_peak_rates():
    params = scenarios.build_params("binh_thuong")
    for lot in params.lots:
        for lane in lot.lanes:
            assert lane.lambda_in == 0.15
            assert lane.lambda_out == 0.15


def test_cao_diem_vao_sets_incoming_rate_on_every_lane():
    params = scenarios.build_params("cao_diem_vao")
    for lot in params.lots:
        for lane in lot.lanes:
            assert lane.lambda_in == 0.5
            assert lane.lambda_out == 0.05


def test_cao_diem_ra_sets_outgoing_rate_on_every_lane():
    params = scenarios.build_params("cao_diem_ra")
    for lot in params.lots:
        for lane in lot.lanes:
            assert lane.lambda_in == 0.05
            assert lane.lambda_out == 0.5


def test_mot_lan_vao_gives_ktx_exactly_one_open_incoming_lane():
    params = scenarios.build_params("mot_lan_vao")
    ktx = lanes_of(params, "KTX")
    incoming_open = [lane.lane_id for lane in ktx.values() if lane.direction == IN and lane.open]
    assert incoming_open == ["L1"]
    assert ktx["L2"].direction == OUT
    assert ktx["L3"].direction == OUT
    assert ktx["L4"].direction == OUT
    assert ktx["L1"].lambda_in == 0.4
    assert ktx["L1"].lambda_out == 0.1


def test_mot_lan_vao_keeps_default_directions_in_other_lots():
    params = scenarios.build_params("mot_lan_vao")
    for lot_id in ("A", "BC", "D"):
        for lane_id, lane in lanes_of(params, lot_id).items():
            assert lane.direction == C.DEFAULT_DIRECTIONS[lane_id]


def test_qua_tai_A_overrides_only_lot_A():
    params = scenarios.build_params("qua_tai_A")
    a = lanes_of(params, "A")
    assert {lane.lambda_in for lane in a.values()} == {2.0}
    assert {lane.lambda_out for lane in a.values()} == {0.1}
    for lot_id in ("BC", "D", "KTX"):
        for lane in lanes_of(params, lot_id).values():
            assert lane.lambda_in == 0.1
            assert lane.lambda_out == 0.1
    for lane_id, lane in a.items():
        assert lane.direction == C.DEFAULT_DIRECTIONS[lane_id]


def test_build_params_honours_seed_and_max_sim_seconds():
    params = scenarios.build_params("cao_diem_vao", seed=7, max_sim_seconds=300)
    assert params.seed == 7
    assert params.max_sim_seconds == 300
    assert params.processing_time_s == C.PROCESSING_TIME_S


# --------------------------------------------------------------------------- request
def test_build_from_request_with_scenario_id():
    params = scenarios.build_from_request({"scenario_id": "cao_diem_vao", "seed": 3})
    assert params.scenario_id == "cao_diem_vao"
    assert params.seed == 3
    assert all(lane.lambda_in == 0.5 for lot in params.lots for lane in lot.lanes)
    validate_run_params(params)


def test_build_from_request_custom_body_matches_lambdas():
    body = custom_body(seed=9, max_sim_seconds=120)
    params = scenarios.build_from_request(body)
    assert params.name == "Tự chọn"
    assert params.seed == 9
    assert params.max_sim_seconds == 120
    assert params.scenario_id == "custom"
    for lot in params.lots:
        assert {lane.lambda_in for lane in lot.lanes} == {0.2}
        assert {lane.lambda_out for lane in lot.lanes} == {0.2}
    validate_run_params(params)


def test_build_from_request_normalises_lot_aliases():
    aliases = {"BCD": "BC", "E": "D", "1": "A", "3": "KTX"}
    body = {
        "name": "alias",
        "lots": [
            {
                "lot_id": raw,
                "lanes": [lane_spec(lane, C.DEFAULT_DIRECTIONS[lane]) for lane in C.LANES_PER_LOT],
            }
            for raw in aliases
        ],
    }
    params = scenarios.build_from_request(body)
    assert {lot.lot_id for lot in params.lots} == set(C.LOT_CAPACITIES)
    for raw, canonical in aliases.items():
        assert normalise_lot_id(raw) == canonical
    validate_run_params(params)


def test_build_from_request_missing_lanes_fall_back_to_defaults():
    body = {
        "name": "một làn",
        "lots": [{"lot_id": "A", "lanes": [lane_spec("L1", IN, lam_in=0.5, lam_out=0.5)]}],
    }
    params = scenarios.build_from_request(body)
    a = lanes_of(params, "A")
    assert a["L1"].lambda_in == 0.5
    assert a["L2"].direction == C.DEFAULT_DIRECTIONS["L2"]
    assert a["L3"].direction == C.DEFAULT_DIRECTIONS["L3"]


def test_build_from_request_requires_scenario_or_lots():
    with pytest.raises(ValidationError) as exc:
        scenarios.build_from_request({})
    assert exc.value.code == "invalid_params"
    with pytest.raises(ValidationError):
        scenarios.build_from_request({"name": "rỗng", "lots": []})


def test_build_from_request_unknown_lane_is_rejected():
    body = {
        "lots": [
            {
                "lot_id": "A",
                "lanes": [lane_spec("L9", IN)],
            }
        ]
    }
    with pytest.raises(ValidationError) as exc:
        scenarios.build_from_request(body)
    assert exc.value.code == "unknown_lane"
    assert exc.value.details["lane_id"] == "L9"


def test_build_from_request_unknown_lot_is_rejected():
    body = {"lots": [{"lot_id": "ZZ", "lanes": []}]}
    with pytest.raises(ValidationError) as exc:
        scenarios.build_from_request(body)
    assert exc.value.code == "unknown_lot"


def test_build_from_request_lopsided_needs_confirmation():
    body = {
        "name": "lệch",
        "lots": [
            {"lot_id": "A", "lanes": [lane_spec(lane, IN) for lane in C.LANES_PER_LOT]}
        ],
    }
    with pytest.raises(ValidationError) as exc:
        scenarios.build_from_request(body)
    assert exc.value.code == "lopsided_needs_confirm"
    assert exc.value.details["requires_confirmation"] is True

    params = scenarios.build_from_request({**body, "confirm_lopsided": True})
    assert params.allow_lopsided is True
    validate_run_params(params)


def test_build_from_request_confirm_lopsided_works_with_preset():
    params = scenarios.build_from_request({"scenario_id": "mot_lan_vao", "confirm_lopsided": True})
    assert params.allow_lopsided is True
    assert params.scenario_id == "mot_lan_vao"


def test_build_from_request_rejects_bad_types():
    with pytest.raises(ValidationError) as exc:
        scenarios.build_from_request({"scenario_id": "binh_thuong", "seed": "abc"})
    assert exc.value.code == "invalid_params"
    with pytest.raises(ValidationError) as exc:
        scenarios.build_from_request({"lots": "abc"})
    assert exc.value.code == "invalid_params"
