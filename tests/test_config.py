"""Test hằng số miền: dung tích, τ, làn, alias tên nhà xe."""

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
