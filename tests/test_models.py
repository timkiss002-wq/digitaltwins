"""Test dataclass miền: cấu hình bất biến, trạng thái làn, helper lambda_for/to_json."""

import dataclasses
import json

import pytest

from simulation import config as C
from simulation.models import (
    GATE_CLOSED,
    GATE_DRAINING,
    GATE_OPEN,
    GATE_PENDING,
    IN,
    OUT,
    LaneState,
    LotConfig,
    LotState,
    Vehicle,
)
from tests.conftest import build_lane, build_params


def test_lambda_for_selects_direction():
    params = build_params()
    assert params.lambda_for("A", "L1", IN) == 0.0
    assert params.lambda_for("A", "L1", OUT) == 0.0
    assert params.lambda_for("khong-ton-tai", "L1", IN) == 0.0


def test_lambda_for_reads_configured_values():
    lanes = tuple(
        build_lane(lane, C.DEFAULT_DIRECTIONS[lane], lam_in=0.5, lam_out=0.05)
        for lane in C.LANES_PER_LOT
    )
    params = build_params(lots=(LotConfig(lot_id="A", lanes=lanes),))
    assert params.lambda_for("A", "L1", IN) == 0.5
    assert params.lambda_for("A", "L1", OUT) == 0.05


def test_run_params_is_frozen():
    params = build_params()
    with pytest.raises(dataclasses.FrozenInstanceError):
        params.name = "khac"


def test_run_params_to_json_is_serialisable_and_round_trips():
    params = build_params()
    payload = params.to_json()
    text = json.dumps(payload)
    restored = json.loads(text)
    assert restored["name"] == "test"
    assert restored["lots"][0]["lot_id"] == "A"
    assert len(restored["lots"]) == len(C.LOT_CAPACITIES)
    assert restored["lots"][0]["lanes"][0]["lane_id"] == "L1"
    assert restored["lots"][0]["lanes"][0]["lambda_in"] == 0.0


def test_lane_state_gate_states():
    assert LaneState("L1", IN).gate_state == GATE_OPEN

    closed_empty = LaneState("L1", IN, open=False)
    assert closed_empty.gate_state == GATE_CLOSED

    closed_busy = LaneState("L1", IN, open=False)
    closed_busy.queue.append(Vehicle(1, 0, IN, "A"))
    assert closed_busy.gate_state == GATE_DRAINING

    converting = LaneState("L1", IN)
    converting.pending_direction = OUT
    assert converting.gate_state == GATE_PENDING


def test_lane_state_waiting_counts_queue_only():
    lane = LaneState("L1", IN)
    for vid in range(3):
        lane.queue.append(Vehicle(vid, 0, IN, "A"))
    lane.current = Vehicle(99, 0, IN, "A")
    assert lane.waiting == 3


def test_lot_state_defaults():
    lot = LotState("A", 1100)
    assert lot.capacity == 1100
    assert lot.occupancy == 0
    assert lot.lanes == {}
    assert lot.near_full_open is False


def test_vehicle_defaults():
    vehicle = Vehicle(1, 5, IN, "A")
    assert vehicle.entry_lot == ""
    assert vehicle.hops == 0
    assert vehicle.gate_start_time is None
