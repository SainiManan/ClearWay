"""Walking directions from a routing provider.

Two providers are supported, chosen automatically:

1. **OpenRouteService** — used whenever an API key is available. This is a
   commercial-grade service that accepts requests from cloud hosts, which is
   why it is required for the Streamlit Cloud deployment.
2. **OpenStreetMap routed-foot** — used when no key is present. This is a
   donated community service with no signup, which is ideal for local
   development but it refuses connections from datacentre IP ranges, so it
   cannot be relied on for cloud deployment.

Both are converted into SafeStride's internal route structure so nothing
downstream depends on a vendor's response format.

Attribution required by the ODbL: "Data (c) OpenStreetMap contributors".
"""

import math
import os

import requests

BASE_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/foot"
ORS_URL = "https://api.openrouteservice.org/v2/directions/foot-walking"

USER_AGENT = "ClearWay/1.0 (OFFGRID hackathon prototype; student project)"
TIMEOUT_SECONDS = 25
ORS_TIMEOUT_SECONDS = 30
MAX_ALTERNATIVES = 3

ATTRIBUTION = "Data (c) OpenStreetMap contributors"
ORS_ATTRIBUTION = "OpenRouteService, data (c) OpenStreetMap contributors"

# A waypoint snapped further than this from the requested coordinate usually
# means there is no walkable road nearby.
MAX_SNAP_DISTANCE_M = 150.0

ROUTE_IDS = ["route_a", "route_b", "route_c", "route_d"]


class RoutingError(Exception):
    """Raised when walking routes cannot be retrieved."""


def _streamlit_secret_key():
    """Read the key from Streamlit secrets, if Streamlit is running."""
    try:
        import streamlit as st

        if hasattr(st, "secrets") and "OPENROUTESERVICE_API_KEY" in st.secrets:
            return str(st.secrets["OPENROUTESERVICE_API_KEY"])
    except Exception:  # noqa: BLE001 - running outside Streamlit must be fine
        pass
    return None


def get_routing_api_key():
    """Return an OpenRouteService key, or None.

    Checked in the environment first (works for a local .env) and then in
    Streamlit secrets (how Streamlit Cloud passes configuration).
    """
    from_path = os.environ.get("OPENROUTESERVICE_API_KEY")
    if from_path:
        return from_path
    return _streamlit_secret_key()


def _check_coordinate(coordinate, name):
    if not isinstance(coordinate, dict):
        raise RoutingError(f"{name} must be a dict with 'lat' and 'lon'.")
    try:
        lat = float(coordinate["lat"])
        lon = float(coordinate["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RoutingError(f"{name} is missing valid lat/lon values.") from exc
    if not -90 <= lat <= 90:
        raise RoutingError(f"{name} latitude is outside the valid range.")
    if not -180 <= lon <= 180:
        raise RoutingError(f"{name} longitude is outside the valid range.")
    return lat, lon


def _haversine_m(point_a, point_b):
    lat1, lon1 = point_a
    lat2, lon2 = point_b
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _empty_route(route_id):
    """The internal route shape every route must fill in."""
    return {
        "route_id": route_id,
        "distance_m": None,
        "duration_s": None,
        "geometry": [],
        "indicators": {},
        "data_coverage": 0.0,
        "risk_score": None,
        "explanation": [],
        "provider": "routing service",
        "provider_note": None,
    }


def _finalise(routes, provider, attribution, requested):
    """Apply shared post-processing and stable route ids."""
    # Routes must be presented fastest-first so "Route A" is the quickest.
    routes.sort(key=lambda r: r["duration_s"])
    for index, route in enumerate(routes):
        route["route_id"] = (
            ROUTE_IDS[index] if index < len(ROUTE_IDS) else f"route_{index}"
        )
        route["provider"] = provider
        route["attribution"] = attribution

    if len(routes) == 1 and requested > 1:
        routes[0]["provider_note"] = (
            routes[0]["provider_note"]
            or "Only one walking route was returned for this pair of locations."
        )
    return routes


def _parse_osm_route(raw, route_id):
    geometry = raw.get("geometry") or {}
    coordinates = geometry.get("coordinates")

    if geometry.get("type") != "LineString" or not coordinates:
        raise RoutingError("The routing service returned a route without geometry.")

    # The provider returns [longitude, latitude]; Folium wants [latitude, longitude].
    path = [[float(lat), float(lon)] for lon, lat in coordinates]

    try:
        distance_m = float(raw["distance"])
        duration_s = float(raw["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RoutingError(
            "The routing service returned a route without distance or duration."
        ) from exc

    route = _empty_route(route_id)
    route["distance_m"] = distance_m
    route["duration_s"] = duration_s
    route["geometry"] = path
    return route


def _osm_routes(origin, destination, requested):
    """Fetch routes from the community OpenStreetMap routing service."""
    origin_lat, origin_lon = _check_coordinate(origin, "Origin")
    dest_lat, dest_lon = _check_coordinate(destination, "Destination")

    url = f"{BASE_URL}/{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "true" if requested > 1 else "false",
        "steps": "false",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise RoutingError("The routing service timed out. Please try again.") from exc
    except requests.RequestException as exc:
        raise RoutingError(f"Could not reach the routing service: {exc}") from exc

    if response.status_code != 200:
        raise RoutingError(
            f"The routing service returned HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RoutingError("The routing service returned an unreadable response.") from exc

    code = payload.get("code")
    if code != "Ok":
        message = payload.get("message", "unknown error")
        raise RoutingError(
            f"The routing service could not find a walking route ({message})."
        )

    raw_routes = payload.get("routes") or []
    if not raw_routes:
        raise RoutingError("No walking route was found between those two locations.")

    waypoints = payload.get("waypoints") or []
    snap_notes = []
    requested_points = [(origin_lat, origin_lon), (dest_lat, dest_lon)]
    for index, waypoint in enumerate(waypoints[:2]):
        location = waypoint.get("location")
        if not location or len(location) < 2:
            continue
        snapped = (float(location[1]), float(location[0]))
        gap = _haversine_m(requested_points[index], snapped)
        if gap > MAX_SNAP_DISTANCE_M:
            label = "start" if index == 0 else "destination"
            snap_notes.append(
                f"The nearest walkable road to the {label} is about "
                f"{gap / 1000:.1f} km away."
            )

    routes = [
        _parse_osm_route(raw, ROUTE_IDS[i] if i < len(ROUTE_IDS) else f"route_{i}")
        for i, raw in enumerate(raw_routes[:requested])
    ]
    for route in routes:
        if snap_notes:
            route["provider_note"] = " ".join(snap_notes)

    return _finalise(
        routes, "OpenStreetMap routed-foot", ATTRIBUTION, requested
    )


def _parse_ors_feature(feature, route_id):
    """Convert one OpenRouteService GeoJSON feature into the internal shape."""
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")

    if geometry.get("type") != "LineString" or not coordinates:
        raise RoutingError("The routing service returned a route without geometry.")

    # ORS returns [longitude, latitude]; Folium wants [latitude, longitude].
    path = [[float(lat), float(lon)] for lon, lat in coordinates]

    properties = feature.get("properties") or {}
    summary = properties.get("summary") or {}
    try:
        distance_m = float(summary["distance"])
        duration_s = float(summary["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RoutingError(
            "The routing service returned a route without distance or duration."
        ) from exc

    route = _empty_route(route_id)
    route["distance_m"] = distance_m
    route["duration_s"] = duration_s
    route["geometry"] = path
    return route


def _ors_routes(origin, destination, requested, api_key):
    """Fetch routes from OpenRouteService (requires an API key)."""
    origin_lat, origin_lon = _check_coordinate(origin, "Origin")
    dest_lat, dest_lon = _check_coordinate(destination, "Destination")

    body = {
        "coordinates": [[origin_lon, origin_lat], [dest_lon, dest_lat]],
        "instructions": False,
        "maneuvers": False,
        "elevation": False,
    }
    if requested > 1:
        body["alternative_routes"] = {
            "target_count": requested,
            "weight_factor": 1.6,
        }

    try:
        response = requests.post(
            ORS_URL,
            json=body,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
                # GeoJSON keeps the parser simple: no polyline decoding.
                "Accept": "application/geo+json, application/json, application/gpx",
                "User-Agent": USER_AGENT,
            },
            timeout=ORS_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise RoutingError("The routing service timed out. Please try again.") from exc
    except requests.RequestException as exc:
        raise RoutingError(f"Could not reach the routing service: {exc}") from exc

    if response.status_code in (401, 403):
        raise RoutingError(
            "The OpenRouteService API key was rejected (HTTP "
            f"{response.status_code}). Check the key in your Streamlit secrets."
        )
    if response.status_code == 429:
        raise RoutingError(
            "The routing service rate limit was reached. Please try again shortly."
        )
    if response.status_code != 200:
        raise RoutingError(
            f"The routing service returned HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RoutingError("The routing service returned an unreadable response.") from exc

    if payload.get("type") == "FeatureCollection":
        raw_routes = payload.get("features") or []
        parser = _parse_ors_feature
    else:
        # Plain JSON shape: routes carry a polyline geometry we do not decode.
        raw_routes = payload.get("routes") or []
        parser = None

    if not raw_routes:
        raise RoutingError("No walking route was found between those two locations.")

    if parser is None:
        raise RoutingError(
            "The routing service returned a response shape this prototype does "
            "not support. Expected a GeoJSON FeatureCollection."
        )

    routes = [
        _parse_ors_feature(raw, ROUTE_IDS[i] if i < len(ROUTE_IDS) else f"route_{i}")
        for i, raw in enumerate(raw_routes[:requested])
    ]

    return _finalise(
        routes, "OpenRouteService", ORS_ATTRIBUTION, requested
    )


def get_walking_routes(origin, destination, alternatives=MAX_ALTERNATIVES, api_key=None):
    """Return a list of walking routes in ClearWay's internal format.

    ``origin`` and ``destination`` are dicts with ``lat`` and ``lon`` keys.
    OpenRouteService is used when a key is available; otherwise the keyless
    OpenStreetMap community service is used. Raises RoutingError on failure.
    """
    # Validate coordinates even before a provider is chosen.
    _check_coordinate(origin, "Origin")
    _check_coordinate(destination, "Destination")

    requested = min(max(int(alternatives), 1), MAX_ALTERNATIVES)
    key = api_key if api_key is not None else get_routing_api_key()

    if key:
        try:
            return _ors_routes(origin, destination, requested, key)
        except RoutingError as config_error:
            # A rejected key or rate limit is worth surfacing directly, but a
            # transient network failure is worth one retry on the keyless
            # service, which works fine locally.
            if "API key" in str(config_error) or "rate limit" in str(config_error):
                raise
            try:
                routes = _osm_routes(origin, destination, requested)
            except RoutingError:
                raise config_error from None
            routes[0]["provider_note"] = (
                "OpenRouteService was unavailable, so this route came from the "
                "OpenStreetMap community service instead. " + str(config_error)
            )
            return routes

    return _osm_routes(origin, destination, requested)


def describe_provider():
    """A one-line description of the provider that will actually be used."""
    if get_routing_api_key():
        return "OpenRouteService (API key configured)"
    return "OpenStreetMap community service (no API key configured)"
