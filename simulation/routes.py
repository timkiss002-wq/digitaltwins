"""Blueprint REST của mô phỏng bãi xe: ``/api/sim/*``.

Đăng ký trong ``dashboard.py``::

    from simulation.routes import sim_bp
    app.register_blueprint(sim_bp)

Mọi lỗi trả về cùng một khuôn: ``{"error_code", "message" (tiếng Việt), "details"}``.
Mã lỗi thuộc nhóm "xung đột trạng thái" trả HTTP 409, còn lại 400.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from . import config as C
from . import scenarios, store
from .service import RunConflict, manager
from .validation import ValidationError, normalise_lot_id

sim_bp = Blueprint("sim", __name__, url_prefix="/api/sim")

# Mã lỗi thể hiện xung đột trạng thái (409) chứ không phải dữ liệu sai (400).
CONFLICT_CODES = ("min_open_lanes", "conversion_pending", "no_active_run", "run_already_active")

DEFAULT_EVENT_LIMIT = 200
DEFAULT_SERIES_WINDOW = 120


# --------------------------------------------------------------------------- #
# Xử lý lỗi
# --------------------------------------------------------------------------- #
@sim_bp.errorhandler(ValidationError)
def _validation_error(exc: ValidationError):
    status = 409 if exc.code in CONFLICT_CODES else 400
    return jsonify({"error_code": exc.code, "message": exc.message, "details": exc.details}), status


@sim_bp.errorhandler(RunConflict)
def _run_conflict(exc: RunConflict):
    return jsonify({"error_code": exc.code, "message": exc.message, "details": exc.details}), 409


# --------------------------------------------------------------------------- #
# Trợ giúp
# --------------------------------------------------------------------------- #
def _body() -> dict:
    """Body JSON của request (dict rỗng nếu không có/không parse được)."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _arg_int(name: str, default: int) -> int:
    """Đọc tham số số nguyên từ query string, sai thì ValidationError('invalid_params')."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError(
            f"Tham số '{name}' không hợp lệ: {raw!r}.",
            "invalid_params",
            {name: raw},
        ) from None


def _normalise_lane(lane_id) -> str:
    """Làn luôn viết hoa (L1..L4) để khớp engine."""
    return str(lane_id or "").strip().upper()


# --------------------------------------------------------------------------- #
# Kịch bản
# --------------------------------------------------------------------------- #
@sim_bp.get("/scenarios")
def list_scenarios():
    """Danh sách kịch bản cho dropdown của UI."""
    return jsonify({"scenarios": scenarios.list_scenarios()})


# --------------------------------------------------------------------------- #
# Vòng đời lượt chạy
# --------------------------------------------------------------------------- #
@sim_bp.post("/runs")
def start_run():
    """Bắt đầu 1 lượt mô phỏng (preset hoặc cấu hình tự chọn)."""
    body = _body()
    params = scenarios.build_from_request(body)
    # Kiểm thử đặt SIM_START_THREAD=False để không cần thread thời gian thực.
    start_thread = bool(current_app.config.get("SIM_START_THREAD", True))
    run_id = manager.start(params, scenario_id=body.get("scenario_id"), start_thread=start_thread)
    return jsonify({"run_id": run_id, "status": "running"}), 201


@sim_bp.get("/runs")
def list_runs():
    """Lịch sử các lượt mô phỏng (mới nhất trước)."""
    return jsonify({"runs": store.list_runs()})


@sim_bp.get("/runs/current")
def current_run():
    """Payload polling: 1 snapshot tự nhất quán, hoặc {'status': 'idle'}."""
    return jsonify(manager.current())


@sim_bp.get("/runs/current/events")
def current_events():
    """Nhật ký sự kiện của lượt đang chạy (rỗng nếu chưa chạy lượt nào)."""
    state = manager.current()
    if state.get("status") == "idle":
        return jsonify({"events": []})
    return jsonify({"events": manager.events(state["run_id"], _arg_int("limit", DEFAULT_EVENT_LIMIT))})


@sim_bp.get("/runs/<int:run_id>/events")
def run_events(run_id: int):
    """Nhật ký sự kiện của 1 lượt (kể cả lượt đã dừng)."""
    return jsonify({"events": manager.events(run_id, _arg_int("limit", DEFAULT_EVENT_LIMIT))})


@sim_bp.post("/runs/<int:run_id>/pause")
def pause_run(run_id: int):
    """Tạm dừng lượt chạy."""
    return jsonify(manager.pause(run_id))


@sim_bp.post("/runs/<int:run_id>/resume")
def resume_run(run_id: int):
    """Tiếp tục lượt chạy."""
    return jsonify(manager.resume(run_id))


@sim_bp.post("/runs/<int:run_id>/stop")
def stop_run(run_id: int):
    """Dừng lượt chạy và trả về bản tổng kết."""
    return jsonify(manager.stop(run_id))


@sim_bp.get("/runs/<int:run_id>/summary")
def run_summary(run_id: int):
    """Bản tổng kết đã lưu; 404 nếu lượt chưa kết thúc."""
    summary = store.summary_for(run_id)
    if summary is None:
        return (
            jsonify(
                {
                    "error_code": "summary_not_ready",
                    "message": "Lượt mô phỏng chưa kết thúc.",
                    "details": {"run_id": run_id},
                }
            ),
            404,
        )
    return jsonify(summary)


# --------------------------------------------------------------------------- #
# Điều khiển làn
# --------------------------------------------------------------------------- #
def _lane_response(run_id: int, lot_id: str, lane_id: str, action: str,
                   target: str | None = None):
    """Chuyển thao tác làn cho manager rồi trả về gate dict mới nhất."""
    gate = manager.lane_action(
        run_id, normalise_lot_id(lot_id), _normalise_lane(lane_id), action, target=target
    )
    return jsonify({"lane": gate})


@sim_bp.post("/runs/<int:run_id>/lanes/<lot_id>/<lane_id>/open")
def open_lane(run_id: int, lot_id: str, lane_id: str):
    """Mở làn."""
    return _lane_response(run_id, lot_id, lane_id, "open")


@sim_bp.post("/runs/<int:run_id>/lanes/<lot_id>/<lane_id>/close")
def close_lane(run_id: int, lot_id: str, lane_id: str):
    """Đóng làn (đóng làn mở cuối cùng của nhà xe -> 409 min_open_lanes)."""
    return _lane_response(run_id, lot_id, lane_id, "close")


@sim_bp.post("/runs/<int:run_id>/lanes/<lot_id>/<lane_id>/convert")
def convert_lane(run_id: int, lot_id: str, lane_id: str):
    """Yêu cầu chuyển hướng làn; body ``{"target": "in" | "out"}``."""
    target = _body().get("target")
    return _lane_response(run_id, lot_id, lane_id, "convert", target=target)


# --------------------------------------------------------------------------- #
# Chuỗi số liệu cho biểu đồ
# --------------------------------------------------------------------------- #
@sim_bp.get("/runs/<int:run_id>/series")
def lane_series(run_id: int):
    """Chuỗi ``{labels, values}`` của 1 làn cho Chart.js."""
    lot_id = normalise_lot_id(request.args.get("lot_id", ""))
    lane_id = _normalise_lane(request.args.get("lane_id"))
    if lot_id not in C.LOT_CAPACITIES:
        raise ValidationError(
            f"Không tìm thấy nhà xe {request.args.get('lot_id')}.",
            "unknown_lot",
            {"lot_id": request.args.get("lot_id"), "normalised": lot_id},
        )
    if lane_id not in C.LANES_PER_LOT:
        raise ValidationError(
            f"Không tìm thấy làn {lane_id} (chỉ có {', '.join(C.LANES_PER_LOT)}).",
            "unknown_lane",
            {"lane_id": request.args.get("lane_id")},
        )
    return jsonify(
        store.recent_lane_series(run_id, lot_id, lane_id, _arg_int("window", DEFAULT_SERIES_WINDOW))
    )
