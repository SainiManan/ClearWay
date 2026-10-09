"""Tests for the indicator data layer (src/data.py).

These cover the honesty logic: region gating, coverage thresholds, and the rule
that a missing factor is never treated as zero risk. Nothing here touches the
network - the datasets are replaced with controlled DataFrames.

Run from the repository root:

    python -m pytest
"""

import pandas as pd
import pytest

from src import data
from src.data import (
    DEMO_REGION,
    HAZARD_DENSITY_RISK_CAP,
    HAZARD_PRESENCE_RADIUS_M,
    HAZARD_PROXIMITY_TIERS,
    LIGHTING_MIN_COVERAGE,
    LIGHTING_STATUS_RISK,
    MIN_IN_REGION_SHARE,
    SAMPLE_STEP_M,
    DataError,
    dataset_summary,
    hazard_indicator,
    lighting_indicator,
    region_contains,
    route_indicators,
)

LIGHTING_COLUMNS = [
    "segment_id",
    "latitude",
    "longitude",
    "lighting_status",
    "observation_date",
    "verification_status",
    "source",
]
HAZARD_COLUMNS = [
    "hazard_id",
    "latitude",
    "longitude",
    "category",
    "description",
    "reported_at",
    "verification_status",
    "source",
]

# A route running east for ~2 km, entirely inside the demonstration region.
INSIDE_ROUTE = [
    [26.90, 75.60],
    [26.90, 75.62],
]
# A route running north across the region's southern edge.
PARTLY_OUTSIDE_ROUTE = [
    [26.70, 75.60],
    [26.7510, 75.60],
]
# A route far outside the region.
OUTSIDE_ROUTE = [
    [25.00, 75.00],
    [25.02, 75.02],
]


@pytest.fixture(autouse=True)
def _reset_caches():
    """The loaders memoise into module globals; isolate every test from that."""
    data._lighting_cache = None
    data._hazard_cache = None
    yield
    data._lighting_cache = None
    data._hazard_cache = None


def lighting_frame(statuses, spacing=10, lat_shift=0.0):
    """Place lighting records evenly along INSIDE_ROUTE, cycling `statuses`.

    Spacing is deliberately close enough that every sampled route point has a
    record within LIGHTING_LOOKUP_RADIUS_M, so coverage is complete.
    """
    start, end = INSIDE_ROUTE
    steps = max(spacing, len(statuses))
    rows = []
    for index in range(steps):
        ratio = index / (steps - 1)
        lat = start[0] + (end[0] - start[0]) * ratio
        lon = start[1] + (end[1] - start[1]) * ratio
        rows.append(
            {
                "segment_id": f"SIM-{index}",
                "latitude": lat + lat_shift,
                "longitude": lon,
                "lighting_status": statuses[index % len(statuses)],
                "observation_date": "2026-01-01",
                "verification_status": "simulated",
                "source": "test",
            }
        )
    return pd.DataFrame(rows, columns=LIGHTING_COLUMNS)


def hazard_frame(offsets, base_lat=26.90, base_lon=75.60):
    """Place hazard records at (lat, lon) offsets from a base point."""
    rows = []
    for index, (lat_off, lon_off) in enumerate(offsets):
        rows.append(
            {
                "hazard_id": f"SIM-HZD-{index:04d}",
                "latitude": base_lat + lat_off,
                "longitude": base_lon + lon_off,
                "category": "poor_lighting",
                "description": "test",
                "reported_at": "2026-01-01",
                "verification_status": "simulated",
                "source": "test",
            }
        )
    return pd.DataFrame(rows, columns=HAZARD_COLUMNS)


def _fake_pedestrian(geometry, distance_m):
    return {
        "value": 50.0,
        "status": "observed",
        "source": "OpenStreetMap via Overpass API",
        "observed_at": "OSM snapshot test",
        "detail": "fake pedestrian data for tests",
    }


def patch_lighting(monkeypatch, frame):
    monkeypatch.setattr(data, "load_lighting", lambda: frame)


def patch_hazards(monkeypatch, frame):
    monkeypatch.setattr(data, "load_hazards", lambda: frame)


# ---------------------------------------------------------------------------
# Region gating
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "lat,lon,expected",
    [
        (26.90, 75.70, True),      # comfortably inside
        (26.75, 75.55, True),      # exact lower bounds are inclusive
        (27.02, 75.90, True),      # exact upper bounds are inclusive
        (26.74, 75.70, False),     # just below min_lat
        (26.90, 75.54, False),     # just below min_lon
        (27.03, 75.70, False),     # just above max_lat
        (26.90, 75.91, False),     # just above max_lon
        (0.0, 0.0, False),
    ],
)
def test_region_contains_boundaries(lat, lon, expected):
    assert region_contains(lat, lon) is expected


def test_demo_region_is_jaipur_city_scale():
    span_lat = DEMO_REGION["max_lat"] - DEMO_REGION["min_lat"]
    span_lon = DEMO_REGION["max_lon"] - DEMO_REGION["min_lon"]
    # Roughly 20-40 km per axis; a sanity check against a typo in the box.
    assert 0.1 < span_lat < 0.6
    assert 0.1 < span_lon < 0.6


# ---------------------------------------------------------------------------
# Geometry sampling
# ---------------------------------------------------------------------------
def test_sample_geometry_spaces_points_by_step():
    samples = data._sample_geometry(INSIDE_ROUTE)
    assert len(samples) > 5
    gaps = [
        data._haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(samples, samples[1:])
    ]
    for gap in gaps[:-1]:
        assert gap == pytest.approx(SAMPLE_STEP_M, rel=0.15)


def test_sample_geometry_preserves_endpoints():
    geometry = INSIDE_ROUTE
    samples = data._sample_geometry(geometry)
    assert samples[0] == tuple(geometry[0])
    assert samples[-1] == tuple(geometry[-1])


def test_sample_geometry_handles_degenerate_input():
    assert data._sample_geometry([]) == []
    assert data._sample_geometry(None) == []
    single = [[26.90, 75.60]]
    assert data._sample_geometry(single) == [[26.90, 75.60]]


def test_sample_geometry_handles_short_route():
    # Under one step, so only the endpoints are sampled.
    samples = data._sample_geometry([[26.90, 75.60], [26.9001, 75.60]])
    assert samples[0] == (26.90, 75.60)
    assert samples[-1] == (26.9001, 75.60)


def test_haversine_known_distance():
    # One degree of latitude is about 111.2 km.
    distance = data._haversine_m(26.0, 75.0, 27.0, 75.0)
    assert distance == pytest.approx(111195.0, rel=0.01)


def test_distance_matrix_matches_haversine():
    frame = lighting_frame(["lit"])
    # Shift every record 0.01 degrees north so the first one is a known
    # distance from the query point.
    frame["latitude"] = frame["latitude"] + 0.01
    query_lat = float(frame["latitude"].iloc[0]) - 0.01
    query_lon = float(frame["longitude"].iloc[0])

    distances = data._distance_matrix_m(query_lat, query_lon, frame)
    expected = data._haversine_m(
        query_lat, query_lon, float(frame["latitude"].iloc[0]), query_lon
    )
    assert float(distances[0]) == pytest.approx(expected, rel=1e-6)


def test_distance_matrix_is_zero_for_same_point():
    frame = lighting_frame(["lit"])
    record_lat = float(frame["latitude"].iloc[0])
    record_lon = float(frame["longitude"].iloc[0])
    distances = data._distance_matrix_m(record_lat, record_lon, frame)
    assert min(distances) < 1.0


# ---------------------------------------------------------------------------
# Lighting indicator: the value
# ---------------------------------------------------------------------------
def test_lighting_all_lit_gives_minimum_risk(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["lit"]))
    indicator = lighting_indicator(INSIDE_ROUTE)

    assert indicator["value"] == pytest.approx(LIGHTING_STATUS_RISK["lit"], abs=0.1)
    assert indicator["status"] == "simulated"
    assert indicator["percentages"]["lit"] == pytest.approx(1.0)


def test_lighting_all_unlit_gives_maximum_risk(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["unlit"]))
    indicator = lighting_indicator(INSIDE_ROUTE)

    assert indicator["value"] == pytest.approx(LIGHTING_STATUS_RISK["unlit"], abs=0.1)
    assert indicator["percentages"]["unlit"] == pytest.approx(1.0)


def test_lighting_partial_gives_middle_risk(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["partial"]))
    indicator = lighting_indicator(INSIDE_ROUTE)

    assert indicator["value"] == pytest.approx(LIGHTING_STATUS_RISK["partial"], abs=0.1)


def test_lighting_mixes_statuses_as_weighted_average(monkeypatch):
    # Two records side by side, so nearest-record alternates between them.
    frame = lighting_frame(["lit", "unlit"])
    patch_lighting(monkeypatch, frame)
    indicator = lighting_indicator(INSIDE_ROUTE)

    low, high = LIGHTING_STATUS_RISK["lit"], LIGHTING_STATUS_RISK["unlit"]
    assert low < indicator["value"] < high
    assert indicator["percentages"]["lit"] + indicator["percentages"]["unlit"] == \
        pytest.approx(1.0, abs=0.05)


def test_lighting_provenance_is_reported(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["lit"]))
    indicator = lighting_indicator(INSIDE_ROUTE)

    assert indicator["source"] == data.SIMULATED_SOURCE_LABEL
    assert "simulated sample rows" in indicator["observed_at"]
    assert "NOT inferred from the presence of a road" in indicator["detail"]
    assert "% lit" in indicator["detail"]


def test_lighting_value_is_within_bounds(monkeypatch):
    for status in LIGHTING_STATUS_RISK:
        patch_lighting(monkeypatch, lighting_frame([status]))
        indicator = lighting_indicator(INSIDE_ROUTE)
        assert 0.0 <= indicator["value"] <= 100.0


# ---------------------------------------------------------------------------
# Lighting indicator: missing data must never be read as zero risk
# ---------------------------------------------------------------------------
def test_lighting_missing_when_no_geometry():
    indicator = lighting_indicator([])
    assert indicator["value"] is None
    assert indicator["status"] == "missing"


def test_lighting_missing_when_route_outside_region(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["lit"]))
    indicator = lighting_indicator(OUTSIDE_ROUTE)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "0% of this route lies inside" in indicator["detail"]
    assert DEMO_REGION["name"] in indicator["detail"]


def test_lighting_missing_when_mostly_outside_region(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["lit"]))
    indicator = lighting_indicator(PARTLY_OUTSIDE_ROUTE)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "lies inside" in indicator["detail"]


def test_lighting_missing_when_records_too_far(monkeypatch):
    # Records shifted ~12 km north of every sampled route point.
    frame = lighting_frame(["lit"], lat_shift=0.11)
    patch_lighting(monkeypatch, frame)
    indicator = lighting_indicator(INSIDE_ROUTE)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "only covers" in indicator["detail"]
    assert f"{LIGHTING_MIN_COVERAGE:.0%}" in indicator["detail"]


def test_lighting_missing_when_dataset_empty(monkeypatch):
    patch_lighting(monkeypatch, pd.DataFrame(columns=LIGHTING_COLUMNS))
    indicator = lighting_indicator(INSIDE_ROUTE)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "empty" in indicator["detail"]


def test_lighting_missing_when_statuses_unrecognised(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["bright", "dim"]))
    indicator = lighting_indicator(INSIDE_ROUTE)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "no recognised lighting statuses" in indicator["detail"]


def test_lighting_missing_when_dataset_unreadable(monkeypatch):
    def broken():
        raise DataError("Lighting dataset not found: /nope")

    monkeypatch.setattr(data, "load_lighting", broken)
    indicator = lighting_indicator(INSIDE_ROUTE)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "not found" in indicator["detail"]


def test_lighting_ignores_rows_without_coordinates(monkeypatch):
    frame = lighting_frame(["lit"])
    frame = pd.concat(
        [frame, pd.DataFrame([{"segment_id": "X", "latitude": None, "longitude": 75.7}]),
         ],
        ignore_index=True,
    )
    # Simulate the loader's dropna behaviour.
    patch_lighting(monkeypatch, frame.dropna(subset=["latitude", "longitude"]))
    indicator = lighting_indicator(INSIDE_ROUTE)
    assert indicator["value"] is not None


def test_lighting_status_is_case_insensitive(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["LIT"]))
    indicator = lighting_indicator(INSIDE_ROUTE)
    assert indicator["value"] == pytest.approx(LIGHTING_STATUS_RISK["lit"], abs=0.1)


def test_lighting_missing_never_returns_zero(monkeypatch):
    """The core honesty rule, asserted for every missing-data path."""
    frames = {
        "empty": pd.DataFrame(columns=LIGHTING_COLUMNS),
        "bad_statuses": lighting_frame(["nope"]),
        "too_far": lighting_frame(["lit"], lat_shift=0.11),
    }
    for name, frame in frames.items():
        patch_lighting(monkeypatch, frame)
        assert lighting_indicator(INSIDE_ROUTE)["value"] is None, name

    assert lighting_indicator(OUTSIDE_ROUTE)["value"] is None

    def broken():
        raise DataError("boom")

    monkeypatch.setattr(data, "load_lighting", broken)
    assert lighting_indicator(INSIDE_ROUTE)["value"] is None


# ---------------------------------------------------------------------------
# Hazard indicator: the value
# ---------------------------------------------------------------------------
def _route_km(geometry=INSIDE_ROUTE):
    return data._haversine_m(*geometry[0], *geometry[-1]) / 1000.0


def test_hazard_nearby_report_raises_risk(monkeypatch):
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0002)]))
    indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)

    assert indicator["value"] > 0
    assert indicator["status"] == "simulated"
    assert indicator["count_within_radius"] >= 1


def test_hazard_value_combines_density_and_proximity(monkeypatch):
    # One report right on the route: nearest distance drives the proximity tier.
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0)]))
    indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)

    per_km = indicator["count_within_radius"] / _route_km()
    density = min(HAZARD_DENSITY_RISK_CAP, per_km * data.HAZARD_DENSITY_RISK_SCALE)
    assert indicator["value"] >= density


def test_hazard_density_is_capped(monkeypatch):
    # Many reports close to the route, so density saturates.
    offsets = [(0.0, 0.0002 * i) for i in range(1, 12)]
    patch_hazards(monkeypatch, hazard_frame(offsets))
    indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)

    assert indicator["value"] <= 100.0
    assert indicator["value"] >= HAZARD_DENSITY_RISK_CAP


def test_hazard_proximity_tiers_are_monotonic(monkeypatch):
    # Push the nearest report progressively further away and confirm the risk
    # does not increase as distance grows.
    values = []
    for offset in (0.0, 0.002, 0.005):
        patch_hazards(monkeypatch, hazard_frame([(offset, 0.0)]))
        indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)
        if indicator["value"] is not None:
            values.append(indicator["value"])

    assert values == sorted(values, reverse=True)


def test_hazard_provenance_is_reported(monkeypatch):
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0002)]))
    indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)

    assert indicator["source"] == data.SIMULATED_SOURCE_LABEL
    assert "simulated sample rows" in indicator["observed_at"]
    assert "per km" in indicator["detail"]
    assert "nearest_m" in indicator


def test_hazard_handles_zero_distance(monkeypatch):
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0)]))
    indicator = hazard_indicator(INSIDE_ROUTE, None)
    assert indicator["value"] is not None
    assert 0.0 <= indicator["value"] <= 100.0


# ---------------------------------------------------------------------------
# Hazard indicator: absence of reports is not evidence of safety
# ---------------------------------------------------------------------------
def test_hazard_missing_when_no_reports_nearby(monkeypatch):
    # Every record is far beyond the presence radius.
    patch_hazards(monkeypatch, hazard_frame([(0.4, 0.0)]))
    indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "NOT evidence that a route is safe" in indicator["detail"]


def test_hazard_missing_when_no_geometry():
    indicator = hazard_indicator([], 1000.0)
    assert indicator["value"] is None
    assert indicator["status"] == "missing"


def test_hazard_missing_when_outside_region(monkeypatch):
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0)]))
    indicator = hazard_indicator(OUTSIDE_ROUTE, 2000.0)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "lies inside" in indicator["detail"]


def test_hazard_missing_when_dataset_empty(monkeypatch):
    patch_hazards(monkeypatch, pd.DataFrame(columns=HAZARD_COLUMNS))
    indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "empty" in indicator["detail"]


def test_hazard_missing_when_dataset_unreadable(monkeypatch):
    def broken():
        raise DataError("Hazard dataset not found: /nope")

    monkeypatch.setattr(data, "load_hazards", broken)
    indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)

    assert indicator["value"] is None
    assert "not found" in indicator["detail"]


def test_hazard_presence_radius_boundary(monkeypatch):
    """A report just inside the presence radius counts; just outside does not."""
    metres_to_degrees = 1.0 / 111320.0

    inside = hazard_frame([((HAZARD_PRESENCE_RADIUS_M - 100.0) * metres_to_degrees, 0.01)])
    patch_hazards(monkeypatch, inside)
    assert hazard_indicator(INSIDE_ROUTE, 1600.0)["value"] is not None

    outside = hazard_frame([((HAZARD_PRESENCE_RADIUS_M + 200.0) * metres_to_degrees, 0.01)])
    patch_hazards(monkeypatch, outside)
    assert hazard_indicator(INSIDE_ROUTE, 1600.0)["value"] is None


def test_hazard_missing_never_returns_zero(monkeypatch):
    patch_hazards(monkeypatch, hazard_frame([(0.4, 0.0)]))
    assert hazard_indicator(INSIDE_ROUTE, 1600.0)["value"] is None

    patch_hazards(monkeypatch, pd.DataFrame(columns=HAZARD_COLUMNS))
    assert hazard_indicator(INSIDE_ROUTE, 1600.0)["value"] is None

    assert hazard_indicator([], 1600.0)["value"] is None


def test_hazard_count_ignores_outside_region_duplicates(monkeypatch):
    # The same report id appears twice; only unique ids are counted.
    frame = pd.concat(
        [hazard_frame([(0.0, 0.0002)]), hazard_frame([(0.0, 0.0002)])],
        ignore_index=True,
    )
    patch_hazards(monkeypatch, frame)
    indicator = hazard_indicator(INSIDE_ROUTE, 1600.0)
    assert indicator["count_within_radius"] == 1


# ---------------------------------------------------------------------------
# Bundle and summary
# ---------------------------------------------------------------------------
def test_route_indicators_returns_full_bundle(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["lit"]))
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0002)]))
    monkeypatch.setattr(data, "pedestrian_indicator", _fake_pedestrian)

    indicators, notes = route_indicators(
        {"geometry": INSIDE_ROUTE, "distance_m": 1600.0}
    )

    assert set(indicators) == {"lighting", "hazard", "pedestrian", "isolation"}
    assert indicators["isolation"] is None
    assert indicators["pedestrian"]["status"] == "observed"
    assert any("isolation" in note.lower() for note in notes)


def test_route_indicators_notes_missing_pedestrian(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["lit"]))
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0002)]))
    monkeypatch.setattr(
        data,
        "pedestrian_indicator",
        lambda g, d: {"value": None, "status": "missing", "detail": "no osm"},
    )

    _, notes = route_indicators({"geometry": INSIDE_ROUTE, "distance_m": 1600.0})
    assert any("Pedestrian infrastructure" in note for note in notes)


def test_route_indicators_handles_missing_geometry():
    indicators, notes = route_indicators({"geometry": [], "distance_m": None})

    assert indicators["lighting"]["value"] is None
    assert indicators["hazard"]["value"] is None
    assert indicators["pedestrian"] is None
    assert indicators["isolation"] is None
    assert notes == ["No route geometry was available for indicator analysis."]


def test_route_indicators_are_accepted_by_the_risk_engine(monkeypatch):
    """The produced bundle must feed assess_route without raising."""
    from src.risk_engine import assess_route

    patch_lighting(monkeypatch, lighting_frame(["lit"]))
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0002)]))
    monkeypatch.setattr(data, "pedestrian_indicator", _fake_pedestrian)

    indicators, _ = route_indicators(
        {"geometry": INSIDE_ROUTE, "distance_m": 1600.0}
    )
    assessment = assess_route(indicators)

    # lighting + hazard + pedestrian are all available -> 90% coverage.
    assert assessment["coverage"] == pytest.approx(0.90)
    assert assessment["score"] is not None
    assert assessment["missing_factors"] == ["isolation"]


def test_route_indicators_outside_region_withhold_score(monkeypatch):
    """Outside the region only two factors vanish, so no score is produced."""
    from src.risk_engine import assess_route

    patch_lighting(monkeypatch, lighting_frame(["lit"]))
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0)]))
    monkeypatch.setattr(
        data,
        "pedestrian_indicator",
        lambda g, d: {"value": None, "status": "missing", "detail": "no osm"},
    )

    indicators, _ = route_indicators(
        {"geometry": OUTSIDE_ROUTE, "distance_m": 5000.0}
    )
    assessment = assess_route(indicators)

    assert assessment["score"] is None
    assert assessment["withheld_reason"]
    assert assessment["coverage"] == 0.0


def test_dataset_summary_reports_counts_and_caveat(monkeypatch):
    patch_lighting(monkeypatch, lighting_frame(["lit"], spacing=3))
    patch_hazards(monkeypatch, hazard_frame([(0.0, 0.0)] * 2))
    monkeypatch.setattr(data, "pedestrian_indicator", _fake_pedestrian)

    summary = dataset_summary()

    assert summary["lighting_rows"] == 3
    assert summary["hazard_rows"] == 2
    assert summary["region_status"] == "simulated"
    assert "Overpass" in summary["pedestrian_source"]
    assert summary["pedestrian_caveat"]


def test_dataset_summary_survives_unreadable_datasets(monkeypatch):
    def broken():
        raise DataError("nope")

    monkeypatch.setattr(data, "load_lighting", broken)
    monkeypatch.setattr(data, "load_hazards", broken)

    summary = dataset_summary()
    assert summary["lighting_rows"] == 0
    assert summary["hazard_rows"] == 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def test_coverage_thresholds_are_sane():
    assert 0 < LIGHTING_MIN_COVERAGE <= 1.0
    assert 0 < MIN_IN_REGION_SHARE <= 1.0

    from src.risk_engine import FACTOR_WEIGHTS, MIN_COVERAGE_FOR_SCORE

    # The three factors this prototype can actually populate must clear the
    # bar; lighting alone (0.35) must not, which is why the app needs the
    # hazard and pedestrian data too.
    populated = (
        FACTOR_WEIGHTS["lighting"]
        + FACTOR_WEIGHTS["hazard"]
        + FACTOR_WEIGHTS["pedestrian"]
    )
    assert populated >= MIN_COVERAGE_FOR_SCORE
    assert FACTOR_WEIGHTS["lighting"] < MIN_COVERAGE_FOR_SCORE


def test_status_risks_are_ordered():
    risks = LIGHTING_STATUS_RISK
    assert risks["lit"] < risks["partial"] < risks["unlit"]
    assert set(risks) == {"lit", "partial", "unlit"}


def test_proximity_tiers_are_ordered_and_bounded():
    distances = [threshold for threshold, _ in HAZARD_PROXIMITY_TIERS]
    scores = [score for _, score in HAZARD_PROXIMITY_TIERS]
    assert distances == sorted(distances)
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= score <= 100 for score in scores)


def test_region_share_constant_matches_risk_engine_minimum():
    from src.risk_engine import MIN_COVERAGE_FOR_SCORE

    # Coverage from lighting + hazard + pedestrian must clear the bar, while
    # lighting alone must not, so the prototype can produce a real score.
    total = 0.35 + 0.30 + 0.25
    assert total >= MIN_COVERAGE_FOR_SCORE
    assert 0.35 < MIN_COVERAGE_FOR_SCORE
