"""Tests for the ClearWay risk scoring engine.

Run from the repository root:

    python -m pytest
"""

import pytest

from src.risk_engine import (
    FACTOR_WEIGHTS,
    MIN_COVERAGE_FOR_SCORE,
    assess_route,
    available_factors,
    calculate_route_risk,
    data_coverage,
    describe_risk,
    extract_factor_value,
)


# ---------------------------------------------------------------------------
# The original three required tests
# ---------------------------------------------------------------------------
def test_zero_inputs_return_zero():
    assert calculate_route_risk(0, 0, 0, 0) == 0.0


def test_maximum_inputs_return_100():
    assert calculate_route_risk(100, 100, 100, 100) == 100.0


def test_negative_input_raises_value_error():
    with pytest.raises(ValueError):
        calculate_route_risk(-1, 0, 0, 0)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_value", [101, 1000, -0.5])
def test_out_of_range_inputs_raise(bad_value):
    with pytest.raises(ValueError):
        calculate_route_risk(bad_value, 0, 0, 0)


@pytest.mark.parametrize("bad_value", ["50", None, [50], object(), True])
def test_invalid_types_raise(bad_value):
    with pytest.raises(ValueError):
        calculate_route_risk(bad_value, 0, 0, 0)


def test_weights_sum_to_one():
    assert round(sum(FACTOR_WEIGHTS.values()), 6) == 1.0


def test_calculate_route_risk_uses_documented_weights():
    # 0.35*100 + 0.25*0 + 0.30*50 + 0.10*0 = 50.0
    assert calculate_route_risk(100, 0, 50, 0) == 50.0


# ---------------------------------------------------------------------------
# Risk description thresholds
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "Lower estimated risk"),
        (29.9, "Lower estimated risk"),
        (30, "Moderate estimated risk"),
        (59.9, "Moderate estimated risk"),
        (60, "Higher estimated risk"),
        (100, "Higher estimated risk"),
    ],
)
def test_describe_risk_thresholds(score, expected):
    assert describe_risk(score) == expected


@pytest.mark.parametrize("bad_score", [-1, 101, None, "high"])
def test_describe_risk_rejects_invalid(bad_score):
    with pytest.raises(ValueError):
        describe_risk(bad_score)


# ---------------------------------------------------------------------------
# Missing values
# ---------------------------------------------------------------------------
def test_missing_value_is_not_zero_risk():
    """A missing hazard factor must not be read as 'no hazards'."""
    with_missing = assess_route({"lighting": None, "hazard": None})
    assert with_missing["score"] is None
    assert with_missing["withheld_reason"]
    assert "no score was calculated" in with_missing["withheld_reason"].lower()


def test_empty_indicators_withhold_score():
    result = assess_route({})
    assert result["score"] is None
    assert result["coverage"] == 0.0
    assert result["missing_factors"] == list(FACTOR_WEIGHTS)


def test_missing_factor_dict_with_null_value_is_missing():
    assert extract_factor_value("lighting", {"value": None}) is None
    assert extract_factor_value("lighting", {"value": 40.0}) == 40.0


def test_missing_factors_are_listed_separately():
    result = assess_route({"lighting": 30, "hazard": 70})
    assert result["available_factors"] == ["lighting", "hazard"]
    assert result["missing_factors"] == ["pedestrian", "isolation"]
    for factor in result["missing_factors"]:
        assert result["factors"][factor]["value"] is None
        assert result["factors"][factor]["status"] == "missing"
        assert result["factors"][factor]["contribution"] is None


def test_insufficient_coverage_withholds_score():
    # Lighting only = 0.35 coverage, below the 0.5 minimum.
    result = assess_route({"lighting": 20})
    assert result["score"] is None
    assert result["coverage"] == 0.35
    assert "below" in result["withheld_reason"]


def test_partial_data_produces_provisional_score():
    result = assess_route({"lighting": 20, "hazard": 40})
    assert result["score"] is not None
    assert result["provisional"] is True
    assert result["coverage"] == 0.65


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------
def test_data_coverage_is_share_of_total_weight():
    assert data_coverage({"lighting": 10, "hazard": 10}) == 0.65
    assert data_coverage({}) == 0.0
    full = {name: 10 for name in FACTOR_WEIGHTS}
    assert data_coverage(full) == 1.0


def test_coverage_ignores_missing_values():
    assert data_coverage({"lighting": 10, "hazard": None}) == 0.35


def test_partial_score_renormalises_over_available_weights():
    result = assess_route({"lighting": 100, "hazard": 0})
    # (0.35*100 + 0.30*0) / 0.65
    assert result["score"] == pytest.approx(53.8, abs=0.05)


def test_full_coverage_is_not_provisional():
    result = assess_route({name: 25.0 for name in FACTOR_WEIGHTS})
    assert result["provisional"] is False
    assert result["coverage"] == 1.0
    assert result["score"] == 25.0


def test_available_factors_ignores_provenance():
    indicators = {
        "lighting": {"value": 10, "status": "observed", "source": "a"},
        "hazard": {"value": None, "status": "missing"},
    }
    assert available_factors(indicators) == ["lighting"]


# ---------------------------------------------------------------------------
# Provenance and explanations
# ---------------------------------------------------------------------------
def test_provenance_is_preserved():
    indicators = {
        "lighting": {
            "value": 42.0,
            "status": "observed",
            "source": "Municipal survey 2026",
            "observed_at": "2026-01-04",
            "detail": "62% of sampled points unlit",
        }
    }
    result = assess_route({**indicators, "hazard": {"value": 10}})
    detail = result["factors"]["lighting"]
    assert detail["value"] == 42.0
    assert detail["status"] == "observed"
    assert detail["source"] == "Municipal survey 2026"
    assert detail["observed_at"] == "2026-01-04"
    assert "62%" in detail["detail"]


def test_contributions_are_reported_for_available_factors_only():
    result = assess_route({"lighting": 40, "hazard": 60})
    assert set(result["factors"]["lighting"]) >= {"contribution", "weight"}
    assert result["factors"]["lighting"]["contribution"] > 0
    assert result["factors"]["pedestrian"]["contribution"] is None


def test_equivalent_inputs_produce_identical_results():
    first = assess_route({"lighting": 33, "hazard": 71, "pedestrian": 5})
    second = assess_route({"lighting": 33, "hazard": 71, "pedestrian": 5})
    assert first["score"] == second["score"]
    assert first["label"] == second["label"]
    assert first["coverage"] == second["coverage"]
    assert first["missing_factors"] == second["missing_factors"]


def test_indicators_must_be_a_dict():
    with pytest.raises(ValueError):
        assess_route([1, 2, 3])


def test_minimum_coverage_constant_is_sane():
    assert 0 < MIN_COVERAGE_FOR_SCORE <= 1.0
