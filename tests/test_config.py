"""Test hằng số miền: dung tích, τ, làn, alias tên nhà xe."""

import pytest

from simulation import config as C


def test_capacities_match_spec():
    assert C.LOT_CAPACITIES == {"A": 1100, "BC": 1750, "D": 1750, "KTX": 2000}


def test_tau_matches_15_vehicles_per_minute():
    assert abs(1 / C.PROCESSING_TIME_S * 60 - 15) < 1e-9


def test_every_lot_has_four_lanes():
    assert set(C.DEFAULT_DIRECTIONS) == set(C.LANES_PER_LOT)
    assert sorted(C.DEFAULT_DIRECTIONS.values()) == ["in", "in", "out", "out"]


def test_legacy_aliases_resolve_to_canonical_ids():
    assert C.LEGACY_LOT_ALIASES["BCD"] == "BC"
    assert C.LEGACY_LOT_ALIASES["E"] == "D"
    assert set(C.LEGACY_LOT_ALIASES.values()) <= set(C.LOT_CAPACITIES)


def test_thresholds_match_description():
    assert C.OVERCROWD_THRESHOLD == 20
    assert C.NEAR_FULL_RATIO == 0.90
    assert C.TICK_S == 1.0
    assert C.LAMBDA_MIN < C.LAMBDA_MAX


def test_lot_labels_cover_all_lots():
    assert set(C.LOT_LABELS) == set(C.LOT_CAPACITIES)


def test_scaled_capacities_shrinks_but_keeps_ids_and_stays_positive():
    scaled = C.scaled_capacities(C.BASE_LOT_CAPACITIES, 0.02)
    assert scaled == {"A": 22, "BC": 35, "D": 35, "KTX": 40}
    assert all(value >= 1 for value in C.scaled_capacities(C.BASE_LOT_CAPACITIES, 0.00001).values())


def test_scaled_capacities_is_identity_when_disabled():
    assert C.scaled_capacities(C.BASE_LOT_CAPACITIES, 1) == C.BASE_LOT_CAPACITIES
    assert C.scaled_capacities(C.BASE_LOT_CAPACITIES, 0) == C.BASE_LOT_CAPACITIES


def test_default_scale_keeps_the_numbers_from_description():
    """Mặc định phải đúng số liệu Description.md; chỉ bật SIM_CAPACITY_SCALE khi demo."""
    if C.CAPACITY_SCALE != 1:
        pytest.skip("SIM_CAPACITY_SCALE đang được bật (chế độ demo)")
    assert C.LOT_CAPACITIES == C.BASE_LOT_CAPACITIES == {"A": 1100, "BC": 1750, "D": 1750, "KTX": 2000}
