"""Compatibility entry point for the Flask dashboard."""

import os
import threading

from dashboard import app, init_db, reset_metrics, run_vision_worker


if __name__ == "__main__":
    init_db()
    reset_metrics()
    threading.Thread(target=run_vision_worker, daemon=True, name="yolo-worker").start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
