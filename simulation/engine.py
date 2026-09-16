"""Động cơ mô phỏng bãi xe — thuần Python, chỉ dùng thư viện chuẩn.

1 tick = 1 giây mô phỏng. Thứ tự trong :meth:`SimulationEngine.tick`::

    Sinh xe đến -> Xử lý cổng (hoàn tất xe đang phục vụ) -> Nạp cổng
    (quyết định nhận/điều hướng/từ chối) -> Áp dụng chuyển hướng làn
    -> Đánh giá cảnh báo -> sim_time += 1 -> trả snapshot

Không import flask / cv2 / ultralytics / simulation.store.
"""

from __future__ import annotations

import math
import random

from . import config as C
from .models import IN, OUT, LaneState, LotState, RunParams, Vehicle
from .validation import ValidationError


def poisson(lam: float, rng: random.Random) -> int:
    """Số xe đến trong 1 giây theo phân phối Poisson (thuật toán Knuth).

    λ nhỏ (<= 2 xe/giây) nên vòng lặp rất ngắn.
    """
    if lam <= 0:
        return 0
    limit = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


class SimulationEngine:
    """Trạng thái 1 lượt mô phỏng: các nhà xe, làn, hàng chờ và số liệu."""

    def __init__(self, params: RunParams, rng: random.Random | None = None):
        self.params = params
        self.rng = rng if rng is not None else random.Random(params.seed)
        self.sim_time = 0
        self.next_vehicle_id = 1
        self.events: list[dict] = []       # sự kiện sinh ra trong tick hiện tại
        self.completed: list[dict] = []    # xe xử lý xong trong tick hiện tại
        self.lots: dict[str, LotState] = {}
        for lot_cfg in params.lots:
            lot = LotState(lot_id=lot_cfg.lot_id, capacity=C.LOT_CAPACITIES[lot_cfg.lot_id])
            for lane_cfg in lot_cfg.lanes:
                lot.lanes[lane_cfg.lane_id] = LaneState(
                    lane_cfg.lane_id, lane_cfg.direction, lane_cfg.open
                )
            self.lots[lot_cfg.lot_id] = lot

    # ------------------------------------------------------------------ tick
    def tick(self) -> dict:
        """Tiến 1 giây mô phỏng và trả về snapshot mới."""
        t = self.sim_time
        self.events, self.completed = [], []
        for lot in self.lots.values():
            self._generate_arrivals(lot, t)
        for lot in self.lots.values():
            for lane in lot.lanes.values():
                self._advance_gate(lot, lane)
        for lot in self.lots.values():
            for lane in lot.lanes.values():
                self._load_gate(lot, lane, t)
        self._apply_conversions(t)
        self._evaluate_warnings(t)
        self.sim_time += 1
        return self.snapshot()

    def _log(self, type_: str, sim_time: int, lot_id=None, lane_id=None, reason=None, **payload):
        self.events.append(
            {
                "type": type_,
                "sim_time": sim_time,
                "lot_id": lot_id,
                "lane_id": lane_id,
                "reason": reason,
                "payload": payload,
            }
        )

    # ------------------------------------------------------------- truy cập
    def _lot(self, lot_id: str) -> LotState:
        lot = self.lots.get(lot_id)
        if lot is None:
            raise ValidationError(f"Không tìm thấy nhà xe {lot_id}.", "unknown_lot", {"lot_id": lot_id})
        return lot

    def _lane(self, lot_id: str, lane_id: str) -> LaneState:
        lot = self._lot(lot_id)
        lane = lot.lanes.get(lane_id)
        if lane is None:
            raise ValidationError(
                f"Không tìm thấy làn {lane_id} trong nhà xe {lot_id}.",
                "unknown_lane",
                {"lot_id": lot_id, "lane_id": lane_id},
            )
        return lane

    # ------------------------------------------------------------ xe đến
    def _generate_arrivals(self, lot: LotState, t: int) -> None:
        """Mỗi làn đang mở bốc Poisson(λ) xe/giây; làn đóng/đang chờ chuyển thì không."""
        for lane in lot.lanes.values():
            if not lane.open or lane.pending_direction is not None:
                continue
            lam = self.params.lambda_for(lot.lot_id, lane.lane_id, lane.direction)
            n = poisson(lam, self.rng)
            if lane.direction == IN:
                for _ in range(n):
                    lane.queue.append(Vehicle(self.next_vehicle_id, t, IN, lot.lot_id))
                    self.next_vehicle_id += 1
            else:
                # Không thể xuất nhiều xe hơn số xe nhà xe đang thực chứa.
                available = lot.occupancy - self._outstanding_out(lot)
                n = max(0, min(n, available))
                for _ in range(n):
                    lane.queue.append(Vehicle(self.next_vehicle_id, t, OUT, lot.lot_id))
                    self.next_vehicle_id += 1
            lane.arrivals_window.append((t, n))

    def _outstanding_out(self, lot: LotState) -> int:
        """Số xe ra đã được sinh nhưng chưa rời khỏi bãi (hàng chờ + đang xử lý)."""
        total = 0
        for lane in lot.lanes.values():
            if lane.direction == OUT:
                total += len(lane.queue) + (1 if lane.current is not None else 0)
        return total

    # ------------------------------------------------------------- cổng
    def _advance_gate(self, lot: LotState, lane: LaneState) -> None:
        """Tiến triển xử lý tại cổng; mỗi xe chiếm đúng PROCESSING_TIME_S giây."""
        if lane.current is None:
            return
        lane.progress += C.TICK_S / self.params.processing_time_s
        if lane.progress < 1.0:
            return
        lane.progress -= 1.0
        vehicle = lane.current
        lane.current = None
        # xe ở cổng luôn có gate_start_time; `or 0` chỉ để tránh cảnh báo kiểu Optional
        wait_s = (vehicle.gate_start_time or 0) - vehicle.enqueue_time
        lane.wait_samples += 1
        lane.wait_sum_s += wait_s
        if vehicle.direction == IN:
            target = self.lots[vehicle.entry_lot]
            target.occupancy += 1
            target.admitted += 1
        else:
            lot.occupancy = max(0, lot.occupancy - 1)
            lot.departed += 1
        lane.processed_total += 1
        self.completed.append(
            {
                "vehicle_id": vehicle.vid,
                "sim_time": self.sim_time,
                "lane_id": lane.lane_id,
                "lot_id": vehicle.entry_lot or lot.lot_id,
                "direction": vehicle.direction,
                "wait_s": float(wait_s),
                "processing_s": float(self.params.processing_time_s),
                "total_s": float(wait_s) + float(self.params.processing_time_s),
            }
        )

    def _load_gate(self, lot: LotState, lane: LaneState, t: int) -> None:
        """Lấy xe đầu hàng chờ vào cổng (kể cả làn đang xả/đang chờ chuyển)."""
        if lane.current is not None or not lane.queue:
            return
        vehicle = lane.queue.popleft()
        if vehicle.direction == IN and not self._try_admit(lot, lane, vehicle, t):
            return                       # đã điều hướng sang nhà xe khác hoặc bị từ chối
        if vehicle.direction == OUT:
            vehicle.entry_lot = lot.lot_id
        vehicle.gate_start_time = t
        lane.current = vehicle

    # ------------------------------------------- sức chứa, điều hướng, từ chối
    def _free_slots(self, lot: LotState) -> int:
        """Chỗ trống còn lại, trừ cả xe đang trong 4 giây xử lý tại cổng (pending_in_flight)."""
        pending = 0
        for lane in lot.lanes.values():
            if lane.direction == IN and lane.current is not None and lane.current.entry_lot == lot.lot_id:
                pending += 1
        return lot.capacity - lot.occupancy - pending

    def _try_admit(self, lot: LotState, lane: LaneState, vehicle: Vehicle, t: int) -> bool:
        """Nhận xe vào nhà xe nếu còn chỗ; nếu đầy thì điều hướng, cuối cùng mới từ chối."""
        if self._free_slots(lot) > 0:
            vehicle.entry_lot = lot.lot_id
            return True
        for candidate in self._redirect_candidates(lot):
            target_lane = self._shortest_open_in_lane(candidate)
            if target_lane is None or vehicle.hops >= C.MAX_REDIRECT_HOPS:
                continue
            vehicle.hops += 1
            vehicle.dest_lot = candidate.lot_id
            target_lane.queue.append(vehicle)   # giữ nguyên enqueue_time => vẫn tính thời gian chờ
            lot.redirected_out += 1
            candidate.redirected_in += 1
            self._log(
                "redirect",
                t,
                lot.lot_id,
                lane.lane_id,
                reason="lot_full",
                vehicle_id=vehicle.vid,
                from_lot=lot.lot_id,
                to_lot=candidate.lot_id,
                destination_lane=target_lane.lane_id,
                hops=vehicle.hops,
            )
            return False
        lot.rejected += 1
        self._log(
            "reject", t, lot.lot_id, lane.lane_id, reason="no_capacity",
            vehicle_id=vehicle.vid, dest_lot=lot.lot_id,
        )
        return False

    def _redirect_candidates(self, lot: LotState) -> list[LotState]:
        """Các nhà xe khác còn chỗ, xếp nhà xe trống nhất (tỉ lệ lấp đầy thấp) lên trước."""
        others = [l for l in self.lots.values() if l.lot_id != lot.lot_id and self._free_slots(l) > 0]
        others.sort(key=lambda l: (l.occupancy / l.capacity, l.lot_id))
        return others

    def _shortest_open_in_lane(self, lot: LotState) -> LaneState | None:
        lanes = [
            l for l in lot.lanes.values()
            if l.direction == IN and l.open and l.pending_direction is None
        ]
        lanes.sort(key=lambda l: (len(l.queue), l.lane_id))
        return lanes[0] if lanes else None

    # ---------------------------------------------------- điều khiển bằng tay
    def open_lane(self, lot_id: str, lane_id: str) -> None:
        lane = self._lane(lot_id, lane_id)
        if not lane.open and lane.pending_direction is None:
            lane.open = True
            self._log("open", self.sim_time, lot_id, lane_id)

    def close_lane(self, lot_id: str, lane_id: str) -> None:
        """Đóng làn: ngừng nhận xe mới nhưng vẫn xả hết hàng chờ + xe đang xử lý."""
        lot = self._lot(lot_id)
        lane = self._lane(lot_id, lane_id)
        if sum(1 for l in lot.lanes.values() if l.open) <= 1 and lane.open:
            raise ValidationError(
                f"Nhà xe {lot_id} phải giữ ít nhất 1 làn mở.",
                "min_open_lanes",
                {"lot_id": lot_id},
            )
        if lane.open:
            lane.open = False
            self._log("close", self.sim_time, lot_id, lane_id)

    def request_conversion(self, lot_id: str, lane_id: str, target: str) -> None:
        """Yêu cầu chuyển hướng làn ('in'/'out'); chỉ áp dụng khi làn đã xả hết."""
        if target not in (IN, OUT):
            raise ValidationError(
                "Hướng chuyển đổi không hợp lệ (chỉ nhận 'in' hoặc 'out').",
                "bad_direction",
                {"target": target},
            )
        lane = self._lane(lot_id, lane_id)
        if lane.pending_direction is not None:
            raise ValidationError(
                f"Làn {lot_id}/{lane_id} đang chờ chuyển đổi.",
                "conversion_pending",
                {"lot_id": lot_id, "lane_id": lane_id},
            )
        if lane.direction == target:
            raise ValidationError(
                f"Làn {lot_id}/{lane_id} đã ở hướng này.",
                "already_direction",
                {"lot_id": lot_id, "lane_id": lane_id, "direction": lane.direction},
            )
        lane.pending_direction = target
        self._log(
            "convert_request", self.sim_time, lot_id, lane_id, reason="user_request",
            from_direction=lane.direction, to_direction=target,
        )

    def _apply_conversions(self, t: int) -> None:
        """Chỉ đổi hướng làn khi hàng chờ rỗng và không còn xe tại cổng."""
        for lot in self.lots.values():
            for lane in lot.lanes.values():
                if lane.pending_direction is None:
                    continue
                if lane.current is not None or lane.queue:
                    continue
                previous, lane.direction = lane.direction, lane.pending_direction
                lane.pending_direction = None
                lane.progress = 0.0
                self._log(
                    "convert_done", t, lot.lot_id, lane.lane_id,
                    from_direction=previous, to_direction=lane.direction,
                )

    # -------------------------------------------------------------- cảnh báo
    def _evaluate_warnings(self, t: int) -> None:
        for lot in self.lots.values():
            for lane in lot.lanes.values():
                overloaded = lane.waiting >= C.OVERCROWD_THRESHOLD
                if overloaded and not lane.overload_open:
                    lane.overload_open = True
                    lane.overload_events += 1
                    self._log(
                        "overcrowd_start", t, lot.lot_id, lane.lane_id,
                        reason="lane_overcrowded", waiting=lane.waiting,
                        threshold=C.OVERCROWD_THRESHOLD,
                    )
                elif not overloaded and lane.overload_open:
                    lane.overload_open = False
                    self._log(
                        "overcrowd_end", t, lot.lot_id, lane.lane_id, waiting=lane.waiting
                    )
                if lane.overload_open:
                    lane.overload_seconds += 1
            ratio = lot.occupancy / lot.capacity if lot.capacity else 1.0
            near_full = ratio >= C.NEAR_FULL_RATIO
            if near_full and not lot.near_full_open:
                lot.near_full_open = True
                lot.near_full_events += 1
                self._log(
                    "near_full_start", t, lot.lot_id, reason="lot_near_capacity",
                    occupancy=lot.occupancy, capacity=lot.capacity, fill_ratio=round(ratio, 4),
                )
            elif not near_full and lot.near_full_open:
                lot.near_full_open = False
                self._log("near_full_end", t, lot.lot_id, occupancy=lot.occupancy)
            if lot.near_full_open:
                lot.near_full_seconds += 1

    # -------------------------------------------------------------- snapshot
    def _observed_rate(self, lane: LaneState) -> float:
        """Tốc độ xe đến quan sát được (xe/giây) trong ARRIVAL_WINDOW_S giây gần nhất."""
        window_start = self.sim_time - C.ARRIVAL_WINDOW_S + 1
        while lane.arrivals_window and lane.arrivals_window[0][0] < window_start:
            lane.arrivals_window.popleft()
        if not lane.arrivals_window:
            return 0.0
        return round(sum(n for _, n in lane.arrivals_window) / max(1, len(lane.arrivals_window)), 3)

    def _clock_label(self) -> str:
        total = int(self.sim_time)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    def snapshot(self) -> dict:
        """Payload 1 lần polling (hợp đồng với frontend — không đổi tên khoá)."""
        lots, warnings = [], []
        totals = {
            "admitted": 0, "departed": 0, "redirected": 0,
            "rejected": 0, "waiting": 0, "vehicles_processed": 0,
        }
        for lot in self.lots.values():
            gates = []
            for lane in lot.lanes.values():
                avg_wait = round(lane.wait_sum_s / lane.wait_samples, 2) if lane.wait_samples else None
                gates.append(
                    {
                        "lane_id": lane.lane_id,
                        "direction": lane.direction,
                        "gate_state": lane.gate_state,
                        "open": lane.open,
                        "pending_direction": lane.pending_direction,
                        "waiting": lane.waiting,
                        "processing": lane.current is not None,
                        "processing_rate": round(1 / self.params.processing_time_s, 3) if lane.open else 0.0,
                        "arrival_rate": self._observed_rate(lane),
                        "avg_wait_s": avg_wait,
                        "processed_total": lane.processed_total,
                        "overloaded": lane.waiting >= C.OVERCROWD_THRESHOLD,
                    }
                )
                if lane.waiting >= C.OVERCROWD_THRESHOLD:
                    warnings.append(
                        {
                            "level": "lane", "lot_id": lot.lot_id, "lane_id": lane.lane_id,
                            "message": (
                                f"Làn {lot.lot_id}/{lane.lane_id} đang tắc nghẽn "
                                f"(≥{C.OVERCROWD_THRESHOLD} xe chờ)"
                            ),
                        }
                    )
                totals["waiting"] += lane.waiting
                totals["vehicles_processed"] += lane.processed_total
            fill_ratio = round(lot.occupancy / lot.capacity, 4) if lot.capacity else 1.0
            if fill_ratio >= C.NEAR_FULL_RATIO:
                warnings.append(
                    {
                        "level": "lot", "lot_id": lot.lot_id, "lane_id": None,
                        "message": (
                            f"Nhà xe {lot.lot_id} sắp đầy "
                            f"({int(fill_ratio * 100)}% ≥ {int(C.NEAR_FULL_RATIO * 100)}%)"
                        ),
                    }
                )
            lots.append(
                {
                    "lot_id": lot.lot_id,
                    "label": C.LOT_LABELS[lot.lot_id],
                    "capacity": lot.capacity,
                    "occupancy": lot.occupancy,
                    "fill_ratio": fill_ratio,
                    "admitted": lot.admitted,
                    "departed": lot.departed,
                    "redirected_in": lot.redirected_in,
                    "redirected_out": lot.redirected_out,
                    "rejected": lot.rejected,
                    "gates": gates,
                }
            )
            totals["admitted"] += lot.admitted
            totals["departed"] += lot.departed
            totals["redirected"] += lot.redirected_in
            totals["rejected"] += lot.rejected
        return {
            "sim_time": self.sim_time,
            "clock": self._clock_label(),
            "totals": totals,
            "lots": lots,
            "warnings": warnings,
        }
