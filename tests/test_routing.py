"""Tests for geocoding and routing error handling, using mocked HTTP responses.

Run from the repository root:

    python -m pytest
"""

import pytest
import requests

from src import routing
from src.geocoding import GeocodingError, geocode
from src.routing import RoutingError, get_walking_routes

ORIGIN = {"lat": 26.9250, "lon": 75.8235}
DESTINATION = {"lat": 26.9190, "lon": 75.7870}


@pytest.fixture(autouse=True)
def _clear_geocode_cache():
    """The geocoding module memoises results; isolate every test from that."""
    from src import geocoding as geocoding_module

    geocoding_module._cache.clear()
    yield
    geocoding_module._cache.clear()

OK_PAYLOAD = {
    "code": "Ok",
    "waypoints": [
        {"location": [75.8235, 26.9250], "name": "start"},
        {"location": [75.7870, 26.9190], "name": "end"},
    ],
    "routes": [
        {
            "distance": 4200.5,
            "duration": 3200.0,
            "geometry": {
                "type": "LineString",
                "coordinates": [[75.8235, 26.9250], [75.80, 26.92], [75.7870, 26.9190]],
            },
        },
        {
            "distance": 5100.0,
            "duration": 3900.0,
            "geometry": {
                "type": "LineString",
                "coordinates": [[75.8235, 26.9250], [75.79, 26.93], [75.7870, 26.9190]],
            },
        },
    ],
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _patch(monkeypatch, response=None, exc=None):
    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(routing.requests, "get", fake_get)
    return calls


# ---------------------------------------------------------------------------
# Routing: success paths
# ---------------------------------------------------------------------------
def test_routing_returns_internal_structure(monkeypatch):
    _patch(monkeypatch, FakeResponse(payload=OK_PAYLOAD))
    routes = get_walking_routes(ORIGIN, DESTINATION)

    assert len(routes) == 2
    assert [route["route_id"] for route in routes] == ["route_a", "route_b"]

    first = routes[0]
    assert first["distance_m"] == pytest.approx(4200.5)  # fastest first
    assert first["duration_s"] == pytest.approx(3200.0)
    assert routes[1]["distance_m"] == pytest.approx(5100.0)
    assert first["geometry"]
    assert first["geometry"][0] == [26.9250, 75.8235]  # lat/lon, not lon/lat
    assert set(first) >= {
        "route_id",
        "distance_m",
        "duration_s",
        "geometry",
        "indicators",
        "data_coverage",
        "risk_score",
        "explanation",
    }


def test_routing_empty_route_list_raises(monkeypatch):
    payload = {"code": "Ok", "waypoints": [], "routes": []}
    _patch(monkeypatch, FakeResponse(payload=payload))
    with pytest.raises(RoutingError, match="No walking route"):
        get_walking_routes(ORIGIN, DESTINATION)


def test_routing_missing_geometry_raises(monkeypatch):
    payload = {
        "code": "Ok",
        "routes": [{"distance": 1000, "duration": 800, "geometry": {}}],
    }
    _patch(monkeypatch, FakeResponse(payload=payload))
    with pytest.raises(RoutingError, match="without geometry"):
        get_walking_routes(ORIGIN, DESTINATION)


# ---------------------------------------------------------------------------
# Routing: failure modes
# ---------------------------------------------------------------------------
def test_routing_http_error_raises(monkeypatch):
    _patch(monkeypatch, FakeResponse(status_code=503, payload=None))
    with pytest.raises(RoutingError, match="HTTP 503"):
        get_walking_routes(ORIGIN, DESTINATION)


def test_routing_timeout_raises(monkeypatch):
    _patch(monkeypatch, exc=requests.Timeout())
    with pytest.raises(RoutingError, match="timed out"):
        get_walking_routes(ORIGIN, DESTINATION)


def test_routing_connection_error_raises(monkeypatch):
    _patch(monkeypatch, exc=requests.ConnectionError("dns down"))
    with pytest.raises(RoutingError, match="Could not reach"):
        get_walking_routes(ORIGIN, DESTINATION)


def test_routing_unparseable_body_raises(monkeypatch):
    _patch(monkeypatch, FakeResponse(payload=None))
    with pytest.raises(RoutingError, match="unreadable"):
        get_walking_routes(ORIGIN, DESTINATION)


def test_routing_no_route_found_code_raises(monkeypatch):
    payload = {"code": "NoRoute", "message": "No route could be found"}
    _patch(monkeypatch, FakeResponse(payload=payload))
    with pytest.raises(RoutingError, match="could not find a walking route"):
        get_walking_routes(ORIGIN, DESTINATION)


def test_routing_rejects_bad_coordinates():
    with pytest.raises(RoutingError):
        get_walking_routes({"lat": "north", "lon": 0}, DESTINATION)

    with pytest.raises(RoutingError):
        get_walking_routes({"lat": 200, "lon": 0}, DESTINATION)

    with pytest.raises(RoutingError):
        get_walking_routes({"lat": 10}, DESTINATION)


def test_routing_warns_about_distant_snap(monkeypatch):
    payload = {
        "code": "Ok",
        "waypoints": [{"location": [75.0, 26.0]}, {"location": [75.7870, 26.9190]}],
        "routes": [
            {
                "distance": 1000,
                "duration": 800,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[75.8235, 26.9250], [75.7870, 26.9190]],
                },
            }
        ],
    }
    _patch(monkeypatch, FakeResponse(payload=payload))
    routes = get_walking_routes(ORIGIN, DESTINATION)
    assert routes[0]["provider_note"]
    assert "start" in routes[0]["provider_note"]


def test_single_alternative_is_labelled(monkeypatch):
    payload = {
        "code": "Ok",
        "waypoints": [],
        "routes": [
            {
                "distance": 1000,
                "duration": 800,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[75.8235, 26.9250], [75.7870, 26.9190]],
                },
            }
        ],
    }
    _patch(monkeypatch, FakeResponse(payload=payload))
    routes = get_walking_routes(ORIGIN, DESTINATION, alternatives=3)
    assert len(routes) == 1
    assert "Only one walking route" in routes[0]["provider_note"]


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------
def test_geocode_returns_coordinates(monkeypatch):
    monkeypatch.setattr(
        "src.geocoding.requests.get",
        lambda *a, **k: FakeResponse(
            payload=[{"lat": "26.9250", "lon": "75.8235", "display_name": "Jaipur"}]
        ),
    )
    result = geocode("Jaipur")
    assert result["lat"] == pytest.approx(26.9250)
    assert result["lon"] == pytest.approx(75.8235)
    assert result["display_name"] == "Jaipur"


def test_geocode_no_results_returns_none(monkeypatch):
    monkeypatch.setattr(
        "src.geocoding.requests.get", lambda *a, **k: FakeResponse(payload=[])
    )
    assert geocode("Nowhere at all") is None


def test_geocode_empty_query_raises():
    with pytest.raises(GeocodingError, match="cannot be empty"):
        geocode("   ")


def test_geocode_timeout_raises(monkeypatch):
    monkeypatch.setattr(
        "src.geocoding.requests.get", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout())
    )
    with pytest.raises(GeocodingError, match="timed out"):
        geocode("Jaipur")


def test_geocode_http_error_raises(monkeypatch):
    monkeypatch.setattr(
        "src.geocoding.requests.get", lambda *a, **k: FakeResponse(status_code=429)
    )
    with pytest.raises(GeocodingError, match="HTTP 429"):
        geocode("Jaipur")


def test_geocode_result_without_coordinates_raises(monkeypatch):
    monkeypatch.setattr(
        "src.geocoding.requests.get",
        lambda *a, **k: FakeResponse(payload=[{"display_name": "no coords"}]),
    )
    with pytest.raises(GeocodingError, match="missing coordinates"):
        geocode("Jaipur")
