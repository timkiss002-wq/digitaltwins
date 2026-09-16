/* Mô phỏng bãi xe: polling, điều khiển làn, biểu đồ và tổng kết cuối lượt. */
(function () {
    'use strict';

    const POLL_MS = 2000;
    const GATE_LABEL = {
        open: 'ĐANG MỞ',
        closed: 'ĐÃ ĐÓNG',
        draining: 'ĐANG XẢ HÀNG CHỜ',
        pending_conversion: 'CHỜ CHUYỂN HƯỚNG'
    };
    const GATE_CLASS = {
        open: 'badge-open',
        closed: 'badge-closed',
        draining: 'badge-draining',
        pending_conversion: 'badge-pending'
    };
    const STATUS_LABEL = {
        idle: 'Chưa chạy',
        running: 'Đang chạy',
        paused: 'Tạm dừng',
        stopped: 'Đã dừng',
        interrupted: 'Bị ngắt'
    };
    const EVENT_LABEL = {
        start: 'Bắt đầu lượt mô phỏng',
        pause: 'Tạm dừng',
        resume: 'Tiếp tục',
        stop: 'Dừng lượt mô phỏng',
        open: 'Mở làn',
        close: 'Đóng làn',
        convert_request: 'Yêu cầu chuyển hướng làn',
        convert_done: 'Hoàn tất chuyển hướng làn',
        redirect: 'Điều hướng xe sang nhà xe khác',
        reject: 'Từ chối xe (hết chỗ)',
        overcrowd_start: 'Làn bị tắc nghẽn',
        overcrowd_end: 'Làn đã hết tắc nghẽn',
        near_full_start: 'Nhà xe sắp đầy',
        near_full_end: 'Nhà xe đã thoát ngưỡng đầy'
    };

    let currentRun = null;      // { run_id, status, snapshot, params }
    let lastEvents = [];
    let pollTimer = null;
    let chart = null;
    let pendingLopsided = null; // body chờ xác nhận cấu hình lệch

    /* ---------- tiện ích ---------- */

    async function api(path, options) {
        const opts = Object.assign({ cache: 'no-store' }, options || {});
        if (opts.body) {
            opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
        }
        const res = await fetch(path, opts);
        let data = {};
        try { data = await res.json(); } catch (e) { data = {}; }
        if (!res.ok) {
            const err = new Error(data.message || ('Lỗi HTTP ' + res.status));
            err.payload = data;
            err.status = res.status;
            throw err;
        }
        return data;
    }

    function toast(message, level) {
        const host = document.getElementById('toast-host');
        const el = document.createElement('div');
        el.className = 'toast' + (level === 'error' ? ' toast-error' : '');
        el.textContent = message;
        host.appendChild(el);
        setTimeout(function () { el.remove(); }, 6000);
    }

    function el(id) { return document.getElementById(id); }

    function laneNumber(laneId) { return String(laneId).replace(/[^0-9]/g, '') || laneId; }

    function directionLabel(dir) { return dir === 'in' ? 'VÀO' : 'RA'; }

    function fillColor(ratio) {
        if (ratio >= 0.9) return '#ef4148';
        if (ratio >= 0.7) return '#f3a21b';
        return '#55b967';
    }

    function closeDialog(id) { const d = el(id); if (d && d.open) d.close(); }

    document.querySelectorAll('[data-close-dialog]').forEach(function (btn) {
        btn.addEventListener('click', function () { closeDialog(btn.dataset.closeDialog); });
    });

    /* ---------- khởi tạo ---------- */

    async function loadScenarios() {
        try {
            const data = await api('/api/sim/scenarios');
            const select = el('scenario-select');
            select.innerHTML = '';
            data.scenarios.forEach(function (sc) {
                const opt = document.createElement('option');
                opt.value = sc.id;
                opt.textContent = sc.name;
                opt.title = sc.description || '';
                select.appendChild(opt);
            });
            renderScenarioHint();
        } catch (err) {
            toast('Không tải được danh sách kịch bản: ' + err.message, 'error');
        }
    }

    function renderScenarioHint() {
        const select = el('scenario-select');
        const chosen = select.options[select.selectedIndex];
        el('run-params').textContent = chosen ? (chosen.title || '') : '';
    }

    function buildLaneOptions() {
        const select = el('chart-lane-select');
        if (select.dataset.ready === '1') return;
        ['A', 'BC', 'D', 'KTX'].forEach(function (lot) {
            ['L1', 'L2', 'L3', 'L4'].forEach(function (lane) {
                const opt = document.createElement('option');
                opt.value = lot + '|' + lane;
                opt.textContent = 'Nhà xe ' + lot + ' · Làn ' + laneNumber(lane);
                select.appendChild(opt);
            });
        });
        select.dataset.ready = '1';
    }

    /* ---------- hiển thị trạng thái ---------- */

    function renderControls(state) {
        const status = state.status;
        el('run-status').textContent = STATUS_LABEL[status] || status;
        el('run-status').dataset.status = status;
        el('btn-start').disabled = status === 'running' || status === 'paused';
        el('btn-pause').disabled = status !== 'running';
        el('btn-resume').disabled = status !== 'paused';
        el('btn-stop').disabled = status !== 'running' && status !== 'paused';
        el('scenario-select').disabled = status === 'running' || status === 'paused';
    }

    function renderKpis(totals, lots) {
        el('kpi-admitted').textContent = totals.admitted;
        el('kpi-departed').textContent = totals.departed;
        el('kpi-waiting').textContent = totals.waiting;
        el('kpi-rejected').textContent = totals.rejected;
        el('kpi-redirected').textContent = totals.redirected;

        let samples = 0;
        let waitSum = 0;
        lots.forEach(function (lot) {
            lot.gates.forEach(function (gate) {
                if (gate.avg_wait_s !== null && gate.avg_wait_s !== undefined && gate.processed_total) {
                    samples += gate.processed_total;
                    waitSum += gate.avg_wait_s * gate.processed_total;
                }
            });
        });
        el('kpi-avg-wait').textContent = samples ? (waitSum / samples).toFixed(1) + 's' : '—';
    }

    function renderLots(lots) {
        const host = el('lots-grid');
        lots.forEach(function (lot) {
            let card = document.getElementById('lot-card-' + lot.lot_id);
            if (!card) {
                card = document.createElement('div');
                card.className = 'card lot-card';
                card.id = 'lot-card-' + lot.lot_id;
                card.innerHTML =
                    '<div class="lot-head">' +
                    '  <div><div class="lot-name">Nhà xe ' + lot.lot_id + '</div>' +
                    '  <div class="lot-sub">' + lot.label + '</div></div>' +
                    '  <div class="run-status" id="lot-badge-' + lot.lot_id + '"></div>' +
                    '</div>' +
                    '<div class="lot-gauge">' +
                    '  <div class="gauge-container"><canvas id="gauge-' + lot.lot_id + '"></canvas></div>' +
                    '  <div class="lot-fill" id="lot-fill-' + lot.lot_id + '"></div>' +
                    '</div>' +
                    '<div class="gate-list" id="gates-' + lot.lot_id + '"></div>';
                host.appendChild(card);
                createGauge('gauge-' + lot.lot_id);
            }

            const pct = Math.round(lot.fill_ratio * 100);
            updateGauge('gauge-' + lot.lot_id, pct, fillColor(lot.fill_ratio));
            el('lot-fill-' + lot.lot_id).innerHTML =
                '<b>' + lot.occupancy + ' / ' + lot.capacity + ' xe</b><br>' +
                'Sức chứa đã dùng: ' + pct + '%<br>' +
                'Đã vào: ' + lot.admitted + ' · Đã ra: ' + lot.departed + '<br>' +
                'Điều hướng đến: ' + lot.redirected_in + ' · Đi từ đây: ' + lot.redirected_out + '<br>' +
                'Bị từ chối: ' + lot.rejected;
            const badge = el('lot-badge-' + lot.lot_id);
            badge.textContent = lot.fill_ratio >= 0.9 ? 'SẮP ĐẦY' : 'BÌNH THƯỜNG';
            badge.dataset.status = lot.fill_ratio >= 0.9 ? 'paused' : 'running';

            renderGates(lot);
        });
    }

    function renderGates(lot) {
        const host = el('gates-' + lot.lot_id);
        lot.gates.forEach(function (gate) {
            const rowId = 'gate-' + lot.lot_id + '-' + gate.lane_id;
            let row = document.getElementById(rowId);
            if (!row) {
                row = document.createElement('div');
                row.className = 'gate-row';
                row.id = rowId;
                host.appendChild(row);
            }
            const pending = gate.gate_state === 'pending_conversion';
            row.className = 'gate-row' +
                (gate.overloaded ? ' overloaded' : '') +
                (pending ? ' pending' : '');

            const dirClass = gate.pending_direction
                ? (gate.pending_direction === 'in' ? 'badge-in' : 'badge-out')
                : (gate.direction === 'in' ? 'badge-in' : 'badge-out');
            const targetDir = gate.pending_direction || gate.direction;

            row.innerHTML =
                '<div>' +
                '  <div class="gate-title">LÀN ' + laneNumber(gate.lane_id) + ' ' +
                '     <span class="badge ' + dirClass + '">' + directionLabel(targetDir) + '</span>' +
                '     <span class="badge ' + (GATE_CLASS[gate.gate_state] || '') + '">' +
                (GATE_LABEL[gate.gate_state] || gate.gate_state) + '</span>' +
                (gate.overloaded ? ' <span class="badge badge-overload">TẮC NGHẼN</span>' : '') +
                '  </div>' +
                '  <div class="gate-meta">' +
                '     <span>Xe chờ: <b>' + gate.waiting + '</b></span>' +
                '     <span>Tốc độ đến: ' + gate.arrival_rate + ' xe/s</span>' +
                '     <span>Tốc độ xử lý: ' + gate.processing_rate + ' xe/s</span>' +
                '     <span>Chờ TB: ' + (gate.avg_wait_s === null || gate.avg_wait_s === undefined ? '—' : gate.avg_wait_s + 's') + '</span>' +
                '     <span>Đã xử lý: ' + gate.processed_total + '</span>' +
                '  </div>' +
                '</div>' +
                '<div class="gate-actions">' +
                '  <button class="btn-mini" data-action="' + (gate.open ? 'close' : 'open') + '"' +
                '          data-lot="' + lot.lot_id + '" data-lane="' + gate.lane_id + '">' +
                (gate.open ? 'Đóng làn' : 'Mở làn') + '</button>' +
                '  <button class="btn-mini" data-action="convert" data-target="' + (gate.direction === 'in' ? 'out' : 'in') + '"' +
                '          data-lot="' + lot.lot_id + '" data-lane="' + gate.lane_id + '"' +
                (pending ? ' disabled' : '') + '>' +
                (pending ? 'Đang xả hàng chờ…' : ('Chuyển thành làn ' + directionLabel(gate.direction === 'in' ? 'out' : 'in'))) +
                '  </button>' +
                '</div>';
        });
    }

    function renderWarnings(warnings) {
        const banner = el('warn-banner');
        if (!warnings || !warnings.length) {
            banner.hidden = true;
            banner.innerHTML = '';
            return;
        }
        banner.hidden = false;
        let html = '<div class="warn-title">⚠ CẢNH BÁO (' + warnings.length + ')</div>';
        warnings.forEach(function (w) {
            html += '<div>• ' + w.message + '</div>';
        });
        banner.innerHTML = html;
    }

    function renderEventLog(events) {
        const host = el('event-log');
        if (!events.length) {
            if (!lastEvents.length) return;
            host.innerHTML = '<p class="empty-note">Chưa có sự kiện.</p>';
            return;
        }
        host.innerHTML = events.map(function (ev) {
            const where = ev.lot_id ? (' · ' + ev.lot_id + (ev.lane_id ? '/' + ev.lane_id : '')) : '';
            const detail = ev.reason ? (' <span class="empty-note">(' + ev.reason + ')</span>') : '';
            return '<div class="event-item">' +
                '<div class="event-time">' + formatClock(ev.sim_time) + '</div>' +
                '<div><span class="event-type">' + (EVENT_LABEL[ev.type] || ev.type) + '</span>' +
                where + detail + '</div></div>';
        }).join('');
    }

    function formatClock(seconds) {
        const s = Math.max(0, Math.floor(seconds || 0));
        const h = String(Math.floor(s / 3600)).padStart(2, '0');
        const m = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
        const sec = String(s % 60).padStart(2, '0');
        return h + ':' + m + ':' + sec;
    }

    /* ---------- biểu đồ ---------- */

    const gauges = {};

    function createGauge(canvasId) {
        const canvas = el(canvasId);
        if (!canvas || gauges[canvasId]) return;
        const chart = new Chart(canvas.getContext('2d'), {
            type: 'doughnut',
            data: { datasets: [{ data: [0, 100], backgroundColor: ['#55b967', '#dfe5f0'], borderWidth: 0 }] },
            options: {
                cutout: '78%',
                responsive: true,
                maintainAspectRatio: false,
                plugins: { tooltip: { enabled: false }, legend: { display: false } }
            },
            plugins: [{
                id: 'gaugeText',
                afterDraw: function (c) {
                    const text = c.$percentText || '0%';
                    const width = c.width;
                    const height = c.height;
                    const ctx = c.ctx;
                    ctx.save();
                    ctx.font = 'bold 15px sans-serif';
                    ctx.textBaseline = 'middle';
                    ctx.textAlign = 'center';
                    ctx.fillStyle = '#000000';
                    ctx.fillText(text, width / 2, height / 2);
                    ctx.restore();
                }
            }]
        });
        gauges[canvasId] = chart;
    }

    function updateGauge(canvasId, percent, color) {
        const chart = gauges[canvasId];
        if (!chart) return;
        chart.$percentText = percent + '%';
        chart.data.datasets[0].data = [percent, Math.max(0, 100 - percent)];
        chart.data.datasets[0].backgroundColor = [color, '#dfe5f0'];
        chart.update('none');
    }

    /* ---------- polling ---------- */

    async function pollState() {
        try {
            const state = await api('/api/sim/runs/current');
            renderControls(state);
            if (state.status === 'idle') {
                currentRun = null;
                el('sim-clock').textContent = '⏱ 00:00:00';
                return;
            }
            currentRun = state;
            const snap = state.snapshot;
            el('sim-clock').textContent = '⏱ ' + snap.clock + (state.status === 'paused' ? ' (tạm dừng)' : '');
            renderKpis(snap.totals, snap.lots);
            renderLots(snap.lots);
            renderWarnings(snap.warnings);
            await pollEvents(state.run_id);
            await refreshSeries(state.run_id);
        } catch (err) {
            toast('Không lấy được trạng thái mô phỏng: ' + err.message, 'error');
        }
    }

    async function pollEvents(runId) {
        try {
            const data = await api('/api/sim/runs/' + runId + '/events?limit=50');
            lastEvents = data.events || [];
            renderEventLog(lastEvents);
        } catch (err) { /* giữ nhật ký cũ khi lỗi mạng */ }
    }

    async function refreshSeries(runId) {
        const select = el('chart-lane-select');
        if (!select.value) return;
        const parts = select.value.split('|');
        try {
            const data = await api('/api/sim/runs/' + runId + '/series?lot_id=' + parts[0] +
                '&lane_id=' + parts[1] + '&window=120');
            el('chart-lane-label').textContent = '— nhà xe ' + parts[0] + ' / làn ' + laneNumber(parts[1]);
            chart.data.labels = data.labels;
            chart.data.datasets[0].data = data.values;
            chart.update('none');
        } catch (err) { /* biểu đồ giữ dữ liệu cũ */ }
    }

    function initChart() {
        const canvas = el('sim-chart');
        chart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Số xe đang chờ',
                    data: [],
                    borderColor: '#4b6ff2',
                    borderWidth: 2,
                    tension: 0.3,
                    pointRadius: 0,
                    pointHoverRadius: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false }, ticks: { color: '#000000', font: { size: 10 } } },
                    y: { beginAtZero: true, grid: { color: '#dfe5f0' }, ticks: { color: '#000000', font: { size: 10 } } }
                }
            }
        });
    }

    /* ---------- hành động ---------- */

    function runPath(suffix) {
        return '/api/sim/runs/' + currentRun.run_id + suffix;
    }

    async function startRun() {
        const select = el('scenario-select');
        const body = { scenario_id: select.value };
        if (pendingLopsided) Object.assign(body, pendingLopsided);
        try {
            await api('/api/sim/runs', { method: 'POST', body: JSON.stringify(body) });
            closeDialog('lopsided-modal');
            pendingLopsided = null;
            await pollState();
            toast('Đã bắt đầu lượt mô phỏng.');
        } catch (err) {
            if (err.payload && err.payload.error_code === 'lopsided_needs_confirm') {
                pendingLopsided = { scenario_id: select.value, confirm_lopsided: true };
                el('lopsided-body').innerHTML =
                    '<p>' + err.message + '</p>' +
                    '<p class="summary-note">Cấu hình chỉ có làn vào hoặc chỉ có làn ra sẽ làm hàng chờ tăng vô hạn. ' +
                    'Chọn “Vẫn tiếp tục” nếu đây là chủ đích.</p>';
                el('lopsided-modal').showModal();
                return;
            }
            toast(err.message, 'error');
        }
    }

    async function controlRun(action) {
        if (!currentRun) return;
        try {
            if (action === 'stop') {
                const summary = await api(runPath('/stop'), { method: 'POST' });
                renderSummary(summary);
                el('summary-modal').showModal();
            } else {
                await api(runPath('/' + action), { method: 'POST' });
            }
            await pollState();
        } catch (err) {
            toast(err.message, 'error');
        }
    }

    async function laneAction(lotId, laneId, action, target) {
        if (!currentRun) return;
        const suffix = '/lanes/' + lotId + '/' + laneId + '/' + action;
        const opts = { method: 'POST' };
        if (action === 'convert') opts.body = JSON.stringify({ target: target });
        try {
            await api(runPath(suffix), opts);
            await pollState();
        } catch (err) {
            toast(err.message, 'error');
        }
    }

    function renderSummary(summary) {
        const totals = summary.totals;
        let html = '<p class="summary-note">Thời lượng: ' + formatClock(summary.sim_seconds) +
            ' · Đã tạm dừng ' + summary.paused_seconds + 's · Lý do dừng: ' + (summary.stop_reason || '—') + '</p>';

        html += '<table class="summary-table"><thead><tr>' +
            '<th>Nhà xe</th><th>Vào</th><th>Ra</th><th>Tồn cuối</th><th>Điều hướng</th>' +
            '<th>Từ chối</th><th>Sự cố tắc nghẽn</th><th>Thời gian tắc nghẽn</th>' +
            '<th>Sự cố sắp đầy</th><th>Chờ TB</th></tr></thead><tbody>';
        summary.lots.forEach(function (lot) {
            const laneOverloadEvents = lot.lanes.reduce(function (a, l) { return a + l.overload_events; }, 0);
            const laneOverloadSeconds = lot.lanes.reduce(function (a, l) { return a + l.overload_seconds; }, 0);
            html += '<tr><td>' + lot.lot_id + '</td><td>' + lot.admitted + '</td><td>' + lot.departed + '</td>' +
                '<td>' + lot.final_occupancy + '/' + lot.capacity + '</td>' +
                '<td>' + lot.redirected_in + '</td><td>' + lot.rejected + '</td>' +
                '<td>' + laneOverloadEvents + '</td>' +
                '<td>' + laneOverloadSeconds + 's</td>' +
                '<td>' + lot.near_full_events + '</td>' +
                '<td>' + (lot.avg_wait_s === null || lot.avg_wait_s === undefined ? '—' : lot.avg_wait_s + 's') + '</td></tr>';
        });
        html += '</tbody></table>';

        html += '<table class="summary-table"><thead><tr><th>Chỉ số toàn hệ thống</th><th>Giá trị</th></tr></thead><tbody>' +
            '<tr><td>Tổng xe vào</td><td>' + totals.admitted + '</td></tr>' +
            '<tr><td>Tổng xe ra</td><td>' + totals.departed + '</td></tr>' +
            '<tr><td>Tổng xe bị từ chối</td><td>' + totals.rejected + '</td></tr>' +
            '<tr><td>Tổng xe được điều hướng</td><td>' + totals.redirected + '</td></tr>' +
            '<tr><td>Số lần tắc nghẽn làn</td><td>' + totals.overload_events + '</td></tr>' +
            '<tr><td>Tổng thời gian tắc nghẽn</td><td>' + totals.overload_seconds + 's</td></tr>' +
            '<tr><td>Số lần nhà xe sắp đầy</td><td>' + totals.near_full_events + '</td></tr>' +
            '<tr><td>Tổng thời gian sắp đầy</td><td>' + totals.near_full_seconds + 's</td></tr>' +
            '</tbody></table>';

        html += '<table class="summary-table"><thead><tr><th>Nhà xe / làn</th><th>Hướng</th>' +
            '<th>Đã xử lý</th><th>Chờ TB</th><th>Tắc nghẽn</th></tr></thead><tbody>';
        summary.lots.forEach(function (lot) {
            lot.lanes.forEach(function (lane) {
                html += '<tr><td>' + lot.lot_id + ' / ' + lane.lane_id + '</td>' +
                    '<td>' + directionLabel(lane.direction) + '</td>' +
                    '<td>' + lane.processed_total + '</td>' +
                    '<td>' + (lane.avg_wait_s === null || lane.avg_wait_s === undefined ? '—' : lane.avg_wait_s + 's') + '</td>' +
                    '<td>' + lane.overload_events + ' lần / ' + lane.overload_seconds + 's</td></tr>';
            });
        });
        html += '</tbody></table>';

        el('summary-body').innerHTML = html;
    }

    /* ---------- gắn sự kiện ---------- */

    el('scenario-select').addEventListener('change', renderScenarioHint);
    el('btn-start').addEventListener('click', startRun);
    el('btn-pause').addEventListener('click', function () { controlRun('pause'); });
    el('btn-resume').addEventListener('click', function () { controlRun('resume'); });
    el('btn-stop').addEventListener('click', function () { controlRun('stop'); });
    el('btn-confirm-lopsided').addEventListener('click', function () {
        if (!pendingLopsided) pendingLopsided = { confirm_lopsided: true };
        startRun();
    });
    el('chart-lane-select').addEventListener('change', function () {
        if (currentRun) refreshSeries(currentRun.run_id);
    });
    el('lots-grid').addEventListener('click', function (event) {
        const btn = event.target.closest('button[data-action]');
        if (!btn) return;
        laneAction(btn.dataset.lot, btn.dataset.lane, btn.dataset.action, btn.dataset.target);
    });

    /* ---------- khởi động ---------- */

    if (window.lucide) window.lucide.createIcons();
    buildLaneOptions();
    initChart();
    loadScenarios();
    pollState();
    pollTimer = setInterval(pollState, POLL_MS);
})();
