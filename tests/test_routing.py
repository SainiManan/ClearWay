"""Tests for geocoding and routing error handling, using mocked HTTP responses.

Run from the repository root:

    python -m pytest
"""

import pytest
import requests

from src import routing
from src.geocoding import GeocodingError, geocode
from src.routing import (
    RoutingError,
    describe_provider,
    get_walking_routes,
)

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


# ---------------------------------------------------------------------------
# OpenRouteService provider
# ---------------------------------------------------------------------------

ORS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "summary": {"distance": 4200.5, "duration": 2900.0},
                "segments": [],
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [75.8235, 26.9250],
                    [75.8000, 26.9200],
                    [75.7870, 26.9190],
                ],
            },
        }
    ],
}

TEST_KEY = "test-key-not-real"


def _patch_post(monkeypatch, response=None, exc=None, status=200):
    def fake_post(*args, **kwargs):
        if exc is not None:
            raise exc
        # FakeResponse takes status_code first, payload second.
        return FakeResponse(status_code=status, payload=response)

    monkeypatch.setattr(routing.requests, "post", fake_post)


def _no_key(monkeypatch):
    monkeypatch.setattr(routing, "get_routing_api_key", lambda: None)


@pytest.fixture
def ors_key(monkeypatch):
    monkeypatch.setattr(routing, "get_routing_api_key", lambda: TEST_KEY)


def test_parses_ors_geojson_feature():
    route = routing._parse_ors_feature(ORS_GEOJSON["features"][0], "route_a")

    assert route["distance_m"] == pytest.approx(4200.5)
    assert route["duration_s"] == pytest.approx(2900.0)
    # [lon, lat] in, [lat, lon] out.
    assert route["geometry"][0] == [26.9250, 75.8235]
    # Provider and attribution are stamped by _finalise, not the parser.
    assert route["route_id"] == "route_a"
    assert set(route) >= {"distance_m", "duration_s", "geometry", "provider"}


def test_ors_rejects_missing_geometry():
    bad = {"properties": {"summary": {"distance": 1, "duration": 1}},
           "geometry": {}}
    with pytest.raises(RoutingError, match="without geometry"):
        routing._parse_ors_feature(bad, "route_a")


def test_ors_rejects_missing_summary():
    bad = {"properties": {}, "geometry": {"type": "LineString",
           "coordinates": [[75.8, 26.9], [75.7, 26.8]]}}
    with pytest.raises(RoutingError, match="without distance or duration"):
        routing._parse_ors_feature(bad, "route_a")


def test_ors_used_when_key_present(monkeypatch, ors_key):
    _patch_post(monkeypatch, ORS_GEOJSON)
    routes = get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)

    assert len(routes) == 1
    assert routes[0]["provider"] == "OpenRouteService"
    assert routes[0]["distance_m"] == pytest.approx(4200.5)


def test_ors_rejected_key_surfaces_the_error(monkeypatch, ors_key):
    _patch_post(monkeypatch, status=401)
    with pytest.raises(RoutingError, match="API key was rejected"):
        get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)


def test_ors_rate_limit_surfaces_the_error(monkeypatch, ors_key):
    _patch_post(monkeypatch, status=429)
    with pytest.raises(RoutingError, match="rate limit"):
        get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)


def test_ors_http_error(monkeypatch, ors_key):
    _patch(monkeypatch, exc=requests.ConnectionError("osm down too"))
    _patch_post(monkeypatch, status=500)
    with pytest.raises(RoutingError, match="HTTP 500"):
        get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)


def test_ors_timeout(monkeypatch, ors_key):
    _patch(monkeypatch, exc=requests.ConnectionError("osm down too"))
    _patch_post(monkeypatch, exc=requests.Timeout())
    with pytest.raises(RoutingError, match="timed out"):
        get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)


def test_ors_no_routes(monkeypatch, ors_key):
    _patch(monkeypatch, exc=requests.ConnectionError("osm down too"))
    _patch_post(monkeypatch, {"type": "FeatureCollection", "features": []})
    with pytest.raises(RoutingError, match="No walking route"):
        get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)


def test_ors_unsupported_shape_is_rejected(monkeypatch, ors_key):
    _patch(monkeypatch, exc=requests.ConnectionError("osm down too"))
    _patch_post(monkeypatch, {"routes": [{"summary": {}, "geometry": "encoded"}]})
    with pytest.raises(RoutingError, match="response shape"):
        get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)


def test_ors_falls_back_to_osm_on_network_failure(monkeypatch, ors_key):
    """A transient ORS outage should degrade to the keyless provider."""
    _patch_post(monkeypatch, exc=requests.ConnectionError("ors down"))
    _patch(monkeypatch, FakeResponse(payload=OK_PAYLOAD))

    routes = get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)

    assert routes[0]["provider"] == "OpenStreetMap routed-foot"
    assert "OpenRouteService was unavailable" in routes[0]["provider_note"]


def test_osm_used_when_no_key(monkeypatch):
    _no_key(monkeypatch)
    _patch(monkeypatch, FakeResponse(payload=OK_PAYLOAD))

    routes = get_walking_routes(ORIGIN, DESTINATION)
    assert routes[0]["provider"] == "OpenStreetMap routed-foot"


def test_sends_authorisation_header(monkeypatch, ors_key):
    seen = {}

    def fake_post(*args, **kwargs):
        seen.update(kwargs.get("headers", {}))
        return FakeResponse(ORS_GEOJSON)

    monkeypatch.setattr(routing.requests, "post", fake_post)
    get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)

    assert seen["Authorization"] == TEST_KEY


def test_requests_geojson_not_polyline(monkeypatch, ors_key):
    seen = {}

    def fake_post(*args, **kwargs):
        seen.update(kwargs.get("headers", {}))
        return FakeResponse(ORS_GEOJSON)

    monkeypatch.setattr(routing.requests, "post", fake_post)
    get_walking_routes(ORIGIN, DESTINATION, api_key=TEST_KEY)

    assert "geo+json" in seen["Accept"]


def test_coordinates_still_validated_before_provider_choice(monkeypatch, ors_key):
    with pytest.raises(RoutingError):
        get_walking_routes({"lat": "x", "lon": 0}, DESTINATION, api_key=TEST_KEY)


def test_describe_provider_reports_the_active_one(monkeypatch, ors_key):
    assert describe_provider() == "OpenRouteService (API key configured)"

    _no_key(monkeypatch)
    assert "no API key" in describe_provider()


def test_api_key_read_from_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTESERVICE_API_KEY", "env-key")
    monkeypatch.setattr(
        routing, "_streamlit_secret_key", lambda: None
    )
    assert routing.get_routing_api_key() == "env-key"


def test_api_key_absent_returns_none(monkeypatch):
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    monkeypatch.setattr(routing, "_streamlit_secret_key", lambda: None)
    assert routing.get_routing_api_key() is None
