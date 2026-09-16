"""Tầng lưu trữ SQLite cho mô phỏng bãi xe.

Dùng lại ``database.get_connection()`` (đã bật WAL + busy_timeout) nên mọi bản ghi
nằm trong cùng file ``traffic_monitor.db`` với các bảng của camera. Năm bảng mới:

    sim_runs          - vòng đời 1 lượt mô phỏng (tham số, trạng thái, tổng kết)
    sim_lane_metrics  - 1 dòng / làn / giây mô phỏng
    sim_lot_metrics   - 1 dòng / nhà xe / giây mô phỏng
    sim_events        - sự kiện rời rạc (pause, resume, stop, mở/đóng/chuyển làn, ...)
    sim_vehicles      - 1 dòng / xe xử lý xong (thời gian chờ, thời gian xử lý)

Trạng thái hợp lệ của 1 run: ``running``, ``paused``, ``stopped``, ``interrupted``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from database import get_connection

from . import config as C

# ---- từ vựng trạng thái (dùng thống nhất giữa DB, service và UI) ----
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_INTERRUPTED = "interrupted"
STATUSES = (STATUS_RUNNING, STATUS_PAUSED, STATUS_STOPPED, STATUS_INTERRUPTED)

LIVE_STATUSES = (STATUS_RUNNING, STATUS_PAUSED)
STOP_REASON_SERVER_RESTART = "server_restart"

# DDL chính xác theo Task 2.1 của kế hoạch (chạy với IF NOT EXISTS nên idempotent).
DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS sim_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, scenario_id TEXT, name TEXT NOT NULL,
      status TEXT NOT NULL, seed INTEGER NOT NULL, params_json TEXT NOT NULL,
      started_at TEXT NOT NULL, ended_at TEXT, sim_seconds INTEGER DEFAULT 0,
      paused_seconds REAL DEFAULT 0, stop_reason TEXT, summary_json TEXT)
    """,
    """
    CREATE TABLE IF NOT EXISTS sim_lane_metrics (
      run_id INTEGER NOT NULL, sim_time INTEGER NOT NULL, lot_id TEXT NOT NULL, lane_id TEXT NOT NULL,
      direction TEXT NOT NULL, gate_state TEXT NOT NULL, waiting INTEGER NOT NULL,
      processed_total INTEGER NOT NULL, arrival_rate REAL NOT NULL, avg_wait_s REAL,
      overloaded INTEGER NOT NULL, PRIMARY KEY (run_id, sim_time, lot_id, lane_id))
    """,
    """
    CREATE TABLE IF NOT EXISTS sim_lot_metrics (
      run_id INTEGER NOT NULL, sim_time INTEGER NOT NULL, lot_id TEXT NOT NULL,
      occupancy INTEGER NOT NULL, capacity INTEGER NOT NULL, fill_ratio REAL NOT NULL,
      near_full INTEGER NOT NULL, PRIMARY KEY (run_id, sim_time, lot_id))
    """,
    """
    CREATE TABLE IF NOT EXISTS sim_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, sim_time INTEGER NOT NULL,
      wall_time TEXT NOT NULL, type TEXT NOT NULL, lot_id TEXT, lane_id TEXT, reason TEXT,
      payload_json TEXT)
    """,
    "CREATE INDEX IF NOT EXISTS idx_sim_events_run ON sim_events (run_id, sim_time)",
    """
    CREATE TABLE IF NOT EXISTS sim_vehicles (
      id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, vehicle_id INTEGER NOT NULL,
      sim_time INTEGER NOT NULL, lot_id TEXT NOT NULL, lane_id TEXT NOT NULL, direction TEXT NOT NULL,
      wait_s REAL NOT NULL, processing_s REAL NOT NULL, total_s REAL NOT NULL)
    """,
    "CREATE INDEX IF NOT EXISTS idx_sim_vehicles_run ON sim_vehicles (run_id, lot_id)",
)


# --------------------------------------------------------------------------- #
# Trợ giúp nội bộ
# --------------------------------------------------------------------------- #
def _now() -> str:
    """Thời gian tường (giờ máy) dạng 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clock_label(sim_time: int) -> str:
    """Nhãn đồng hồ 'HH:MM:SS' suy ra từ số giây mô phỏng (giống engine.snapshot()['clock'])."""
    sim_time = int(sim_time)
    return f"{sim_time // 3600:02d}:{(sim_time % 3600) // 60:02d}:{sim_time % 60:02d}"


def _check_status(status: str) -> str:
    if status not in STATUSES:
        raise ValueError(f"Trạng thái không hợp lệ: {status!r} (chỉ nhận {', '.join(STATUSES)}).")
    return status


def _parse_json(text: str | None):
    """JSON text -> dict; None/rỗng/hỏng đều trả None (đọc dữ liệu cũ không làm sập UI)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _row_to_run(row: sqlite3.Row) -> dict:
    """Chuyển 1 dòng sim_runs thành dict thuần (params_json/summary_json đã parse)."""
    run = dict(row)
    run["params"] = _parse_json(run.get("params_json"))
    run["summary"] = _parse_json(run.get("summary_json"))
    return run


# --------------------------------------------------------------------------- #
# Schema (Task 2.1)
# --------------------------------------------------------------------------- #
def init_sim_db() -> None:
    """Tạo 5 bảng + 2 index của mô phỏng (idempotent, gọi được nhiều lần)."""
    conn = get_connection()
    try:
        for statement in DDL_STATEMENTS:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Vòng đời run (Task 2.2)
# --------------------------------------------------------------------------- #
def create_run(params, scenario_id: str, status: str = "running") -> int:
    """Ghi 1 dòng sim_runs, trả về run_id. params_json lưu toàn bộ RunParams (bất biến)."""
    _check_status(status)
    params_json = json.dumps(params.to_json(), ensure_ascii=False)
    name = getattr(params, "name", None) or scenario_id or "run"
    seed = int(getattr(params, "seed", 0) or 0)
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO sim_runs (scenario_id, name, status, seed, params_json, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (scenario_id, name, status, seed, params_json, _now()),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    if run_id is None:                      # pragma: no cover - INSERT luôn có lastrowid
        raise RuntimeError("Không lấy được run_id sau khi INSERT vào sim_runs.")
    return int(run_id)


def set_run_status(run_id: int, status: str) -> None:
    """Đổi trạng thái 1 run (running <-> paused khi tạm dừng/tiếp tục)."""
    _check_status(status)
    conn = get_connection()
    try:
        conn.execute("UPDATE sim_runs SET status = ? WHERE id = ?", (status, run_id))
        conn.commit()
    finally:
        conn.close()


def log_event(run_id: int, sim_time: int, type_: str, lot_id: str | None = None,
              lane_id: str | None = None, reason: str | None = None, **payload) -> None:
    """Ghi 1 sự kiện rời rạc (pause/resume/stop, mở/đóng/chuyển làn từ API, ...)."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sim_events (run_id, sim_time, wall_time, type, lot_id, lane_id, reason,"
            " payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, int(sim_time), _now(), type_, lot_id, lane_id, reason,
             json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def save_summary(run_id: int, summary: dict, sim_seconds: int,
                 real_seconds: float, paused_seconds: float, stop_reason: str) -> None:
    """Ghi summary_json + ended_at + sim_seconds + paused_seconds + stop_reason, status='stopped'.

    Ghi chú: ``real_seconds`` (thời gian thực của lượt chạy) được giữ trong chữ ký cho
    service.py nhưng không có cột riêng trong DDL đã chốt nên không lưu thành cột.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sim_runs SET status = ?, summary_json = ?, ended_at = ?, sim_seconds = ?,"
            " paused_seconds = ?, stop_reason = ? WHERE id = ?",
            (STATUS_STOPPED, json.dumps(summary, ensure_ascii=False), _now(),
             int(sim_seconds), float(paused_seconds), stop_reason, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_runs(limit: int = 20) -> list[dict]:
    """Danh sách lượt mô phỏng, mới nhất trước; summary_json được parse khi có."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM sim_runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [_row_to_run(row) for row in rows]
    finally:
        conn.close()


def get_run(run_id: int) -> dict | None:
    """1 lượt mô phỏng theo id (params/summary đã parse), None nếu không có."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM sim_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row is not None else None
    finally:
        conn.close()


def recover_interrupted_runs() -> int:
    """Đánh dấu các run còn ``running``/``paused`` (server đã khởi động lại) thành ``interrupted``.

    Trả về số dòng bị ảnh hưởng. Gọi lúc khởi động server (main.py).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE sim_runs SET status = ?, stop_reason = ?, ended_at = ?"
            " WHERE status IN (?, ?)",
            (STATUS_INTERRUPTED, STOP_REASON_SERVER_RESTART, _now(),
             STATUS_RUNNING, STATUS_PAUSED),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Ghi số liệu mỗi tick (Task 2.3)
# --------------------------------------------------------------------------- #
def write_tick(run_id: int, snapshot: dict, events: list[dict], completed: list[dict]) -> None:
    """Ghi toàn bộ số liệu của 1 giây mô phỏng trong MỘT transaction.

    16 dòng sim_lane_metrics (4 nhà xe × 4 làn) + 4 dòng sim_lot_metrics + sự kiện +
    xe đã xử lý xong trong tick này.
    """
    sim_time = int(snapshot["sim_time"])
    lane_rows = [
        (run_id, sim_time, lot["lot_id"], gate["lane_id"], gate["direction"], gate["gate_state"],
         int(gate["waiting"]), int(gate["processed_total"]), float(gate["arrival_rate"]),
         gate["avg_wait_s"], int(bool(gate["overloaded"])))
        for lot in snapshot["lots"] for gate in lot["gates"]
    ]
    lot_rows = [
        (run_id, sim_time, lot["lot_id"], int(lot["occupancy"]), int(lot["capacity"]),
         float(lot["fill_ratio"]), int(lot["fill_ratio"] >= C.NEAR_FULL_RATIO))
        for lot in snapshot["lots"]
    ]
    conn = get_connection()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO sim_lane_metrics (run_id, sim_time, lot_id, lane_id, direction,"
            " gate_state, waiting, processed_total, arrival_rate, avg_wait_s, overloaded)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            lane_rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO sim_lot_metrics (run_id, sim_time, lot_id, occupancy, capacity,"
            " fill_ratio, near_full) VALUES (?, ?, ?, ?, ?, ?, ?)",
            lot_rows,
        )
        if events:
            wall_time = _now()
            conn.executemany(
                "INSERT INTO sim_events (run_id, sim_time, wall_time, type, lot_id, lane_id, reason,"
                " payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, int(e["sim_time"]), wall_time, e["type"], e.get("lot_id"),
                  e.get("lane_id"), e.get("reason"),
                  json.dumps(e.get("payload") or {}, ensure_ascii=False)) for e in events],
            )
        if completed:
            conn.executemany(
                "INSERT INTO sim_vehicles (run_id, vehicle_id, sim_time, lot_id, lane_id, direction,"
                " wait_s, processing_s, total_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, int(v["vehicle_id"]), int(v["sim_time"]), v["lot_id"], v["lane_id"],
                  v["direction"], float(v["wait_s"]), float(v["processing_s"]), float(v["total_s"]))
                 for v in completed],
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Đọc dữ liệu cho UI (Task 2.4)
# --------------------------------------------------------------------------- #
def list_events(run_id: int, limit: int = 200) -> list[dict]:
    """Sự kiện của 1 run, mới nhất trước; payload_json được parse thành dict."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM sim_events WHERE run_id = ? ORDER BY sim_time DESC, id DESC LIMIT ?",
            (run_id, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    events = []
    for row in rows:
        event = dict(row)
        event["payload"] = _parse_json(event.get("payload_json")) or {}
        events.append(event)
    return events


def recent_lane_series(run_id: int, lot_id: str, lane_id: str, window: int = 120) -> dict:
    """Chuỗi thời gian gần nhất của 1 làn, sẵn sàng cho Chart.js.

    ``values`` = số xe đang CHỜ trong làn (cột ``waiting`` của sim_lane_metrics) tại mỗi
    giây mô phỏng; ``labels`` = nhãn đồng hồ 'HH:MM:SS' suy từ ``sim_time``. Hai danh sách
    song song, dài bằng nhau, xếp theo thời gian tăng dần (cũ -> mới).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT sim_time, waiting FROM sim_lane_metrics"
            " WHERE run_id = ? AND lot_id = ? AND lane_id = ?"
            " ORDER BY sim_time DESC LIMIT ?",
            (run_id, lot_id, lane_id, int(window)),
        ).fetchall()
    finally:
        conn.close()
    rows = rows[::-1]                       # cũ -> mới cho biểu đồ
    return {
        "labels": [clock_label(sim_time) for sim_time, _ in rows],
        "values": [int(waiting) for _, waiting in rows],
    }


def summary_for(run_id: int) -> dict | None:
    """Tổng kết cuối lượt (đã parse), None nếu lượt chưa dừng / không tồn tại."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT summary_json FROM sim_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    return _parse_json(row[0]) if row is not None else None