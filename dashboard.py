import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
from flask import Flask, jsonify, render_template
from ultralytics import YOLO

from database import get_connection, init_db, log_metric, log_vehicle, reset_metrics

BASE_DIR = Path(__file__).resolve().parent
VIDEO_PATH = Path(os.getenv("TRAFFIC_VIDEO", BASE_DIR / "Vehicle Dataset Sample 2 [JqhdBCCUVyQ].mp4"))
MODEL_PATH = Path(os.getenv("YOLO_MODEL", BASE_DIR / "yolov8n.pt"))
SHOW_VIDEO = os.getenv("SHOW_VIDEO", "0") == "1"
VEHICLE_CLASSES = {2: "Car", 3: "Motorcycle", 5: "Bus", 7: "Truck"}
COUNTING_LINE_Y = 400
COUNTING_LINE_MARGIN = 15

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))


@app.get("/")
def dashboard():
    return render_template("web.html")


@app.get("/api/traffic-data")
def traffic_data():
    """Return every saved one-second throughput endpoint from the last two hours."""
    now = datetime.now()
    since = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    connection = get_connection()
    rows = connection.execute(
        """
        SELECT timestamp, total_vehicles
        FROM traffic_metrics
        WHERE timestamp >= ?
        GROUP BY timestamp
        ORDER BY timestamp
        """,
        (since,),
    ).fetchall()
    connection.close()

    if rows:
        labels = [timestamp[11:] for timestamp, _ in rows]
        values = [cars or 0 for _, cars in rows]
    else:
        labels = []
        values = []

    return jsonify({"labels": labels, "values": values})


def run_vision_worker():
    """Process the configured video twice and write one metric row per second."""
    init_db()
    model = YOLO(str(MODEL_PATH))
    total_entries = 0

    for _ in range(1):
        capture = cv2.VideoCapture(str(VIDEO_PATH))
        if not capture.isOpened():
            print(f"Không thể mở file video: {VIDEO_PATH}")
            return

        track_history = {}
        counted_ids = set()
        last_metric_time = time.time()

        while capture.isOpened():
            ok, frame = capture.read()
            if not ok:
                break

            results = model.track(
                frame,
                persist=True,
                classes=list(VEHICLE_CLASSES),
                verbose=False,
            )

            if results[0].boxes is not None and results[0].boxes.id is not None:
                boxes = results[0].boxes.xyxy.cpu().numpy()
                track_ids = results[0].boxes.id.int().cpu().tolist()
                class_ids = results[0].boxes.cls.int().cpu().tolist()
                for box, track_id, class_id in zip(boxes, track_ids, class_ids):
                    center_y = int((box[1] + box[3]) / 2)
                    previous_y = track_history.get(track_id)
                    crossed_line = (
                        previous_y is not None
                        and previous_y < COUNTING_LINE_Y <= center_y
                    )
                    near_line = abs(center_y - COUNTING_LINE_Y) < COUNTING_LINE_MARGIN
                    if (crossed_line or near_line) and track_id not in counted_ids:
                        counted_ids.add(track_id)
                        total_entries += 1
                        log_vehicle(
                            track_id,
                            VEHICLE_CLASSES.get(class_id, "Vehicle"),
                        )
                        print(f"{time.time()}: car detected")
                    track_history[track_id] = center_y

            now = time.time()
            if now - last_metric_time >= 1:
                log_metric(total_entries)
                last_metric_time = now

            if SHOW_VIDEO:
                cv2.imshow("Digital Twin - Traffic Monitor", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    capture.release()
                    cv2.destroyAllWindows()
                    return

        capture.release()

# if __name__ == "__main__":
#     init_db()
#     reset_metrics()
#     threading.Thread(target=run_vision_worker, daemon=True, name="yolo-worker").start()
#     app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)