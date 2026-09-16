"""Dataclass cho cấu hình (bất biến) và trạng thái (thay đổi) của mô phỏng."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

IN, OUT = "in", "out"

GATE_OPEN = "open"
GATE_CLOSED = "closed"
GATE_DRAINING = "draining"
GATE_PENDING = "pending_conversion"


@dataclass(frozen=True)
class LaneConfig:
    """Cấu hình 1 làn: hướng, λ vào/ra (xe/giây), trạng thái mở ban đầu."""

    lane_id: str
    direction: str            # IN | OUT
    lambda_in: float = 0.0
    lambda_out: float = 0.0
    open: bool = True


@dataclass(frozen=True)
class LotConfig:
    lot_id: str
    lanes: tuple[LaneConfig, ...]


@dataclass(frozen=True)
class RunParams:
    """Tham số của 1 lượt mô phỏng - bất biến sau khi run bắt đầu."""

    name: str
    scenario_id: str
    lots: tuple[LotConfig, ...]
    processing_time_s: float = 4.0
    seed: int = 0
    allow_lopsided: bool = False
    max_sim_seconds: int | None = None

    def lambda_for(self, lot_id: str, lane_id: str, direction: str) -> float:
        for lot in self.lots:
            if lot.lot_id != lot_id:
                continue
            for lane in lot.lanes:
                if lane.lane_id == lane_id:
                    return lane.lambda_in if direction == IN else lane.lambda_out
        return 0.0

    def to_json(self) -> dict:
        """Bản sao dạng dict thuần để lưu DB / trả API (JSON-serialisable)."""
        return {
            "name": self.name,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "processing_time_s": self.processing_time_s,
            "allow_lopsided": self.allow_lopsided,
            "max_sim_seconds": self.max_sim_seconds,
            "lots": [
                {
                    "lot_id": lot.lot_id,
                    "lanes": [
                        {
                            "lane_id": lane.lane_id,
                            "direction": lane.direction,
                            "lambda_in": lane.lambda_in,
                            "lambda_out": lane.lambda_out,
                            "open": lane.open,
                        }
                        for lane in lot.lanes
                    ],
                }
                for lot in self.lots
            ],
        }


@dataclass
class Vehicle:
    vid: int
    enqueue_time: int
    direction: str
    dest_lot: str            # nhà xe mong muốn ban đầu
    entry_lot: str = ""      # nhà xe thực tế nhận xe (đặt khi bắt đầu xử lý)
    hops: int = 0
    gate_start_time: int | None = None


@dataclass
class LaneState:
    lane_id: str
    direction: str
    open: bool = True
    queue: deque = field(default_factory=deque)
    current: Vehicle | None = None
    progress: float = 0.0
    pending_direction: str | None = None
    processed_total: int = 0
    wait_samples: int = 0
    wait_sum_s: float = 0.0
    arrivals_window: deque = field(default_factory=deque)   # (sim_time, số xe đến)
    overload_events: int = 0
    overload_seconds: int = 0
    overload_open: bool = False

    @property
    def waiting(self) -> int:
        """Số xe đang chờ trong làn (xe đang ở cổng được tính là 'đang xử lý')."""
        return len(self.queue)

    @property
    def gate_state(self) -> str:
        if self.pending_direction is not None:
            return GATE_PENDING
        if not self.open:
            return GATE_DRAINING if (self.current is not None or self.queue) else GATE_CLOSED
        return GATE_OPEN


@dataclass
class LotState:
    lot_id: str
    capacity: int
    lanes: dict = field(default_factory=dict)
    occupancy: int = 0
    admitted: int = 0
    departed: int = 0
    redirected_in: int = 0
    redirected_out: int = 0
    rejected: int = 0
    near_full_events: int = 0
    near_full_seconds: int = 0
    near_full_open: bool = False
