import cv2
import time
from ultralytics import YOLO
from database import init_db, log_vehicle, log_metric

# Khởi tạo DB
init_db()

video_path = "Vehicle Dataset Sample 2 [JqhdBCCUVyQ].mp4"
model = YOLO("yolov8n.pt")  # Sử dụng mô hình YOLOv8

cap = cv2.VideoCapture(video_path)
if not cap.isOpened():
    print(f"Không thể mở file video: {video_path}")
    exit()

window_name = "Digital Twin - Traffic Monitor"
cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
cv2.resizeWindow(window_name, 1024, 576)

# Các lớp phương tiện trong COCO Dataset (2: car, 3: motorcycle, 5: bus, 7: truck)
VEHICLE_CLASSES = {2: 'Car', 3: 'Motorcycle', 5: 'Bus', 7: 'Truck'}

# Cấu hình vạch đếm xe (Line Position - điều chỉnh y_line theo video của bạn)
y_line = 400
line_margin = 15  # Vùng đệm để tránh lặp record

# Lưu vết chuyển động của xe: {track_id: previous_y_center}
track_history = {}
counted_ids = set()

# Quản lý lưu lượng theo giây
total_entries = 0
entries_in_current_sec = 0
last_sec_timestamp = time.time()

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    # Sử dụng tracking với model.track thay vì model() thông thường
    results = model.track(frame, persist=True, classes=list(VEHICLE_CLASSES.keys()), verbose=False)

    current_time = time.time()
    
    # Kiểm tra mỗi giây để lưu metric lưu lượng
    if current_time - last_sec_timestamp >= 1.0:
        log_metric(entries_in_current_sec, total_entries)
        entries_in_current_sec = 0
        last_sec_timestamp = current_time

    # Vẽ vạch kiểm soát màu đỏ
    h, w, _ = frame.shape
    cv2.line(frame, (0, y_line), (w, y_line), (0, 0, 255), 3)
    cv2.putText(frame, "GATEWAY COUNTING LINE", (20, y_line - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    # Nếu phát hiện và theo vết được đối tượng
    if results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        track_ids = results[0].boxes.id.int().cpu().tolist()
        cls_ids = results[0].boxes.cls.int().cpu().tolist()

        for box, track_id, cls_id in zip(boxes, track_ids, cls_ids):
            x1, y1, x2, y2 = box
            cy = int((y1 + y2) / 2)  # Tọa độ tâm Y của xe
            vehicle_type = VEHICLE_CLASSES.get(cls_id, 'Vehicle')

            # Kiểm tra va chạm với vạch đếm (khi xe đi từ trên xuống dưới)
            if track_id in track_history:
                prev_y = track_history[track_id]
                
                # Nếu tâm xe vượt qua vạch y_line
                if prev_y < y_line <= cy or (abs(cy - y_line) < line_margin and track_id not in counted_ids):
                    if track_id not in counted_ids:
                        counted_ids.add(track_id)
                        total_entries += 1
                        entries_in_current_sec += 1
                        
                        # Ghi vào SQLite database
                        log_vehicle(track_id, vehicle_type)
                        print(f"[LOG] Xe ID {track_id} ({vehicle_type}) đã vào cổng!")

            track_history[track_id] = cy

            # Vẽ khung và thông tin ID
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
            cv2.putText(frame, f"ID:{track_id} {vehicle_type}", (int(x1), int(y1) - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    # Hiển thị thông số trên góc màn hình video
    cv2.putText(frame, f"Total In: {total_entries}", (30, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 3)

    cv2.imshow(window_name, frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()