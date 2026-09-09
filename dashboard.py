import streamlit as st
import sqlite3
import pandas as pd
from streamlit_autorefresh import st_autorefresh

# Tự động làm mới giao diện mỗi 1000ms (1 giây)
st_autorefresh(interval=1000, key="datarefresh")

st.set_page_config(page_title="Digital Twin Traffic Dashboard", layout="wide")

st.title("🚗 Trường Học Digital Twin - Giám Sát Phân Luồng Xe")

DB_NAME = "traffic_monitor.db"

def load_data():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Tự động tạo bảng nếu chưa có
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vehicle_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            vehicle_type TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS traffic_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            cars_per_sec INTEGER,
            total_vehicles INTEGER
        )
    ''')
    conn.commit()
    
    # Đọc dữ liệu
    entries_df = pd.read_sql_query("SELECT * FROM vehicle_entries ORDER BY id DESC", conn)
    metrics_df = pd.read_sql_query("SELECT * FROM traffic_metrics ORDER BY id DESC LIMIT 60", conn)
    conn.close()
    return entries_df, metrics_df

entries_df, metrics_df = load_data()

# Các chỉ số Metric chính
col1, col2, col3 = st.columns(3)

total_vehicles = len(entries_df)
current_rate = metrics_df['cars_per_sec'].iloc[0] if not metrics_df.empty else 0
peak_rate = metrics_df['cars_per_sec'].max() if not metrics_df.empty else 0

with col1:
    st.metric(label="Tổng số xe đã vào cổng", value=total_vehicles)
with col2:
    st.metric(label="Lưu lượng hiện tại (xe/giây)", value=f"{current_rate} xe/s")
with col3:
    st.metric(label="Lưu lượng đỉnh điểm", value=f"{peak_rate} xe/s")

st.divider()

# Biểu đồ và Bảng thống kê
col_left, col_right = st.columns([2, 1])

with col_left:
    st.subheader("📈 Lưu lượng xe theo thời gian (60 giây gần nhất)")
    if not metrics_df.empty:
        chart_data = metrics_df[['timestamp', 'cars_per_sec']].sort_values(by='timestamp')
        st.line_chart(chart_data.set_index('timestamp'))
    else:
        st.info("Chưa có dữ liệu lưu lượng...")

with col_right:
    st.subheader("📊 Phân loại xe vào")
    if not entries_df.empty:
        type_counts = entries_df['vehicle_type'].value_counts()
        st.bar_chart(type_counts)
    else:
        st.info("Chưa có xe nào đi qua...")

st.divider()

st.subheader("📋 Lịch sử lượt xe vào gần đây")
st.dataframe(entries_df[['id', 'track_id', 'vehicle_type', 'timestamp']].head(10), use_container_width=True)