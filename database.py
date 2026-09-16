import sqlite3
from datetime import datetime
from pathlib import Path

DB_NAME = Path(__file__).resolve().parent / "traffic_monitor.db"


def get_connection():
    """Create a short-lived SQLite connection for the current thread."""
    conn = sqlite3.connect(DB_NAME, timeout=30)
    # WAL + busy_timeout: worker camera và mô phỏng cùng ghi, tránh lỗi "database is locked".
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init_db():
    """Khởi tạo các bảng dữ liệu nếu chưa tồn tại"""
    conn = get_connection()
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
            total_vehicles INTEGER
        )
    ''')
    
    conn.commit()
    conn.close()


def reset_metrics():
    """Remove metrics from previous server runs so the chart starts empty."""
    conn = get_connection()
    conn.execute("DELETE FROM traffic_metrics")
    conn.commit()
    conn.close()

def log_vehicle(track_id, vehicle_type):
    """Ghi nhận 1 xe vừa đi qua vạch"""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "INSERT INTO vehicle_entries (track_id, vehicle_type, timestamp) VALUES (?, ?, ?)",
        (track_id, vehicle_type, now)
    )
    conn.commit()
    conn.close()

def log_metric(total_vehicles):
    """Ghi nhận chỉ số lưu lượng theo giây"""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "INSERT INTO traffic_metrics (timestamp, total_vehicles) VALUES (?, ?)",
        (now, total_vehicles)
    )
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Khởi tạo cơ sở dữ liệu SQLite thành công!")