        // Khởi tạo Lucide Icons
        lucide.createIcons();

        //Dữ liệu mô phỏng riêng cho từng nhà xe (1 đến 4)
        const garageData = {
            1: {
                flow: "47 xe", trend: "↑ Tăng 12% so với chu kỳ trước",
                queue: "12 xe", queueLen: "Chiều dài đuôi hàng ~60m",
                time: "45s",
                alert: "<b>ĐIỀU HƯỚNG:</b>",
                recommend: "ĐIỀU HƯỚNG SANG NHÀ XE SỐ 4",
                chartData: [15, 18, 22, 30, 38, 48, 55, 50, 42]
            },
            2: {
                flow: "28 xe", trend: "↓ Giảm 5% so với chu kỳ trước",
                queue: "4 xe", queueLen: "Chiều dài đuôi hàng ~15m",
                time: "25s",
                alert: "<b>THÔNG BÁO:</b> MỞ THÊM 1 CỔNG VÀO Ở NHÀ XE SỐ 2",
                recommend: "ĐANG PHÁT TRIỂN",
                chartData: [10, 15, 20, 25, 28, 30, 28, 25, 20]
            },
            3: {
                flow: "62 xe", trend: "↑ Tăng 25% so với chu kỳ trước",
                queue: "18 xe", queueLen: "Chiều dài đuôi hàng ~90m",
                time: "60s",
                alert: "<b>CẢNH BÁO UÙN TẮC:</b> Đề xuất mở thêm Cổng 4 để xả luồng.",
                recommend: "KHUYÊN DÙNG: ĐANG PHÁT TRIỂN",
                chartData: [20, 25, 35, 45, 60, 65, 62, 58, 50]
            },
            4: {
                flow: "14 xe", trend: "→ Phân luồng ổn định",
                queue: "2 xe", queueLen: "Chiều dài đuôi hàng ~5m",
                time: "18s",
                alert: "<b>TRẠNG THÁI THÔNG THOÁNG:</b> Bãi D hiện tại đáp ứng tốt lưu lượng xe di chuyển từ các khu vực khác.",
                recommend: "KHUYÊN DÙNG: ĐANG PHÁT TRIỂN",
                chartData: [5, 8, 12, 10, 14, 15, 14, 12, 10]
            }
        };

        // Hàm chuyển tab Nhà xe
        function switchGarage(id) {
            // Active Tab
            document.querySelectorAll('.tab-btn').forEach((btn, index) => {
                if (index + 1 === id) btn.classList.add('active');
                else btn.classList.remove('active');
            });

            // Cập nhật thông số
            const data = garageData[id];
            document.getElementById('val-flow').innerText = data.flow;
            document.getElementById('val-flow-trend').innerText = data.trend;
            document.getElementById('val-queue').innerText = data.queue;
            document.getElementById('val-queue-len').innerText = data.queueLen;
            document.getElementById('val-time').innerText = data.time;
            document.getElementById('alert-text').innerHTML = data.alert;
            document.getElementById('alert-recommend').innerText = data.recommend;

            // Giữ biểu đồ do dữ liệu trực tiếp từ API điều khiển.
            refreshTrafficChart();
        }

        // --- KHỞI TẠO BIỂU ĐỒ LƯU LƯỢNG (CHART.JS) ---
        const ctx = document.getElementById('trafficChart').getContext('2d');
        const visibleTimestampPoints = {
            id: 'visibleTimestampPoints',
            afterDatasetsDraw(chart) {
                const dataset = chart.getDatasetMeta(0);
                const drawingContext = chart.ctx;

                drawingContext.save();
                    drawingContext.fillStyle = '#4b6ff2';
                dataset.data.forEach((point) => {
                    const {x, y} = point.getProps(['x', 'y'], true);
                    drawingContext.beginPath();
                    drawingContext.arc(x, y, 5, 0, Math.PI * 2);
                    drawingContext.fill();
                });
                drawingContext.restore();
            }
        };
        const trafficChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['06:00', '06:15', '06:30', '06:45', '07:00', '07:15', '07:30', '07:45', '08:00'],
                datasets: [{
                    label: 'LƯU LƯỢNG THỰC TẾ',
                    data: garageData[1].chartData,
                    borderColor: '#4b6ff2',
                    borderWidth: 2,
                    tension: 0.35,
                    pointStyle: 'circle',
                    pointRadius: 4,
                    pointHoverRadius: 6,
                    pointHitRadius: 16,
                    pointBackgroundColor: '#4b6ff2',
                    pointBorderColor: '#4b6ff2',
                    pointBorderWidth: 0,
                    cubicInterpolationMode: 'default'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {mode: 'index', intersect: false},
                elements: {
                    point: {
                        radius: 6,
                        hoverRadius: 8,
                        hitRadius: 16,
                        backgroundColor: '#4b6ff2',
                        borderColor: '#4b6ff2',
                        borderWidth: 0
                    }
                },
                plugins: {
                    legend: {display: false},
                    tooltip: {
                        enabled: true,
                        displayColors: true,
                        titleColor: '#000000',
                        bodyColor: '#000000',
                        callbacks: {
                            title: (items) => items[0].label,
                            label: (item) => ` LƯU LƯỢNG THỰC TẾ: ${item.raw}`
                        }
                    }
                },
                scales: {
                    x: {grid: {display: false}, ticks: {color: '#000000', font: {size: 10}}},
                    y: {grid: {color: '#dfe5f0'}, ticks: {color: '#000000', font: {size: 10}}}
                }
            },
            plugins: [visibleTimestampPoints]
        });

        const chartRunStartedAt = Date.now();
        const chartWindowSize = 60;

        function updateTrafficWindow(labels, values) {
            const windowStart = Math.max(0, labels.length - chartWindowSize);
            trafficChart.data.labels = labels.slice(windowStart);
            trafficChart.data.datasets[0].data = values.slice(windowStart);
            trafficChart.update();
        }

        async function refreshTrafficChart() {
            // if (Date.now() - chartRunStartedAt >= chartRunDurationMs) return;

            try {
                const response = await fetch('/api/traffic-data', {cache: 'no-store'});
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const liveData = await response.json();
                // if (Date.now() - chartRunStartedAt >= chartRunDurationMs) return;
                if (liveData.labels.length) {
                    updateTrafficWindow(liveData.labels, liveData.values);
                }
            } catch (error) {
                console.warn('Không thể cập nhật dữ liệu lưu lượng:', error);
            }
        }

        refreshTrafficChart();
        const chartRefreshTimer = setInterval(() => {
            refreshTrafficChart();
        }, 2500);

        // --- CÁC BIỂU ĐỒ ĐỒNG HỒ SỨC CHỨA (GAUGE CHARTS) ---
        function createGauge(id, percent, color) {
            new Chart(document.getElementById(id).getContext('2d'), {
                type: 'doughnut',
                data: {
                    datasets: [{
                        data: [percent, 100 - percent],
                        backgroundColor: [color, '#dfe5f0'],
                        borderWidth: 0
                    }]
                },
                options: {
                    cutout: '80%',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {tooltip: {enabled: false}, legend: {display: false}}
                },
                plugins: [{
                    id: 'text',
                    beforeDraw: function (chart) {
                        var width = chart.width, height = chart.height, ctx = chart.ctx;
                        ctx.restore();
                        ctx.font = "bold 16px sans-serif";
                        ctx.textBaseline = "middle";
                        ctx.fillStyle = '#000000';
                        var text = percent + "%",
                            textX = Math.round((width - ctx.measureText(text).width) / 2),
                            textY = height / 2;
                        ctx.fillText(text, textX, textY);
                        ctx.save();
                    }
                }]
            });
        }

        // Tạo 4 biểu đồ sức chứa đồng bộ cho 4 Bãi (1, 2, 3, 4)
        createGauge('gaugeA', 92, '#ff5252'); 
        createGauge('gaugeB', 45, '#00e676'); 
        createGauge('gaugeC', 78, '#ff9100'); 
        createGauge('gaugeD', 23, '#00e676'); 