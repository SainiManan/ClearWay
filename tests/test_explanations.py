"""Tests for the user-facing explanation text (src/explanations.py).

These pin the wording the user actually reads. The important thing being
protected is the honesty language: "not a guarantee of safety", "an absence of
reports is not evidence of safety", and the explicit "unavailable" statements
for missing factors. A silent wording change there would matter.

Run from the repository root:

    python -m pytest
"""

import pytest

from src.explanations import (
    MISSING_EXPLANATION,
    SAFETY_DISCLAIMER,
    SIMULATED_EXPLANATION,
    STATUS_WORD,
    comparison_sentences,
    factor_sentences,
    format_distance,
    format_minutes,
    route_sentences,
    scoring_status_text,
)
from src.risk_engine import FACTOR_LABELS, FACTOR_WEIGHTS, assess_route


def make_route(route_id="route_a", duration_s=4080.0, distance_m=5070.0, **extra):
    route = {
        "route_id": route_id,
        "distance_m": distance_m,
        "duration_s": duration_s,
        "geometry": [],
        "provider": "OpenStreetMap routed-foot",
    }
    route.update(extra)
    return route


def make_assessment(indicators):
    """Build a real assessment so these tests cannot drift from risk_engine."""
    return assess_route(indicators)


def indicator(status, value, **extra):
    base = {
        "value": value,
        "status": status,
        "source": "test source",
        "observed_at": "test date",
        "detail": "test detail",
    }
    base.update(extra)
    return base


FULL_INDICATORS = {
    "lighting": indicator("simulated", 38.5),
    "hazard": indicator("simulated", 43.8),
    "pedestrian": indicator("observed", 98.1),
    "isolation": None,
}

PARTIAL_INDICATORS = {
    "lighting": indicator("simulated", 38.5),
    "hazard": None,
    "pedestrian": indicator("observed", 98.1),
    "isolation": None,
}

EMPTY_INDICATORS = {name: None for name in FACTOR_WEIGHTS}


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "seconds,expected",
    [
        (None, "unknown"),
        (0, "under a minute"),
        (30, "under a minute"),
        (60, "1 min"),
        (90, "2 min"),      # rounds, does not truncate
        (4080, "68 min"),
        (259200, "4320 min"),
    ],
)
def test_format_minutes(seconds, expected):
    assert format_minutes(seconds) == expected


@pytest.mark.parametrize(
    "metres,expected",
    [
        (None, "unknown"),
        (0, "0.00 km"),
        (5070, "5.07 km"),
        (999, "1.00 km"),   # rounds up, not truncated
        (29470, "29.47 km"),
    ],
)
def test_format_distance(metres, expected):
    assert format_distance(metres) == expected


# ---------------------------------------------------------------------------
# Factor sentences
# ---------------------------------------------------------------------------
def test_missing_factor_states_it_is_unavailable():
    assessment = make_assessment(EMPTY_INDICATORS)
    sentences = factor_sentences(assessment)

    assert len(sentences) == len(FACTOR_WEIGHTS)
    for sentence, factor in zip(sentences, FACTOR_WEIGHTS):
        assert "data unavailable" in sentence
        assert FACTOR_LABELS[factor] in sentence
        assert "Nothing was assumed" in sentence


def test_missing_factor_never_suggests_a_value():
    """Critical: a missing factor must not read as zero or as low risk."""
    sentences = factor_sentences(make_assessment(EMPTY_INDICATORS))
    joined = " ".join(sentences).lower()

    for word in ("0/100", "zero risk", "no hazards", "well lit", "safe"):
        assert word not in joined


def test_present_factor_includes_value_status_and_detail():
    sentences = factor_sentences(make_assessment(FULL_INDICATORS))

    lighting = sentences[0]
    assert "Street lighting" in lighting
    assert "38/100" in lighting
    assert "simulated sample" in lighting
    assert "test detail" in lighting


def test_real_factor_is_labelled_as_real():
    sentences = factor_sentences(make_assessment(FULL_INDICATORS))
    pedestrian = next(s for s in sentences if "Pedestrian infrastructure" in s)

    assert "real OpenStreetMap" in pedestrian
    assert "98/100" in pedestrian


def test_status_word_mapping_covers_every_status():
    assert set(STATUS_WORD) >= {"observed", "simulated", "estimated", "missing"}
    assert STATUS_WORD["observed"] != STATUS_WORD["simulated"]
    assert "simulated" in STATUS_WORD["simulated"]


def test_factor_sentence_falls_back_when_detail_absent():
    indicators = {"lighting": {"value": 20.0, "status": "simulated"}, "hazard": None}
    sentences = factor_sentences(make_assessment(indicators))

    assert "No further detail available" in sentences[0]


def test_partial_indicators_produce_both_kinds_of_sentence():
    sentences = factor_sentences(make_assessment(PARTIAL_INDICATORS))

    assert any("data unavailable" in s for s in sentences)
    assert any("38/100" in s for s in sentences)


def test_equivalent_inputs_produce_identical_sentences():
    """Explanations must be stable, not order- or instance-dependent."""
    first = factor_sentences(make_assessment(FULL_INDICATORS))
    second = factor_sentences(make_assessment(FULL_INDICATORS))
    assert first == second


# ---------------------------------------------------------------------------
# Route sentences
# ---------------------------------------------------------------------------
def test_route_sentence_reports_real_numbers_and_provider():
    sentences = route_sentences(
        make_route(duration_s=4080.0, distance_m=5070.0), make_assessment(FULL_INDICATORS)
    )

    first = sentences[0]
    assert "Route A" in first
    assert "5.07 km" in first
    assert "68 min" in first
    assert "OpenStreetMap routed-foot" in first
    assert "returned live" in first


def test_scored_route_states_the_score_is_not_a_guarantee():
    sentences = route_sentences(make_route(), make_assessment(FULL_INDICATORS))

    scored = next(s for s in sentences if "Illustrative risk score" in s)
    assert "not a guarantee of safety" in scored
    assert "model estimate" in scored


def test_scored_route_reports_coverage():
    sentences = route_sentences(make_route(), make_assessment(FULL_INDICATORS))
    coverage = next(s for s in sentences if "Data coverage" in s)

    assert "90%" in coverage
    assert "partial" in coverage.lower()


def test_fully_covered_route_is_not_called_provisional():
    indicators = {name: indicator("simulated", 20.0) for name in FACTOR_WEIGHTS}
    sentences = route_sentences(make_route(), make_assessment(indicators))

    coverage = next(s for s in sentences if "Data coverage" in s)
    assert "100%" in coverage
    assert "provisional" not in coverage


def test_unscored_route_explains_why_no_score_exists():
    sentences = route_sentences(make_route(), make_assessment(EMPTY_INDICATORS))

    withheld = next(s for s in sentences if "No risk score was calculated" in s)
    assert "No indicator data is available" in withheld
    assert "no score was calculated" in withheld.lower()


def test_unscored_route_never_states_a_score():
    sentences = route_sentences(make_route(), make_assessment(EMPTY_INDICATORS))
    assert not any("Illustrative risk score" in s for s in sentences)


def test_provider_note_is_surfaced_to_the_user():
    route = make_route(provider_note="Only one walking route was returned.")
    sentences = route_sentences(route, make_assessment(FULL_INDICATORS))

    assert "Only one walking route was returned." in sentences


def test_route_letter_is_extracted_from_route_id():
    for route_id, expected in [
        ("route_a", "A"),
        ("route_b", "B"),
        ("route_c", "C"),
        ("A", "A"),
    ]:
        sentences = route_sentences(
            make_route(route_id=route_id), make_assessment(FULL_INDICATORS)
        )
        assert f"Route {expected}" in sentences[0]


def test_every_factor_appears_in_the_route_sentences():
    sentences = route_sentences(make_route(), make_assessment(FULL_INDICATORS))
    joined = " ".join(sentences)

    for label in FACTOR_LABELS.values():
        assert label in joined


# ---------------------------------------------------------------------------
# Comparison sentences
# ---------------------------------------------------------------------------
def test_single_route_has_nothing_to_compare():
    sentences = comparison_sentences([make_route()], [make_assessment(FULL_INDICATORS)])
    assert len(sentences) == 1
    assert "nothing to compare" in sentences[0]


def test_fewer_than_two_scored_routes_says_so():
    routes = [make_route("route_a"), make_route("route_b")]
    assessments = [make_assessment(FULL_INDICATORS), make_assessment(EMPTY_INDICATORS)]
    sentences = comparison_sentences(routes, assessments)

    assert any("Fewer than two routes received a score" in s for s in sentences)
    assert any("Distance and duration" in s for s in sentences)


def test_fastest_route_also_has_lowest_score():
    route_a = make_route("route_a", duration_s=3600.0)
    route_b = make_route("route_b", duration_s=4800.0)
    assessments = [make_assessment(FULL_INDICATORS) for _ in range(2)]
    assessments[1] = make_assessment(
        {
            "lighting": indicator("simulated", 90.0),
            "hazard": indicator("simulated", 90.0),
            "pedestrian": indicator("observed", 90.0),
            "isolation": None,
        }
    )
    sentences = comparison_sentences([route_a, route_b], assessments)

    assert any("both the quickest" in s for s in sentences)
    assert not any("takes about" in s for s in sentences)


def test_trade_off_is_stated_with_correct_numbers():
    # Route A is quickest but scores worse; route B is slower but scores better.
    route_a = make_route("route_a", duration_s=3600.0)
    route_b = make_route("route_b", duration_s=4800.0)
    assessment_a = make_assessment(
        {
            "lighting": indicator("simulated", 80.0),
            "hazard": indicator("simulated", 80.0),
            "pedestrian": indicator("observed", 80.0),
            "isolation": None,
        }
    )
    assessment_b = make_assessment(
        {
            "lighting": indicator("simulated", 20.0),
            "hazard": indicator("simulated", 20.0),
            "pedestrian": indicator("observed", 20.0),
            "isolation": None,
        }
    )
    sentences = comparison_sentences([route_a, route_b], [assessment_a, assessment_b])

    quickest = next(s for s in sentences if "Quickest" in s)
    slower = next(s for s in sentences if "takes about" in s)

    assert "Route A" in quickest
    assert "80/100" in quickest
    assert "Route B" in slower
    # 4800 - 3600 = 1200 s = 20 minutes
    assert "20 min longer" in slower
    assert "60 points lower" in slower


def test_comparison_never_implies_a_route_is_safe():
    routes = [make_route("route_a", duration_s=3600.0), make_route("route_b", duration_s=4800.0)]
    assessments = [make_assessment(FULL_INDICATORS), make_assessment(EMPTY_INDICATORS)]
    sentences = comparison_sentences(routes, assessments)
    joined = " ".join(sentences).lower()

    assert "a lower score does not mean a route is safe" in joined
    assert "guarantee" not in joined.replace("not a guarantee", "")


def test_unscored_route_is_excluded_from_the_comparison():
    routes = [
        make_route("route_a", duration_s=3600.0),
        make_route("route_b", duration_s=4800.0),
        make_route("route_c", duration_s=6000.0),
    ]
    scored = make_assessment(FULL_INDICATORS)
    sentences = comparison_sentences(
        routes, [scored, make_assessment(EMPTY_INDICATORS), scored]
    )

    assert any("No score was calculated for route B" in s for s in sentences)


def test_disclaimer_survives_the_early_return_paths():
    """The safety caveat must appear even when no comparison is possible."""
    single = comparison_sentences([make_route()], [make_assessment(FULL_INDICATORS)])
    assert "nothing to compare" in single[0]

    few_scored = comparison_sentences(
        [make_route("route_a"), make_route("route_b")],
        [make_assessment(FULL_INDICATORS), make_assessment(EMPTY_INDICATORS)],
    )
    assert SAFETY_DISCLAIMER in few_scored


def test_comparison_mentions_both_real_and_simulated_data():
    routes = [make_route("route_a", duration_s=3600.0), make_route("route_b", duration_s=4800.0)]
    assessments = [make_assessment(FULL_INDICATORS), make_assessment(FULL_INDICATORS)]
    sentences = comparison_sentences(routes, assessments)
    joined = " ".join(sentences)

    assert "real" in joined
    assert "simulated" in joined
    assert "coverage is partial" in joined


def test_comparison_handles_route_ids_without_underscores():
    routes = [make_route("a", duration_s=3600.0), make_route("b", duration_s=4800.0)]
    assessments = [make_assessment(FULL_INDICATORS), make_assessment(FULL_INDICATORS)]
    sentences = comparison_sentences(routes, assessments)

    assert any("Route A" in s for s in sentences)


# ---------------------------------------------------------------------------
# Template constants
# ---------------------------------------------------------------------------
def test_missing_template_has_no_placeholders_left_to_fill():
    formatted = MISSING_EXPLANATION.format(label="Street lighting")
    assert "{" not in formatted
    assert "}" not in formatted
    assert "Street lighting" in formatted


def test_simulated_template_fills_every_placeholder():
    formatted = SIMULATED_EXPLANATION.format(
        label="Reported hazards", value="42", status_word="simulated sample",
        detail="detail text",
    )
    assert "{" not in formatted
    assert "42/100" in formatted
    assert "detail text" in formatted


# ---------------------------------------------------------------------------
# Loading progress line
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "route_id,index,total",
    [("route_a", 1, 1), ("route_a", 1, 2), ("route_b", 2, 2), ("route_c", 3, 3)],
)
def test_scoring_status_names_route_and_position(route_id, index, total):
    text = scoring_status_text(route_id, index, total)
    assert f"({index} of {total})" in text
    assert "Assessing route" in text


def test_scoring_status_says_a_fetch_is_happening():
    """It must not imply the result is ready, nor that anything is instant."""
    text = scoring_status_text("route_a", 1, 2)
    assert "OpenStreetMap" in text
    assert "…" in text
    for forbidden in ("done", "complete", "safe", "score"):
        assert forbidden not in text.lower()


def test_scoring_status_has_no_template_braces_left():
    text = scoring_status_text("route_a", 1, 2)
    assert "{" not in text and "}" not in text
