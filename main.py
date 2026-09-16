"""Điểm khởi chạy ứng dụng Flask (bảng điều khiển camera + mô phỏng bãi xe)."""

import os
import threading

from dashboard import CV_AVAILABLE, app, init_db, reset_metrics, run_vision_worker
from simulation.store import init_sim_db, recover_interrupted_runs


if __name__ == "__main__":
    init_db()
    reset_metrics()

    # Bảng của mô phỏng bãi xe (tạo idempotent) + dọn các lượt bị ngắt do lần chạy trước.
    init_sim_db()
    interrupted = recover_interrupted_runs()
    if interrupted:
        print(f"Đã đánh dấu {interrupted} lượt mô phỏng bị ngắt trước đó.")

    if CV_AVAILABLE and os.getenv("ENABLE_CV", "1") == "1":
        threading.Thread(target=run_vision_worker, daemon=True, name="yolo-worker").start()
    else:
        print("Không chạy worker camera (ENABLE_CV=0 hoặc thiếu ultralytics/opencv).")

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
