"""Illustrative risk scoring for ClearWay.

The score produced here is a transparent weighted model estimate built from
whatever route indicators are actually available. It is not a probability of
harm and it is never evidence that a route is safe.

Every factor must be supplied as either:
  * a number from 0 to 100 (higher = more estimated risk),
  * None (data unavailable), or
  * a dict such as {"value": 42, "status": "observed", "source": "..."}
    whose "value" key holds the number.

A missing factor is never treated as zero risk. If too little of the model
weight is backed by real data, no score is calculated at all.
"""

FACTOR_WEIGHTS = {
    "lighting": 0.35,
    "pedestrian": 0.25,
    "hazard": 0.30,
    "isolation": 0.10,
}

FACTOR_LABELS = {
    "lighting": "Street lighting",
    "pedestrian": "Pedestrian infrastructure",
    "hazard": "Reported hazards",
    "isolation": "Route isolation",
}

# A score is only calculated when at least this share of the total model
# weight is supported by available indicators.
MIN_COVERAGE_FOR_SCORE = 0.5

SCORE_THRESHOLDS = {
    "lower": 30,
    "moderate": 60,
}


def _validated_number(factor, value):
    """Return value as a float in 0-100, or raise ValueError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{factor} risk must be a number from 0 to 100")
    number = float(value)
    if not 0 <= number <= 100:
        raise ValueError(f"{factor} risk must be a number from 0 to 100")
    return number


def calculate_route_risk(
    lighting_risk,
    pedestrian_risk,
    hazard_risk,
    isolation_risk,
):
    """Weighted score from 0 to 100. All four factors are required."""
    factors = {
        "lighting": lighting_risk,
        "pedestrian": pedestrian_risk,
        "hazard": hazard_risk,
        "isolation": isolation_risk,
    }

    for name, value in factors.items():
        factors[name] = _validated_number(name, value)

    score = sum(factors[name] * weight for name, weight in FACTOR_WEIGHTS.items())
    return round(score, 1)


def describe_risk(score):
    """Describe the model estimate, not actual route safety."""
    if score is None:
        raise ValueError("Score must be between 0 and 100")
    score = _validated_number("Score", score)

    if score < SCORE_THRESHOLDS["lower"]:
        return "Lower estimated risk"
    if score < SCORE_THRESHOLDS["moderate"]:
        return "Moderate estimated risk"
    return "Higher estimated risk"


def extract_factor_value(factor, indicator):
    """Return the numeric value of one indicator, or None if unavailable."""
    if indicator is None:
        return None

    if isinstance(indicator, dict):
        raw_value = indicator.get("value")
    else:
        raw_value = indicator

    if raw_value is None:
        return None

    return _validated_number(factor, raw_value)


def prepare_indicators(indicators):
    """Map every scoring factor to either a validated float or None."""
    if not isinstance(indicators, dict):
        raise ValueError("indicators must be a dict of factor name to value")

    prepared = {}
    for factor in FACTOR_WEIGHTS:
        prepared[factor] = extract_factor_value(factor, indicators.get(factor))
    return prepared


def available_factors(indicators):
    """Names of factors that have a usable numeric value."""
    prepared = prepare_indicators(indicators)
    return [factor for factor in FACTOR_WEIGHTS if prepared[factor] is not None]


def data_coverage(indicators):
    """Share of the total model weight backed by available data (0 to 1)."""
    prepared = prepare_indicators(indicators)
    covered = sum(FACTOR_WEIGHTS[f] for f in FACTOR_WEIGHTS if prepared[f] is not None)
    return round(covered, 4)


def coverage_label(coverage):
    """Plain-language description of how much data backed the score."""
    if coverage <= 0:
        return "No indicator data available"
    if coverage >= 1.0:
        return "All scoring factors have data"
    return "Partial data - some factors unavailable"


def assess_route(indicators):
    """Score a route using only the indicators that are actually available.

    Returns a dict containing the score (or None), data coverage, per-factor
    detail with provenance, and which factors were missing. The caller is
    expected to render every missing factor explicitly rather than hiding it.
    """
    prepared = prepare_indicators(indicators)

    present = [f for f in FACTOR_WEIGHTS if prepared[f] is not None]
    missing = [f for f in FACTOR_WEIGHTS if prepared[f] is None]
    coverage = round(sum(FACTOR_WEIGHTS[f] for f in present), 4)

    factor_details = {}
    for factor in FACTOR_WEIGHTS:
        indicator = indicators.get(factor)
        provenance = indicator if isinstance(indicator, dict) else {}
        value = prepared[factor]

        factor_details[factor] = {
            "name": factor,
            "label": FACTOR_LABELS[factor],
            "weight": FACTOR_WEIGHTS[factor],
            "value": value,
            "status": provenance.get("status", "estimated") if value is not None else "missing",
            "source": provenance.get("source"),
            "observed_at": provenance.get("observed_at"),
            "detail": provenance.get("detail"),
            "contribution": None,
        }

    score = None
    label = None
    withheld_reason = None

    if not present:
        withheld_reason = (
            "No indicator data is available for this route, so no score was "
            "calculated. A missing value is never treated as zero risk."
        )
    elif coverage < MIN_COVERAGE_FOR_SCORE:
        withheld_reason = (
            f"Only {coverage:.0%} of the scoring weight is supported by data, "
            f"which is below the {MIN_COVERAGE_FOR_SCORE:.0%} minimum, so no "
            "score was calculated."
        )
    else:
        weighted_sum = sum(prepared[f] * FACTOR_WEIGHTS[f] for f in present)
        score = round(weighted_sum / coverage, 1)
        label = describe_risk(score)
        for factor in present:
            factor_details[factor]["contribution"] = round(
                prepared[factor] * FACTOR_WEIGHTS[factor] / coverage, 1
            )

    return {
        "score": score,
        "label": label,
        "coverage": coverage,
        "coverage_label": coverage_label(coverage),
        "provisional": score is not None and coverage < 1.0,
        "factors": factor_details,
        "available_factors": present,
        "missing_factors": missing,
        "withheld_reason": withheld_reason,
    }
