"""Tầng dịch vụ: vòng đời 1 lượt mô phỏng (chạy / tạm dừng / tiếp tục / dừng).

- :class:`ActiveRun`  — 1 lượt đang chạy: engine, thread nhịp thời gian thực, khoá.
- :class:`RunManager` — quản lý nhiều lượt theo thời gian: chỉ 1 lượt chạy tại một thời điểm.
- ``manager``         — singleton mà ``simulation/routes.py`` dùng.

Nhịp thời gian: ``deadline = monotonic() + 1s`` và ``deadline += 1s`` sau mỗi tick nên
đồng hồ mô phỏng không bị trôi. Khi máy bận (trễ > 5 s) thì bỏ qua phần trễ, KHÔNG dồn
tick bù. Tạm dừng thì dịch ``deadline`` và cộng dồn ``paused_seconds`` (chỉ thời gian thực).
"""

from __future__ import annotations

import threading
import time

from . import config as C
from . import store
from .engine import SimulationEngine
from .summary import build_summary
from .validation import ValidationError, normalise_lot_id, validate_run_params

POLL_SLEEP_S = 0.05          # bước ngủ nhỏ nhất khi chờ tới hạn tick
MAX_STALL_S = 5.0            # trễ quá ngần này thì bỏ qua, không dồn tick bù


class RunConflict(Exception):
    """Thao tác điều khiển không áp dụng được cho trạng thái lượt chạy hiện tại."""

    def __init__(self, message: str, code: str = "run_conflict", details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


def _find_gate(snapshot: dict, lot_id: str, lane_id: str) -> dict:
    """Lấy gate dict của 1 làn từ snapshot mới nhất."""
    for lot in snapshot["lots"]:
        if lot["lot_id"] != lot_id:
            continue
        for gate in lot["gates"]:
            if gate["lane_id"] == lane_id:
                return gate
    raise ValidationError(
        f"Không tìm thấy làn {lane_id} trong nhà xe {lot_id}.",
        "unknown_lane",
        {"lot_id": lot_id, "lane_id": lane_id},
    )


class ActiveRun:
    """Một lượt mô phỏng đang chạy: 1 tick/giây, có thể tạm dừng/tiếp tục/dừng."""

    def __init__(self, run_id: int, engine, thread_factory=threading.Thread):
        self.run_id = int(run_id)
        self.engine = engine
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.snapshot = engine.snapshot()
        self.paused_seconds = 0.0
        self.paused_since = 0.0
        self.started_real = time.monotonic()
        self.stop_reason = None
        self._thread = thread_factory(
            target=self._loop, daemon=True, name=f"sim-run-{self.run_id}"
        )

    # ---------------------------------------------------------------- trạng thái
    @property
    def paused(self) -> bool:
        """Đang tạm dừng?"""
        return self._paused.is_set()

    @property
    def stopped(self) -> bool:
        """Đã dừng?"""
        return self._stop.is_set()

    def is_alive(self) -> bool:
        """Còn chạy (chưa dừng và thread còn sống)?"""
        return not self._stop.is_set() and bool(self._thread.is_alive())

    def start(self) -> None:
        """Khởi động thread nhịp thời gian thực."""
        self._thread.start()

    # -------------------------------------------------------------------- tick
    def step(self) -> dict:
        """Đúng MỘT tick mô phỏng + ghi số liệu. Tách riêng để test không cần sleep."""
        with self.lock:
            snapshot = self.engine.tick()
            store.write_tick(self.run_id, snapshot, self.engine.events, self.engine.completed)
            self.snapshot = snapshot
            return snapshot

    def flush_events(self) -> None:
        """Ghi các sự kiện sinh ra NGOÀI tick (mở/đóng/chuyển làn) rồi xoá khỏi engine."""
        for event in self.engine.events:
            store.log_event(
                self.run_id,
                event["sim_time"],
                event["type"],
                event.get("lot_id"),
                event.get("lane_id"),
                event.get("reason"),
                **(event.get("payload") or {}),
            )
        self.engine.events = []

    def _loop(self) -> None:  # pragma: no cover - kiểm thử qua step() và đồng hồ giả
        """Nhịp thời gian thực: 1 tick/giây, hiệu chỉnh trôi, không dồn tick bù."""
        deadline = time.monotonic() + C.TICK_S
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(POLL_SLEEP_S)
                deadline = time.monotonic() + C.TICK_S
                continue
            now = time.monotonic()
            if now < deadline:
                time.sleep(min(POLL_SLEEP_S, deadline - now))
                continue
            try:
                self.step()
            except Exception:              # pragma: no cover - lỗi ghi DB giữa vòng lặp
                self.stop("error")         # không để lượt chạy treo trạng thái 'running'
                return
            deadline += C.TICK_S
            if now - deadline > MAX_STALL_S:   # tụt quá xa (máy bận) -> bỏ qua, không dồn tick
                deadline = now + C.TICK_S
            limit = self.engine.params.max_sim_seconds
            if limit and self.engine.sim_time >= limit:
                self.stop("max_duration")
                return

    # --------------------------------------------------------------- điều khiển
    def pause(self) -> dict:
        """Tạm dừng: đóng băng đồng hồ và hàng chờ; ghi log + đổi trạng thái DB."""
        with self.lock:
            self._require_live()
            if not self._paused.is_set():
                self._paused.set()
                self.paused_since = time.monotonic()
                store.log_event(self.run_id, self.engine.sim_time, "pause", reason="user_request")
                store.set_run_status(self.run_id, store.STATUS_PAUSED)
            return {"status": "paused", "sim_time": int(self.engine.sim_time)}

    def resume(self) -> dict:
        """Tiếp tục: cộng dồn thời gian đã tạm dừng; ghi log + đổi trạng thái DB."""
        with self.lock:
            self._require_live()
            if self._paused.is_set():
                self._paused.clear()
                self.paused_seconds += max(0.0, time.monotonic() - self.paused_since)
                store.log_event(self.run_id, self.engine.sim_time, "resume", reason="user_request")
                store.set_run_status(self.run_id, store.STATUS_RUNNING)
            return {"status": "running"}

    def stop(self, reason: str = "user_request") -> dict:
        """Dừng lượt: ghi log + lưu bản tổng kết vào DB và trả về bản tổng kết đó."""
        with self.lock:
            self._stop.set()
            self.stop_reason = reason
            store.log_event(self.run_id, self.engine.sim_time, "stop", reason=reason)
            summary = build_summary(self.engine, self.paused_seconds, reason)
            store.save_summary(
                self.run_id,
                summary,
                self.engine.sim_time,
                time.monotonic() - self.started_real,
                self.paused_seconds,
                reason,
            )
            return summary

    def _require_live(self) -> None:
        """Chặn tạm dừng/tiếp tục trên lượt đã dừng."""
        if self._stop.is_set():
            raise RunConflict(
                "Lượt mô phỏng này đã kết thúc.",
                "no_active_run",
                {"run_id": self.run_id},
            )


class RunManager:
    """Quản lý vòng đời lượt mô phỏng: chỉ 1 lượt chạy tại một thời điểm."""

    def __init__(self):
        self._lock = threading.RLock()
        self._run: ActiveRun | None = None
        self._history: list[dict] = []

    # ------------------------------------------------------------------ bắt đầu
    def start(self, params, scenario_id: str | None = None, start_thread: bool = True) -> int:
        """Kiểm tra tham số, ghi ``sim_runs``, tạo engine + thread, trả về ``run_id``.

        ``start_thread=False`` để kiểm thử có thể tự gọi ``step()`` (không cần sleep).
        """
        with self._lock:
            live = self._live_run()
            if live is not None:
                raise RunConflict(
                    "Đang có một lượt mô phỏng chạy. Hãy dừng lượt hiện tại trước.",
                    "run_already_active",
                    {"run_id": live.run_id},
                )
            validate_run_params(params)        # kiểm tra TRƯỚC khi tạo bất kỳ dòng DB nào
            run_id = store.create_run(
                params, scenario_id or params.scenario_id, status=store.STATUS_RUNNING
            )
            engine = SimulationEngine(params)
            run = ActiveRun(run_id, engine)
            self._run = run
            store.log_event(
                run_id, 0, "start", reason="user_request",
                scenario_id=params.scenario_id, seed=params.seed, name=params.name,
            )
            if start_thread:
                run.start()
            return run_id

    # -------------------------------------------------------------- điều khiển
    def pause(self, run_id: int) -> dict:
        """Tạm dừng lượt ``run_id``."""
        with self._lock:
            return self._active(run_id).pause()

    def resume(self, run_id: int) -> dict:
        """Tiếp tục lượt ``run_id``."""
        with self._lock:
            return self._active(run_id).resume()

    def stop(self, run_id: int) -> dict:
        """Dừng lượt ``run_id``, trả về bản tổng kết và lưu vào lịch sử."""
        with self._lock:
            run = self._active(run_id)
            summary = run.stop()
            self._history.append({"run_id": run.run_id, "summary": summary})
            self._run = None
            return summary

    def lane_action(self, run_id: int, lot_id: str, lane_id: str,
                    action: str, target: str | None = None) -> dict:
        """``open`` | ``close`` | ``convert`` một làn; trả về gate dict mới nhất."""
        with self._lock:
            run = self._active(run_id)
            lot = normalise_lot_id(lot_id)
            lane = str(lane_id).strip().upper()
            with run.lock:
                engine = run.engine
                if action == "open":
                    engine.open_lane(lot, lane)
                elif action == "close":
                    engine.close_lane(lot, lane)
                elif action == "convert":
                    engine.request_conversion(lot, lane, target)
                else:
                    raise RunConflict(
                        f"Hành động không hợp lệ: {action} (chỉ nhận open/close/convert).",
                        "invalid_action",
                        {"action": action},
                    )
                run.flush_events()
                run.snapshot = engine.snapshot()
                return _find_gate(run.snapshot, lot, lane)

    # ----------------------------------------------------------------- đọc dữ liệu
    def current(self) -> dict:
        """Payload polling của ``GET /api/sim/runs/current``."""
        with self._lock:
            run = self._run
            if run is None:
                return {"status": "idle"}
            with run.lock:
                return {
                    "status": "paused" if run.paused else "running",
                    "run_id": run.run_id,
                    "params": run.engine.params.to_json(),
                    "snapshot": run.snapshot,
                    "paused_seconds": round(run.paused_seconds, 2),
                }

    def events(self, run_id: int, limit: int = 200) -> list[dict]:
        """Nhật ký sự kiện đã ghi của 1 lượt (mới nhất trước)."""
        return store.list_events(int(run_id), int(limit))

    def reset(self) -> None:
        """Xoá lượt đang chạy khỏi bộ nhớ (dùng cho kiểm thử và khởi động lại)."""
        with self._lock:
            if self._run is not None:
                self._run._stop.set()
            self._run = None
            self._history = []

    # -------------------------------------------------------------------- nội bộ
    def _live_run(self) -> ActiveRun | None:
        """Lượt còn "sống" (chưa được dừng / chưa reset)."""
        run = self._run
        if run is None or run.stopped:
            return None
        return run

    def _active(self, run_id: int) -> ActiveRun:
        run = self._run
        if run is None or int(run_id) != run.run_id:
            raise RunConflict(
                "Không có lượt mô phỏng nào đang chạy với mã này.",
                "no_active_run",
                {"run_id": run_id},
            )
        return run


manager = RunManager()      # singleton dùng chung cho toàn ứng dụng (routes.py)