"""Tests for the REAL pedestrian-infrastructure data layer.

The Overpass fetch is mocked everywhere so these tests never touch the network.
The geometry maths is tested directly because that is where correctness matters.

Run from the repository root:

    python -m pytest
"""

import pytest

from src import osm_data
from src.osm_data import (
    FOOTWAY_SNAP_M,
    OverpassError,
    _bbox_from_geometry,
    _parse_response,
    compute_footway_fraction,
    fetch_pedestrian_infrastructure,
    pedestrian_indicator,
)

# A route running east along latitude 26.9250 through central Jaipur.
ROUTE = [
    [26.9250, 75.8000],
    [26.9250, 75.8100],
    [26.9250, 75.8200],
]

# A footway that overlaps the middle of the route.
OVERPASS_PAYLOAD = {
    "osm3s": {"timestamp_osm_base": "2026-10-09T19:40:29Z"},
    "elements": [
        {
            "type": "way",
            "tags": {"highway": "footway"},
            "geometry": [
                {"lat": 26.9250, "lon": 75.8050},
                {"lat": 26.9250, "lon": 75.8150},
            ],
        },
        {
            "type": "node",
            "tags": {"highway": "crossing"},
            "lat": 26.9250,
            "lon": 75.8100,
        },
        {
            # Ignored: a way with a single node has no segment.
            "type": "way",
            "tags": {"highway": "footway"},
            "geometry": [{"lat": 26.93, "lon": 75.81}],
        },
    ],
}


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(osm_data, "CACHE_DIR", tmp_path)
    # Keep the retry backoff from slowing the suite down.
    monkeypatch.setattr(osm_data, "RETRY_BACKOFF_S", 0.0)


def _patch(monkeypatch, response=None, exc=None, status=200):
    def fake_post(*args, **kwargs):
        if exc is not None:
            raise exc
        return _FakeResponse(response, status)

    monkeypatch.setattr(osm_data.requests, "post", fake_post)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


# ---------------------------------------------------------------------------
# Overpass response parsing
# ---------------------------------------------------------------------------
def test_parse_response_extracts_footways_and_crossings():
    parsed = _parse_response(OVERPASS_PAYLOAD)
    assert len(parsed["footways"]) == 1
    assert parsed["footways"][0] == [[26.9250, 75.8050], [26.9250, 75.8150]]
    assert parsed["crossings"] == [[26.9250, 75.8100]]
    assert parsed["timestamp_osm_base"] == "2026-10-09T19:40:29Z"


def test_parse_response_handles_empty_payload():
    parsed = _parse_response({"elements": []})
    assert parsed["footways"] == []
    assert parsed["crossings"] == []


# ---------------------------------------------------------------------------
# Geometry maths
# ---------------------------------------------------------------------------
def test_point_segment_distance_to_straight_line():
    # 1 degree of longitude at the equator's latitude here is ~100 km; use a
    # small offset that is unambiguous.
    distance = osm_data._point_segment_distance_m(
        26.9250, 75.8050, 26.9250, 75.8000, 26.9250, 75.8100
    )
    assert distance == pytest.approx(0.0, abs=5.0)


def test_point_segment_distance_from_midpoint():
    # Point 0.001 degrees north of a horizontal segment (~111 m).
    distance = osm_data._point_segment_distance_m(
        26.9260, 75.8050, 26.9250, 75.8000, 26.9250, 75.8100
    )
    assert distance == pytest.approx(111.0, rel=0.05)


def test_point_segment_distance_measures_longitude_offset():
    # Point 0.005 degrees east of a vertical segment:
    # 0.005 * 111320 * cos(26.925 deg) = ~496 m.
    distance = osm_data._point_segment_distance_m(
        26.9250, 75.8050, 26.9000, 75.8000, 26.9500, 75.8000
    )
    assert distance == pytest.approx(496.0, rel=0.05)


def test_point_segment_distance_clamps_to_endpoint():
    # Point 0.010 degrees beyond the segment end, measured to the endpoint:
    # 0.010 * 111320 * cos(26.925 deg) = ~993 m.
    distance = osm_data._point_segment_distance_m(
        26.9250, 75.8200, 26.9250, 75.8000, 26.9250, 75.8100
    )
    assert distance == pytest.approx(993.0, rel=0.05)


def test_compute_footway_fraction_all_on_footway():
    footway = [[26.9250, 75.8000], [26.9250, 75.8200]]
    fraction = compute_footway_fraction(ROUTE, [footway])
    assert fraction == pytest.approx(1.0)


def test_compute_footway_fraction_none_on_footway():
    far_footway = [[27.0000, 75.8000], [27.0000, 75.8200]]
    fraction = compute_footway_fraction(ROUTE, [far_footway])
    assert fraction == 0.0


def test_bbox_includes_margin():
    south, west, north, east = _bbox_from_geometry(ROUTE)
    assert south < 26.9250 < north
    assert west < 75.8000
    assert east > 75.8200


# ---------------------------------------------------------------------------
# Indicator behaviour
# ---------------------------------------------------------------------------
def test_pedestrian_indicator_reports_real_data(monkeypatch):
    _patch(monkeypatch, OVERPASS_PAYLOAD)
    indicator = pedestrian_indicator(ROUTE, 2000.0)

    assert indicator["status"] == "observed"
    assert indicator["source"] == "OpenStreetMap via Overpass API"
    assert 0.0 <= indicator["value"] <= 100.0
    assert "OpenStreetMap" in indicator["detail"]
    assert "sparse" in indicator["detail"]
    assert indicator["footway_way_count"] == 1


def test_pedestrian_indicator_marks_missing_when_no_footways(monkeypatch):
    payload = {"elements": [{"type": "node", "tags": {}, "lat": 1, "lon": 1}]}
    _patch(monkeypatch, payload)
    indicator = pedestrian_indicator(ROUTE, 2000.0)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "No footways" in indicator["detail"]


def test_pedestrian_indicator_degrades_on_overpass_failure(monkeypatch):
    import requests

    _patch(monkeypatch, exc=requests.ConnectionError("overpass down"))
    indicator = pedestrian_indicator(ROUTE, 2000.0)

    assert indicator["value"] is None
    assert indicator["status"] == "missing"
    assert "excluded from the score" in indicator["detail"]


def test_pedestrian_indicator_handles_http_error(monkeypatch):
    # pedestrian_indicator never raises: it degrades to a missing factor.
    _patch(monkeypatch, status=504)
    indicator = pedestrian_indicator(ROUTE, 2000.0)
    assert indicator["value"] is None
    assert indicator["status"] == "missing"


def test_fetch_raises_after_exhausting_retries(monkeypatch):
    _patch(monkeypatch, status=504)
    with pytest.raises(OverpassError, match="HTTP 504"):
        fetch_pedestrian_infrastructure(ROUTE, use_cache=False)


def test_fetch_retries_then_succeeds(monkeypatch):
    results = iter(
        [_FakeResponse(None, 504), _FakeResponse(None, 504), _FakeResponse(OVERPASS_PAYLOAD)]
    )

    def flaky_post(*args, **kwargs):
        return next(results)

    monkeypatch.setattr(osm_data.requests, "post", flaky_post)
    parsed = fetch_pedestrian_infrastructure(ROUTE, use_cache=False)
    assert len(parsed["footways"]) == 1


def test_pedestrian_indicator_handles_empty_geometry():
    indicator = pedestrian_indicator([], None)
    assert indicator["value"] is None
    assert indicator["status"] == "missing"


# ---------------------------------------------------------------------------
# Caching and fetching
# ---------------------------------------------------------------------------
def test_fetch_caches_response(monkeypatch, tmp_path):
    calls = {"n": 0}

    def counting_post(*args, **kwargs):
        calls["n"] += 1
        return _FakeResponse(OVERPASS_PAYLOAD)

    monkeypatch.setattr(osm_data.requests, "post", counting_post)
    monkeypatch.setattr(osm_data, "CACHE_DIR", tmp_path)

    first = fetch_pedestrian_infrastructure(ROUTE)
    second = fetch_pedestrian_infrastructure(ROUTE)

    assert calls["n"] == 1, "the second call must be served from cache"
    assert first["footways"] == second["footways"]


def test_fetch_bypasses_cache_when_disabled(monkeypatch):
    calls = {"n": 0}

    def counting_post(*args, **kwargs):
        calls["n"] += 1
        return _FakeResponse(OVERPASS_PAYLOAD)

    _patch(monkeypatch, OVERPASS_PAYLOAD)
    monkeypatch.setattr(osm_data.requests, "post", counting_post)

    fetch_pedestrian_infrastructure(ROUTE, use_cache=False)
    fetch_pedestrian_infrastructure(ROUTE, use_cache=False)
    assert calls["n"] == 2


def test_fetch_requires_geometry():
    with pytest.raises(OverpassError, match="No route geometry"):
        fetch_pedestrian_infrastructure([])


def test_footway_snap_radius_is_sensible():
    assert 0 < FOOTWAY_SNAP_M < 200
