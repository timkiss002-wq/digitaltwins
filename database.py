import sqlite3
from datetime import datetime

DB_NAME = "traffic_monitor.db"

def init_db():
    """Khởi tạo các bảng dữ liệu nếu chưa tồn tại"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Bảng 1: Nhật ký chi tiết từng xe đi qua vạch
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vehicle_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            vehicle_type TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Bảng 2: Thống kê tổng số xe và số xe/giây theo thời điểm
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS traffic_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            cars_per_sec INTEGER,
            total_vehicles INTEGER
        )
    ''')
    
    conn.commit()
    conn.close()

def log_vehicle(track_id, vehicle_type):
    """Ghi nhận 1 xe vừa đi qua vạch"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "INSERT INTO vehicle_entries (track_id, vehicle_type, timestamp) VALUES (?, ?, ?)",
        (track_id, vehicle_type, now)
    )
    conn.commit()
    conn.close()

def log_metric(cars_per_sec, total_vehicles):
    """Ghi nhận chỉ số lưu lượng theo giây"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "INSERT INTO traffic_metrics (timestamp, cars_per_sec, total_vehicles) VALUES (?, ?, ?)",
        (now, cars_per_sec, total_vehicles)
    )
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Khởi tạo cơ sở dữ liệu SQLite thành công!")