"""Fixture dùng chung cho toàn bộ test của mô phỏng bãi xe."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from simulation import config as C  # noqa: E402
from simulation.models import IN, OUT, LaneConfig, LotConfig, RunParams  # noqa: E402


def build_lane(lane_id, direction, lam_in=0.0, lam_out=0.0, open_=True):
    """Tạo LaneConfig với λ mặc định = 0 để test tất định."""
    return LaneConfig(
        lane_id=lane_id,
        direction=direction,
        lambda_in=lam_in,
        lambda_out=lam_out,
        open=open_,
    )


def build_params(**overrides):
    """4 nhà xe × 4 làn theo mặc định; mọi làn có λ = 0 nên không sinh xe nếu không cấu hình."""
    lots = []
    for lot_id in C.LOT_CAPACITIES:
        lanes = tuple(build_lane(lane, C.DEFAULT_DIRECTIONS[lane]) for lane in C.LANES_PER_LOT)
        lots.append(LotConfig(lot_id=lot_id, lanes=lanes))
    data: dict = dict(
        name="test",
        scenario_id="test",
        lots=tuple(lots),
        processing_time_s=C.PROCESSING_TIME_S,
        seed=1234,
        allow_lopsided=True,
        max_sim_seconds=None,
    )
    data.update(overrides)
    return RunParams(**data)


@pytest.fixture
def params():
    return build_params()


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """DB SQLite tạm: không bao giờ chạm vào traffic_monitor.db thật."""
    import database
    import simulation.store as store

    monkeypatch.setattr(database, "DB_NAME", tmp_path / "test_sim.db")
    store.init_sim_db()
    return database.DB_NAME
