"""Thư viện kịch bản mô phỏng (preset) + dựng ``RunParams`` từ request của API.

5 kịch bản lấy đúng số liệu giờ cao điểm trong ``parking_lot_simulation.md``:

    binh_thuong    λ vào 0,15 / λ ra 0,15  (ngoài giờ cao điểm)
    cao_diem_vao   λ vào 0,50 / λ ra 0,05  (đầu tiết 1–2, gấp đôi năng lực 0,25 xe/s/làn)
    cao_diem_ra    λ vào 0,05 / λ ra 0,50  (cuối tiết 3–4)
    qua_tai_A      nền 0,10 / 0,10; riêng nhà xe A λ vào = 2,00 (mức tối đa cho phép)
    mot_lan_vao    nền 0,40 / 0,10; riêng KTX L1 vào, L2–L4 ra

``build_from_request`` nhận HAI dạng body:

    {"scenario_id": "cao_diem_vao", "seed": 7, "max_sim_seconds": 300,
     "allow_lopsided": false, "confirm_lopsided": false}

    {"name": "Tự chọn", "seed": 3, "max_sim_seconds": null, "allow_lopsided": false,
     "lots": [{"lot_id": "BCD", "lanes": [{"lane_id": "L1", "direction": "in",
                "lambda_in": 0.5, "lambda_out": 0.1, "open": true}]}]}

Thiếu nhà xe / thiếu làn thì dùng mặc định trong ``config.py`` (hướng mặc định theo
``DEFAULT_DIRECTIONS``, λ = ``LAMBDA_MIN`` vì λ = 0 không hợp lệ).
"""

from __future__ import annotations

from . import config as C
from .models import IN, OUT, LaneConfig, LotConfig, RunParams
from .validation import ValidationError, normalise_lot_id, validate_run_params

# λ thay thế cho làn/nhà xe không được khai báo trong body tự chọn (λ = 0 không hợp lệ).
FALLBACK_LAMBDA = C.LAMBDA_MIN

SCENARIOS: dict[str, dict] = {
    "binh_thuong": {
        "name": "Bình thường (ngoài giờ cao điểm)",
        "description": (
            "λ vào = 0,15 xe/giây/làn, λ ra = 0,15. Cả 4 nhà xe mở 2 làn vào – 2 làn ra, "
            "hàng chờ ổn định vì năng lực xử lý mỗi làn là 0,25 xe/giây."
        ),
        "lambda_in": 0.15,
        "lambda_out": 0.15,
    },
    "cao_diem_vao": {
        "name": "Cao điểm đầu tiết 1–2 (xe vào)",
        "description": (
            "λ vào = 0,5 xe/giây/làn (≈30 xe/phút, gấp đôi năng lực 15 xe/phút của 1 chốt), "
            "λ ra = 0,05. Hàng chờ vào tăng dần và gây tắc nghẽn làn sau khoảng 40 giây."
        ),
        "lambda_in": 0.5,
        "lambda_out": 0.05,
    },
    "cao_diem_ra": {
        "name": "Cao điểm cuối tiết 3–4 (xe ra)",
        "description": (
            "λ ra = 0,5 xe/giây/làn, λ vào = 0,05. Dùng để xem năng lực xả của các làn ra "
            "khi cả trường cùng rời bãi."
        ),
        "lambda_in": 0.05,
        "lambda_out": 0.5,
    },
    "qua_tai_A": {
        "name": "Nhà xe A quá tải (kiểm tra điều hướng)",
        "description": (
            "Nhà xe A nhận λ vào = 2,0 xe/giây/làn (mức tối đa cho phép), các nhà xe khác 0,1. "
            "Hàng chờ tại A tắc nghẽn (≥ 20 xe) chỉ sau ~10 giây; nhưng phải mất khoảng "
            "3 700 giây mô phỏng (≈ 62 phút) thì A (1100 chỗ) mới hết chỗ, vì 2 làn vào chỉ nhận "
            "được 0,5 xe/giây trong khi 2 làn ra vẫn xả 0,2 xe/giây. Sau đó xe bị điều hướng "
            "sang nhà xe còn chỗ nhất, hết 3 lần điều hướng thì bị từ chối. "
            "Muốn thấy điều hướng sớm hơn, hãy bấm 'Đóng làn' ở các làn ra (L3/L4) của A."
        ),
        "lambda_in": 0.1,
        "lambda_out": 0.1,
        "overrides": {"A": {"lambda_in": 2.0}},
    },
    "mot_lan_vao": {
        "name": "Nhà xe KTX chỉ còn 1 làn vào",
        "description": (
            "KTX: L1 vào, L2–L4 ra (đúng 1 làn vào đang mở). λ vào = 0,4 / λ ra = 0,1. "
            "Dùng để kiểm tra cảnh báo tắc nghẽn làn và đề xuất chuyển 1 làn ra thành làn vào."
        ),
        "lambda_in": 0.4,
        "lambda_out": 0.1,
        "overrides": {"KTX": {"lane_directions": {"L1": IN, "L2": OUT, "L3": OUT, "L4": OUT}}},
    },
}

SCENARIO_IDS = tuple(SCENARIOS)


# --------------------------------------------------------------------------- #
# Trợ giúp
# --------------------------------------------------------------------------- #
def _as_int(value, *, field: str, default: int | None = None) -> int | None:
    """Ép giá trị từ JSON về int, lỗi thì báo ValidationError('invalid_params')."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(
            f"Giá trị '{field}' không hợp lệ: {value!r}.",
            "invalid_params",
            {field: value},
        ) from None


def _as_float(value, *, field: str, default: float) -> float:
    """Ép giá trị λ từ JSON về float, lỗi thì báo ValidationError('invalid_params')."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError(
            f"Giá trị '{field}' không hợp lệ: {value!r}.",
            "invalid_params",
            {field: value},
        ) from None


def _scenario(scenario_id: str) -> dict:
    meta = SCENARIOS.get(str(scenario_id))
    if meta is None:
        raise ValidationError(
            f"Không tìm thấy kịch bản {scenario_id}.",
            "unknown_scenario",
            {"scenario_id": scenario_id, "available": list(SCENARIOS)},
        )
    return meta


def _build_lanes(meta: dict, lot_id: str, lot_override: dict) -> tuple[LaneConfig, ...]:
    """4 làn của 1 nhà xe theo preset + phần ghi đè (λ và/hoặc hướng của từng làn)."""
    directions = lot_override.get("lane_directions", {})
    lam_in = lot_override.get("lambda_in", meta["lambda_in"])
    lam_out = lot_override.get("lambda_out", meta["lambda_out"])
    return tuple(
        LaneConfig(
            lane_id=lane_id,
            direction=directions.get(lane_id, C.DEFAULT_DIRECTIONS[lane_id]),
            lambda_in=lam_in,
            lambda_out=lam_out,
            open=True,
        )
        for lane_id in C.LANES_PER_LOT
    )


def list_scenarios() -> list[dict]:
    """Danh sách kịch bản cho dropdown của UI: [{'id', 'name', 'description'}]."""
    return [
        {"id": scenario_id, "name": meta["name"], "description": meta["description"]}
        for scenario_id, meta in SCENARIOS.items()
    ]


def build_params(scenario_id: str, *, seed: int = 0, allow_lopsided: bool = False,
                 max_sim_seconds: int | None = None) -> RunParams:
    """Sinh ``RunParams`` (bất biến) từ preset; đã kiểm tra hợp lệ trước khi trả về."""
    meta = _scenario(scenario_id)
    overrides = meta.get("overrides", {})
    lots = tuple(
        LotConfig(lot_id=lot_id, lanes=_build_lanes(meta, lot_id, overrides.get(lot_id, {})))
        for lot_id in C.LOT_CAPACITIES
    )
    params = RunParams(
        name=meta["name"],
        scenario_id=str(scenario_id),
        lots=lots,
        processing_time_s=C.PROCESSING_TIME_S,
        seed=int(seed or 0),
        allow_lopsided=bool(allow_lopsided),
        max_sim_seconds=max_sim_seconds,
    )
    validate_run_params(params)
    return params


def _build_custom_lanes(lot_id: str, entry: dict) -> tuple[LaneConfig, ...]:
    """Làn của 1 nhà xe trong body tự chọn; làn thiếu thì lấy mặc định của config.py."""
    raw_lanes = entry.get("lanes") or []
    if not isinstance(raw_lanes, list):
        raise ValidationError(
            f"'lanes' của nhà xe {lot_id} phải là danh sách.",
            "invalid_params",
            {"lot_id": lot_id, "lanes": raw_lanes},
        )
    provided: dict[str, dict] = {}
    for raw in raw_lanes:
        if not isinstance(raw, dict):
            raise ValidationError(
                f"Mỗi làn của nhà xe {lot_id} phải là một đối tượng JSON.",
                "invalid_params",
                {"lot_id": lot_id, "lane": raw},
            )
        lane_id = str(raw.get("lane_id", "")).strip().upper()
        if lane_id not in C.LANES_PER_LOT:
            raise ValidationError(
                f"Nhà xe {lot_id} không có làn {raw.get('lane_id')} "
                f"(chỉ có {', '.join(C.LANES_PER_LOT)}).",
                "unknown_lane",
                {"lot_id": lot_id, "lane_id": raw.get("lane_id")},
            )
        provided[lane_id] = raw

    lanes = []
    for lane_id in C.LANES_PER_LOT:
        raw = provided.get(lane_id, {})
        direction = str(raw.get("direction", C.DEFAULT_DIRECTIONS[lane_id])).strip().lower()
        if direction not in (IN, OUT):
            raise ValidationError(
                f"Hướng của làn {lot_id}/{lane_id} phải là 'in' hoặc 'out'.",
                "bad_direction",
                {"lot_id": lot_id, "lane_id": lane_id, "direction": raw.get("direction")},
            )
        lanes.append(
            LaneConfig(
                lane_id=lane_id,
                direction=direction,
                lambda_in=_as_float(raw.get("lambda_in"), field="lambda_in", default=FALLBACK_LAMBDA),
                lambda_out=_as_float(raw.get("lambda_out"), field="lambda_out", default=FALLBACK_LAMBDA),
                open=bool(raw.get("open", True)),
            )
        )
    return tuple(lanes)


def _build_custom(body: dict, *, allow_lopsided: bool, seed: int,
                  max_sim_seconds: int | None) -> RunParams:
    """Dựng ``RunParams`` từ body tự chọn (đủ 4 nhà xe, tên nhà xe được chuẩn hoá)."""
    entries = body.get("lots")
    if not isinstance(entries, list) or not entries:
        raise ValidationError(
            "Thiếu thông tin: cần 'scenario_id' hoặc danh sách 'lots'.",
            "invalid_params",
            {"body": body},
        )
    by_id: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("lot_id"):
            raise ValidationError(
                "Mỗi nhà xe trong 'lots' phải có 'lot_id'.",
                "invalid_params",
                {"lot": entry},
            )
        lot_id = normalise_lot_id(entry["lot_id"])
        if lot_id not in C.LOT_CAPACITIES:
            raise ValidationError(
                f"Không tìm thấy nhà xe {entry['lot_id']} "
                f"(chỉ nhận {'/'.join(C.LOT_CAPACITIES)} và các tên tương đương).",
                "unknown_lot",
                {"lot_id": entry["lot_id"], "normalised": lot_id},
            )
        by_id[lot_id] = entry

    lots = tuple(
        LotConfig(lot_id=lot_id, lanes=_build_custom_lanes(lot_id, by_id.get(lot_id, {})))
        for lot_id in C.LOT_CAPACITIES
    )
    params = RunParams(
        name=str(body.get("name") or "Cấu hình tự chọn"),
        scenario_id=str(body.get("scenario_id") or "custom"),
        lots=lots,
        processing_time_s=C.PROCESSING_TIME_S,
        seed=seed,
        allow_lopsided=allow_lopsided,
        max_sim_seconds=max_sim_seconds,
    )
    validate_run_params(params)
    return params


def build_from_request(body: dict) -> RunParams:
    """Dựng ``RunParams`` từ body của ``POST /api/sim/runs`` (preset hoặc tự chọn).

    ``confirm_lopsided: true`` (UI xác nhận trong hộp thoại) tương đương ``allow_lopsided``.
    """
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValidationError(
            "Dữ liệu yêu cầu phải là một đối tượng JSON.",
            "invalid_params",
            {"body": body},
        )
    seed = _as_int(body.get("seed"), field="seed", default=0) or 0
    max_sim_seconds = _as_int(body.get("max_sim_seconds"), field="max_sim_seconds", default=None)
    allow_lopsided = bool(body.get("allow_lopsided")) or bool(body.get("confirm_lopsided"))

    scenario_id = body.get("scenario_id")
    if scenario_id:
        params = build_params(
            scenario_id,
            seed=seed,
            allow_lopsided=allow_lopsided,
            max_sim_seconds=max_sim_seconds,
        )
        name = body.get("name")
        if name:
            params = RunParams(
                name=str(name),
                scenario_id=params.scenario_id,
                lots=params.lots,
                processing_time_s=params.processing_time_s,
                seed=params.seed,
                allow_lopsided=params.allow_lopsided,
                max_sim_seconds=params.max_sim_seconds,
            )
        return params
    return _build_custom(body, allow_lopsided=allow_lopsided, seed=seed,
                         max_sim_seconds=max_sim_seconds)
