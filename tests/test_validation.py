"""Test quy tắc kiểm tra hợp lệ: mỗi quy tắc 1 test, thông báo tiếng Việt + mã lỗi."""

import pytest

from simulation import config as C
from simulation.models import IN, OUT, LotConfig, RunParams
from simulation.validation import ValidationError, normalise_lot_id, validate_run_params
from tests.conftest import build_lane, build_params

# λ hợp lệ (>= LAMBDA_MIN) để không vi phạm quy tắc λ trước các quy tắc khác
LAM = 0.15


def valid_params(lot_ids=("A", "BC", "D", "KTX"), **overrides):
    """RunParams toàn bộ làn mở, λ hợp lệ, allow_lopsided=False."""
    lots = []
    for lot_id in lot_ids:
        lanes = tuple(
            build_lane(lane, C.DEFAULT_DIRECTIONS[lane], lam_in=LAM, lam_out=LAM)
            for lane in C.LANES_PER_LOT
        )
        lots.append(LotConfig(lot_id=lot_id, lanes=lanes))
    data = dict(
        name="valid", scenario_id="valid", lots=tuple(lots),
        processing_time_s=C.PROCESSING_TIME_S, seed=7,
        allow_lopsided=False, max_sim_seconds=None,
    )
    data.update(overrides)
    return RunParams(**data)


def lanes_with(open_ids=("L1", "L2", "L3", "L4")):
    """4 làn với λ hợp lệ ở cả 2 hướng; chỉ mở những làn trong `open_ids`."""
    return tuple(
        build_lane(lane, C.DEFAULT_DIRECTIONS[lane], lam_in=LAM, lam_out=LAM, open_=lane in open_ids)
        for lane in C.LANES_PER_LOT
    )


def lots_with(open_ids=("L1", "L2", "L3", "L4"), lot_ids=("A", "BC", "D", "KTX")):
    lanes = lanes_with(open_ids)
    return tuple(LotConfig(lot_id=lid, lanes=lanes) for lid in lot_ids)


def test_valid_params_do_not_raise():
    validate_run_params(valid_params())


def test_validation_error_carries_message_code_and_details():
    err = ValidationError("sai", "some_code", {"k": 1})
    assert err.message == "sai"
    assert err.code == "some_code"
    assert err.details == {"k": 1}
    assert str(err) == "sai"
    assert ValidationError("sai").code == "invalid_params"
    assert ValidationError("sai").details == {}


def test_unknown_lot_is_rejected():
    with pytest.raises(ValidationError) as err:
        validate_run_params(valid_params(lot_ids=("A", "BC", "D", "ZZZ")))
    assert err.value.code == "unknown_lot"
    assert err.value.details["lot_ids"] == ["A", "BC", "D", "ZZZ"]
    assert "4 nhà xe" in err.value.message


def test_missing_lot_is_rejected():
    with pytest.raises(ValidationError) as err:
        validate_run_params(valid_params(lot_ids=("A", "BC", "D")))
    assert err.value.code == "unknown_lot"


def test_duplicate_lane_id_is_rejected():
    lanes = (
        build_lane("L1", IN, lam_in=LAM, lam_out=LAM),
        build_lane("L1", OUT, lam_in=LAM, lam_out=LAM),
        build_lane("L3", OUT, lam_in=LAM, lam_out=LAM),
        build_lane("L4", OUT, lam_in=LAM, lam_out=LAM),
    )
    lots = tuple(LotConfig(lot_id=lid, lanes=lanes) for lid in ("A", "BC", "D", "KTX"))
    params = valid_params(lots=lots)
    with pytest.raises(ValidationError) as err:
        validate_run_params(params)
    assert err.value.code == "duplicate_lane"
    assert "L1" in err.value.message


def test_lambda_above_max_is_rejected():
    lanes = (
        build_lane("L1", IN, lam_in=5.0, lam_out=LAM),
        build_lane("L2", IN, lam_in=LAM, lam_out=LAM),
        build_lane("L3", OUT, lam_in=LAM, lam_out=LAM),
        build_lane("L4", OUT, lam_in=LAM, lam_out=LAM),
    )
    params = valid_params(lots=tuple(LotConfig(lot_id=lid, lanes=lanes) for lid in ("A", "BC", "D", "KTX")))
    with pytest.raises(ValidationError) as err:
        validate_run_params(params)
    assert err.value.code == "lambda_out_of_range"
    assert err.value.details["value"] == 5.0
    assert err.value.details["lane_id"] == "L1"
    assert "λ vào" in err.value.message


def test_lambda_below_min_is_rejected():
    lanes = (
        build_lane("L1", IN, lam_in=0.0, lam_out=LAM),
        build_lane("L2", IN, lam_in=LAM, lam_out=LAM),
        build_lane("L3", OUT, lam_in=LAM, lam_out=LAM),
        build_lane("L4", OUT, lam_in=LAM, lam_out=LAM),
    )
    params = valid_params(lots=tuple(LotConfig(lot_id=lid, lanes=lanes) for lid in ("A", "BC", "D", "KTX")))
    with pytest.raises(ValidationError) as err:
        validate_run_params(params)
    assert err.value.code == "lambda_out_of_range"
    assert err.value.details["value"] == 0.0


def test_lambda_out_of_range_uses_config_bounds():
    lanes = (
        build_lane("L1", IN, lam_in=C.LAMBDA_MIN, lam_out=LAM),
        build_lane("L2", IN, lam_in=C.LAMBDA_MAX, lam_out=LAM),
        build_lane("L3", OUT, lam_in=LAM, lam_out=LAM),
        build_lane("L4", OUT, lam_in=LAM, lam_out=LAM),
    )
    params = valid_params(lots=tuple(LotConfig(lot_id=lid, lanes=lanes) for lid in ("A", "BC", "D", "KTX")))
    validate_run_params(params)                    # ngay tại 2 biên vẫn hợp lệ


def test_lot_without_open_lane_is_rejected():
    with pytest.raises(ValidationError) as err:
        validate_run_params(valid_params(lots=lots_with(open_ids=())))
    assert err.value.code == "min_open_lanes"
    assert "ít nhất 1 làn mở" in err.value.message
    assert err.value.details == {"lot_id": "A"}


def test_lopsided_lot_requires_confirmation():
    # Chỉ mở 2 làn vào -> thiếu hướng ra
    params = valid_params(lots=lots_with(open_ids=("L1", "L2")))
    with pytest.raises(ValidationError) as err:
        validate_run_params(params)
    assert err.value.code == "lopsided_needs_confirm"
    assert err.value.details == {"lot_id": "A", "requires_confirmation": True}
    assert "làn vào" in err.value.message


def test_lopsided_lot_passes_with_explicit_confirmation():
    params = valid_params(lots=lots_with(open_ids=("L1", "L2")), allow_lopsided=True)
    validate_run_params(params)


def test_lopsided_message_says_outgoing_when_only_out_lanes_open():
    params = valid_params(lots=lots_with(open_ids=("L3", "L4")))
    with pytest.raises(ValidationError) as err:
        validate_run_params(params)
    assert err.value.code == "lopsided_needs_confirm"
    assert "làn ra" in err.value.message


@pytest.mark.parametrize(
    "raw,expected",
    [("BCD", "BC"), ("bcd", "BC"), (" bc ", "BC"), ("E", "D"), ("e", "D"),
     ("1", "A"), ("2", "BC"), ("3", "KTX"), ("4", "D"), ("A", "A"), ("ktx", "KTX"),
     ("ZZZ", "ZZZ")],
)
def test_normalise_lot_id(raw, expected):
    assert normalise_lot_id(raw) == expected


def test_normalise_lot_id_maps_into_canonical_lots():
    assert {normalise_lot_id(k) for k in C.LEGACY_LOT_ALIASES} <= set(C.LOT_CAPACITIES)


def test_default_conftest_params_are_rejected_because_lambda_is_zero():
    """λ = 0 nhỏ hơn LAMBDA_MIN nên cấu hình test mặc định không hợp lệ khi chạy thật."""
    with pytest.raises(ValidationError) as err:
        validate_run_params(build_params())
    assert err.value.code == "lambda_out_of_range"


def test_unknown_lane_id_is_rejected():
    """Làn không tồn tại (ví dụ L9) phải bị từ chối kèm mã unknown_lane."""
    lanes = lanes_with() + (build_lane("L9", IN, lam_in=LAM, lam_out=LAM),)
    lots = tuple(
        LotConfig(lot_id=lid, lanes=(lanes if lid == "A" else lanes_with()))
        for lid in ("A", "BC", "D", "KTX")
    )
    with pytest.raises(ValidationError) as err:
        validate_run_params(valid_params(lots=lots))
    assert err.value.code == "unknown_lane"
    assert "L9" in err.value.message
    assert err.value.details == {"lot_id": "A", "lane_id": "L9"}
