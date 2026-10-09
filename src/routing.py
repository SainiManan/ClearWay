"""Walking directions via the OpenStreetMap routing service.

This uses the community-run routed-foot profile. No API key is required, but
that also means there is no service guarantee and stricter rate limits than a
commercial provider. All failures are raised as RoutingError so the UI can
degrade gracefully instead of crashing.

Attribution required by the ODbL: "Data (c) OpenStreetMap contributors".
"""

import math

import requests

BASE_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/foot"
USER_AGENT = "ClearWay/1.0 (OFFGRID hackathon prototype; student project)"
TIMEOUT_SECONDS = 25
MAX_ALTERNATIVES = 3

ATTRIBUTION = "Data (c) OpenStreetMap contributors"

# A waypoint snapped further than this from the requested coordinate usually
# means there is no walkable road nearby.
MAX_SNAP_DISTANCE_M = 150.0

ROUTE_IDS = ["route_a", "route_b", "route_c", "route_d"]


class RoutingError(Exception):
    """Raised when walking routes cannot be retrieved."""


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
        "provider": "OpenStreetMap routed-foot",
        "provider_note": None,
    }


def _parse_route(raw, route_id):
    """Convert one provider route into the internal route structure."""
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
        raise RoutingError("The routing service returned a route without distance or duration.") from exc

    route = _empty_route(route_id)
    route["distance_m"] = distance_m
    route["duration_s"] = duration_s
    route["geometry"] = path
    return route


def get_walking_routes(origin, destination, alternatives=MAX_ALTERNATIVES):
    """Return a list of walking routes in ClearWay's internal format.

    ``origin`` and ``destination`` are dicts with ``lat`` and ``lon`` keys.
    Raises RoutingError on any failure, including "no route found".
    """
    origin_lat, origin_lon = _check_coordinate(origin, "Origin")
    dest_lat, dest_lon = _check_coordinate(destination, "Destination")

    requested = min(max(int(alternatives), 1), MAX_ALTERNATIVES)
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
                f"The nearest walkable road to the {label} is about {gap / 1000:.1f} km away."
            )

    routes = []
    for index, raw_route in enumerate(raw_routes[:requested]):
        route_id = ROUTE_IDS[index] if index < len(ROUTE_IDS) else f"route_{index}"
        route = _parse_route(raw_route, route_id)
        if snap_notes:
            route["provider_note"] = " ".join(snap_notes)
        routes.append(route)

    if len(routes) == 1 and requested > 1:
        routes[0]["provider_note"] = (
            routes[0]["provider_note"]
            or "Only one walking route was returned for this pair of locations."
        )

    # Routes must be presented fastest-first so "Route A" is the quickest.
    routes.sort(key=lambda r: r["duration_s"])
    for index, route in enumerate(routes):
        route["route_id"] = ROUTE_IDS[index] if index < len(ROUTE_IDS) else f"route_{index}"

    return routes
