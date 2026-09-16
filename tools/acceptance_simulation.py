"""Kịch bản kiểm tra chấp nhận (acceptance) cho mô phỏng bãi xe.

Chạy end-to-end qua HTTP API trên một server đang chạy, kiểm tra:
  1. bắt đầu lượt theo kịch bản, đồng hồ chạy
  2. cảnh báo tắc nghẽn làn (>= 20 xe chờ)
  3. yêu cầu chuyển hướng làn -> trạng thái "chờ chuyển hướng" -> đổi hướng sau khi xả hết
  4. nhà xe hết chỗ -> điều hướng xe sang nhà xe khác, rồi từ chối khi hết chỗ toàn hệ thống
  5. tạm dừng làm đóng băng đồng hồ, tiếp tục chạy lại
  6. dừng lượt -> bản tổng kết đầy đủ
  7. log trong SQLite: đúng 16 dòng làn / giây mô phỏng, có đủ loại sự kiện

Cách dùng (dùng dung tích thu nhỏ để thấy hết chỗ trong ~2 phút):

    SIM_CAPACITY_SCALE=0.01 ENABLE_CV=0 PORT=5055 .venv/bin/python main.py &
    .venv/bin/python tools/acceptance_simulation.py --base http://127.0.0.1:5055

Thoát mã 1 nếu có bước không đạt.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DB_PATH = APP_DIR / "traffic_monitor.db"

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'ĐẠT ' if ok else 'KHÔNG'}] {name}{(' — ' + detail) if detail else ''}")
    return bool(ok)


def call(base: str, path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def lot_of(state: dict | None, lot_id: str) -> dict:
    """Nhà xe trong snapshot; trả {} nếu chưa có (để kiểm tra báo lỗi thay vì sập)."""
    for lot in (state or {}).get("snapshot", {}).get("lots", []):
        if lot["lot_id"] == lot_id:
            return lot
    return {}


def gate_of(state: dict | None, lot_id: str, lane_id: str) -> dict:
    for gate in lot_of(state, lot_id).get("gates", []):
        if gate["lane_id"] == lane_id:
            return gate
    return {}


def totals_of(state: dict | None) -> dict:
    return (state or {}).get("snapshot", {}).get("totals", {})


def sim_time_of(state: dict | None) -> int:
    return (state or {}).get("snapshot", {}).get("sim_time", 0)


def wait_until(describe: str, predicate, timeout: float, interval: float = 1.0):
    """Chờ tới khi predicate(state) đúng; trả (state, số giây đã chờ)."""
    started = time.time()
    state: dict = {}
    while time.time() - started < timeout:
        _, state = call(BASE, "/api/sim/runs/current")
        if state.get("status") in ("running", "paused") and predicate(state):
            return state, time.time() - started
        time.sleep(interval)
    return state or {}, time.time() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5055")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    global BASE
    BASE = args.base.rstrip("/")
    print(f"Kiểm tra chấp nhận trên {BASE}\n")

    print("1) Trạng thái ban đầu và danh sách kịch bản")
    status, state = call(BASE, "/api/sim/runs/current")
    check("polling trả 200 khi chưa có lượt chạy", status == 200 and state.get("status") == "idle", str(state))
    status, scenarios = call(BASE, "/api/sim/scenarios")
    ids = [s["id"] for s in scenarios.get("scenarios", [])]
    check("có 5 kịch bản dựng sẵn", status == 200 and len(ids) == 5, ",".join(ids))
    check("tên kịch bản bằng tiếng Việt", all(s["name"] for s in scenarios["scenarios"]))

    print("\n2) Bắt đầu lượt với kịch bản qua_tai_A")
    status, started = call(BASE, "/api/sim/runs", "POST", {"scenario_id": "qua_tai_A"})
    check("POST /runs trả 201 + run_id", status == 201 and isinstance(started.get("run_id"), int), str(started))
    if status != 201:
        return 1
    run_id = started["run_id"]

    status, state = call(BASE, "/api/sim/runs/current")
    check("trạng thái là running", state.get("status") == "running", state.get("status", ""))
    check("snapshot có 4 nhà xe × 4 làn",
          len(state.get("snapshot", {}).get("lots", [])) == 4
          and all(len(l["gates"]) == 4 for l in state["snapshot"]["lots"]))
    check("tham số lượt chạy được trả về", state.get("params", {}).get("scenario_id") == "qua_tai_A")

    print("\n3) Đồng hồ mô phỏng chạy theo thời gian thực")
    time.sleep(5)
    _, state = call(BASE, "/api/sim/runs/current")
    first_time = sim_time_of(state)
    check("đồng hồ đã tiến (~5 giây)", 3 <= first_time <= 9, f"sim_time={first_time}")

    print("\n4) Cảnh báo tắc nghẽn làn (>= 20 xe chờ)")
    state, waited = wait_until("overcrowd", lambda s: any(
        g["overloaded"] for g in lot_of(s, "A").get("gates", [])), timeout=90)
    overloaded = [f"{l['lot_id']}/{g['lane_id']}={g['waiting']}"
                  for l in (state or {}).get("snapshot", {}).get("lots", [])
                  for g in l["gates"] if g["overloaded"]]
    check("nhà xe A bị tắc nghẽn làn", bool(overloaded), f"sau {waited:.0f}s: {overloaded}")
    check("cảnh báo tắc nghẽn xuất hiện trong snapshot", any(
        w["level"] == "lane" for w in (state or {}).get("snapshot", {}).get("warnings", [])),
        "; ".join(w["message"] for w in (state or {}).get("snapshot", {}).get("warnings", [])[:2]))

    print("\n5) Chuyển hướng làn: phải xả hết hàng chờ rồi mới đổi")
    status, converted = call(BASE, f"/api/sim/runs/{run_id}/lanes/A/L3/convert", "POST", {"target": "in"})
    check("API chuyển làn trả 200", status == 200, str(converted.get("lane", {}).get("gate_state", "")))
    check("làn vào trạng thái chờ chuyển hướng",
          converted.get("lane", {}).get("gate_state") == "pending_conversion",
          str(converted.get("lane", {}).get("gate_state")))
    state, waited = wait_until("convert_done", lambda s: gate_of(s, "A", "L3").get("direction") == "in",
                               timeout=120, interval=1.0)
    check("làn A/L3 đã đổi thành làn VÀO sau khi xả hết",
          gate_of(state, "A", "L3").get("direction") == "in", f"sau {waited:.0f}s")

    print("\n6) Hết chỗ -> điều hướng, rồi từ chối")
    state, waited = wait_until("redirect", lambda s: totals_of(s).get("redirected", 0) > 0, timeout=180)
    totals = totals_of(state)
    check("có xe được điều hướng sang nhà xe khác", totals.get("redirected", 0) > 0,
          f"sau {waited:.0f}s, tổng điều hướng={totals.get('redirected', 0)}")
    a_lot = lot_of(state, "A")
    check("nhà xe A đã đầy và không vượt sức chứa",
          bool(a_lot) and a_lot.get("occupancy", 0) <= a_lot.get("capacity", 0),
          f"{a_lot.get('occupancy')}/{a_lot.get('capacity')}")
    state, waited = wait_until("reject", lambda s: totals_of(s).get("rejected", 0) > 0, timeout=180)
    check("có xe bị từ chối khi toàn hệ thống hết chỗ",
          totals_of(state).get("rejected", 0) > 0,
          f"sau {waited:.0f}s, bị từ chối={totals_of(state).get('rejected', 0)}")

    print("\n7) Tạm dừng / tiếp tục")
    call(BASE, f"/api/sim/runs/{run_id}/pause", "POST")
    _, paused = call(BASE, "/api/sim/runs/current")
    check("trạng thái chuyển sang paused", paused.get("status") == "paused", paused.get("status", ""))
    frozen_time = sim_time_of(paused)
    time.sleep(3)
    _, still = call(BASE, "/api/sim/runs/current")
    check("đồng hồ và hàng chờ đóng băng khi tạm dừng",
          sim_time_of(still) == frozen_time,
          f"sim_time {frozen_time} -> {sim_time_of(still)}")
    call(BASE, f"/api/sim/runs/{run_id}/resume", "POST")
    time.sleep(3)
    _, resumed = call(BASE, "/api/sim/runs/current")
    check("tiếp tục chạy lại từ đúng trạng thái",
          resumed.get("status") == "running" and sim_time_of(resumed) >= frozen_time,
          f"sim_time={sim_time_of(resumed)}")

    print("\n8) Dừng lượt và nhận bản tổng kết")
    status, summary = call(BASE, f"/api/sim/runs/{run_id}/stop", "POST")
    required = {"stop_reason", "sim_seconds", "paused_seconds", "totals", "lots"}
    check("stop trả về bản tổng kết đầy đủ", status == 200 and required <= set(summary),
          ",".join(sorted(summary)) if isinstance(summary, dict) else str(summary))
    totals = summary.get("totals", {})
    check("tổng kết có đủ chỉ số yêu cầu",
          {"admitted", "departed", "rejected", "redirected", "overload_events",
           "overload_seconds", "near_full_events", "near_full_seconds"} <= set(totals),
          json.dumps(totals, ensure_ascii=False))
    check("tổng kết có 4 nhà xe, mỗi nhà xe 4 làn",
          len(summary["lots"]) == 4 and all(len(l["lanes"]) == 4 for l in summary["lots"]))
    check("thời gian tạm dừng được ghi nhận", summary["paused_seconds"] >= 2.0,
          f"paused_seconds={summary['paused_seconds']}")
    status, stored = call(BASE, f"/api/sim/runs/{run_id}/summary")
    check("GET summary trả lại đúng bản đã lưu", status == 200 and stored["sim_seconds"] == summary["sim_seconds"])

    print("\n9) Log trong SQLite")
    conn = sqlite3.connect(DB_PATH)
    try:
        lane_rows = conn.execute(
            "SELECT COUNT(*) FROM sim_lane_metrics WHERE run_id=?", (run_id,)).fetchone()[0]
        lot_rows = conn.execute(
            "SELECT COUNT(*) FROM sim_lot_metrics WHERE run_id=?", (run_id,)).fetchone()[0]
        types = dict(conn.execute(
            "SELECT type, COUNT(*) FROM sim_events WHERE run_id=? GROUP BY type", (run_id,)).fetchall())
        vehicles = conn.execute(
            "SELECT COUNT(*) FROM sim_vehicles WHERE run_id=?", (run_id,)).fetchone()[0]
        run_row = conn.execute(
            "SELECT status, sim_seconds, summary_json, params_json FROM sim_runs WHERE id=?",
            (run_id,)).fetchone()
        max_occupancy = conn.execute(
            "SELECT MAX(occupancy) FROM sim_lot_metrics WHERE run_id=? AND lot_id='A'", (run_id,)).fetchone()[0]
    finally:
        conn.close()

    seconds = summary["sim_seconds"]
    check("1 dòng/làn/giây (16 × số giây)", lane_rows == 16 * seconds, f"{lane_rows} dòng vs 16×{seconds}")
    check("1 dòng/nhà xe/giây (4 × số giây)", lot_rows == 4 * seconds, f"{lot_rows} dòng vs 4×{seconds}")
    check("có log người dùng thao tác và điều hướng/từ chối",
          {"start", "convert_request", "convert_done", "redirect", "reject", "pause", "resume", "stop"}
          <= set(types), json.dumps(types, ensure_ascii=False))
    check("có log tắc nghẽn làn", {"overcrowd_start"} <= set(types))
    check("có log từng xe xử lý xong (thời gian chờ)", vehicles > 0, f"{vehicles} xe")
    check("lượt chạy đã lưu trạng thái stopped + tổng kết",
          run_row[0] == "stopped" and run_row[1] == seconds and bool(run_row[2]))
    check("tham số lượt chạy được lưu trong DB", "qua_tai_A" in (run_row[3] or ""))
    check("tồn kho trong log không vượt sức chứa",
          (max_occupancy or 0) <= lot_of(state, "A").get("capacity", 1), f"max={max_occupancy}")

    print("\n10) Nhật ký sự kiện qua API")
    status, events = call(BASE, f"/api/sim/runs/{run_id}/events?limit=50")
    check("API trả về nhật ký sự kiện", status == 200 and len(events.get("events", [])) > 0,
          f"{len(events.get('events', []))} sự kiện")

    failed = [name for name, ok, _ in CHECKS if not ok]
    print(f"\n{'=' * 70}\n{len(CHECKS) - len(failed)}/{len(CHECKS)} bước đạt.")
    if failed:
        print("Không đạt: " + "; ".join(failed))
        return 1
    print("TẤT CẢ BƯỚC KIỂM TRA CHẤP NHẬN ĐỀU ĐẠT.")
    return 0


BASE = "http://127.0.0.1:5055"

if __name__ == "__main__":
    sys.exit(main())
