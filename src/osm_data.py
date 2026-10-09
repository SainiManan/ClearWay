"""REAL pedestrian-infrastructure data from OpenStreetMap.

This module replaces one simulated factor with genuinely real data: OpenStreetMap
maps footways and pedestrian crossings, and we query them through the Overpass
API. Nothing here is synthetic.

What is NOT available from any open source, and is therefore still simulated:
  * Street lighting. OSM has no `lit=*` tags and no `highway=street_lamp` nodes
    in Jaipur. The "Street Lights Data Jaipur" dataset on data.gov.in publishes
    aggregate LED counts, not per-light coordinates, and its resource is not
    publicly downloadable. There is no usable real lighting source.
  * Reported hazards. No open incident feed exists.
  * Route isolation. No data source.

Attribution required by the ODbL: "Data (c) OpenStreetMap contributors".
"""

import json
import math
import time
from pathlib import Path

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "ClearWay/1.0 (OFFGRID hackathon prototype; student project)"

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / ".cache"
CACHE_SUFFIX = ".json"

REQUEST_TIMEOUT_S = 60
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 3.0

# A route point counts as "on mapped pedestrian infrastructure" when a mapped
# footway passes within this distance.
FOOTWAY_SNAP_M = 25.0
# Crossings within this distance of the route are reported as supporting detail.
CROSSING_RADIUS_M = 40.0

# Extra padding around the route bounding box, in degrees (~200 m).
BBOX_MARGIN_DEG = 0.002

ATTRIBUTION = "Data (c) OpenStreetMap contributors"

OVERPASS_QUERY = """
[out:json][timeout:{timeout}];
(
  way["highway"="footway"]({south},{west},{north},{east});
  way["highway"="path"]["foot"!="no"]({south},{west},{north},{east});
  node["highway"="crossing"]({south},{west},{north},{east});
);
out geom;
"""


class OverpassError(Exception):
    """Raised when pedestrian infrastructure cannot be fetched."""


def _cache_path(bbox):
    south, west, north, east = bbox
    key = f"{south:.4f}_{west:.4f}_{north:.4f}_{east:.4f}"
    return CACHE_DIR / f"osm_pedestrian_{key}{CACHE_SUFFIX}"


def _load_cache(bbox):
    path = _cache_path(bbox)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _save_cache(bbox, payload):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(bbox).write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
    except OSError:
        # A cache write failure must never break the request itself.
        pass


def _bbox_from_geometry(geometry, margin=BBOX_MARGIN_DEG):
    lats = [point[0] for point in geometry]
    lons = [point[1] for point in geometry]
    return (
        min(lats) - margin,
        min(lons) - margin,
        max(lats) + margin,
        max(lons) + margin,
    )


def _parse_response(payload):
    """Convert an Overpass response into footway segments and crossing points."""
    footways = []
    crossings = []

    for element in payload.get("elements", []):
        if element.get("type") != "way":
            if element.get("type") == "node":
                lat = element.get("lat")
                lon = element.get("lon")
                if lat is None or lon is None:
                    continue
                if (element.get("tags") or {}).get("highway") == "crossing":
                    crossings.append([float(lat), float(lon)])
            continue

        geometry = element.get("geometry") or []
        if len(geometry) < 2:
            continue

        path = [
            [float(node["lat"]), float(node["lon"])]
            for node in geometry
            if "lat" in node and "lon" in node
        ]
        if len(path) >= 2:
            footways.append(path)

    return {
        "footways": footways,
        "crossings": crossings,
        "timestamp_osm_base": (payload.get("osm3s") or {}).get(
            "timestamp_osm_base"
        ),
    }


def fetch_pedestrian_infrastructure(geometry, use_cache=True):
    """Fetch mapped footways and crossings around a route.

    Returns a dict with "footways" (list of lat/lon paths), "crossings" (list of
    lat/lon points) and the OSM data timestamp. Raises OverpassError on failure.
    """
    if not geometry or len(geometry) < 2:
        raise OverpassError("No route geometry was available to query map data.")

    bbox = _bbox_from_geometry(geometry)

    if use_cache:
        cached = _load_cache(bbox)
        if cached is not None:
            return cached

    south, west, north, east = bbox
    query = OVERPASS_QUERY.format(
        timeout=REQUEST_TIMEOUT_S - 5,
        south=south,
        west=west,
        north=north,
        east=east,
    )

    last_error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                OVERPASS_URL,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_S + 20,
            )
        except requests.Timeout:
            last_error = "the OpenStreetMap query timed out"
        except requests.RequestException as exc:
            last_error = str(exc)
        else:
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
            else:
                try:
                    parsed = _parse_response(response.json())
                except ValueError:
                    last_error = "unreadable response"
                else:
                    parsed["bbox"] = list(bbox)
                    if use_cache:
                        _save_cache(bbox, parsed)
                    return parsed

        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_S * attempt)

    raise OverpassError(
        f"Could not retrieve pedestrian infrastructure data ({last_error})."
    )


def _point_segment_distance_m(p_lat, p_lon, a_lat, a_lon, b_lat, b_lon):
    """Planar distance in metres from a point to a segment.

    An equirectangular projection around the point is accurate enough at city
    scale and far cheaper than a haversine per segment. All arguments are
    latitude/longitude pairs.
    """
    lat_scale = 111320.0
    lon_scale = 111320.0 * math.cos(math.radians(p_lat))

    # Segment vector in metres.
    dx = (b_lon - a_lon) * lon_scale
    dy = (b_lat - a_lat) * lat_scale
    length_sq = dx * dx + dy * dy

    if length_sq == 0.0:
        return math.hypot((a_lat - p_lat) * lat_scale, (a_lon - p_lon) * lon_scale)

    t = ((p_lat - a_lat) * lat_scale * dy + (p_lon - a_lon) * lon_scale * dx) / length_sq
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)

    closest_lat = a_lat + t * (b_lat - a_lat)
    closest_lon = a_lon + t * (b_lon - a_lon)
    return math.hypot(
        (p_lat - closest_lat) * lat_scale, (p_lon - closest_lon) * lon_scale
    )


def _distance_to_nearest_footway_m(point, footways):
    """Shortest distance from a point to any mapped footway segment."""
    p_lat, p_lon = point
    best = float("inf")

    for way in footways:
        for (a_lat, a_lon), (b_lat, b_lon) in zip(way, way[1:]):
            distance = _point_segment_distance_m(
                p_lat, p_lon, a_lat, a_lon, b_lat, b_lon
            )
            if distance < best:
                best = distance

    return best


def _distance_to_nearest_crossing_m(point, crossings):
    p_lat, p_lon = point
    lat_scale = 111320.0
    lon_scale = 111320.0 * math.cos(math.radians(p_lat))
    best = float("inf")
    for c_lat, c_lon in crossings:
        distance = math.hypot((c_lat - p_lat) * lat_scale, (c_lon - p_lon) * lon_scale)
        if distance < best:
            best = distance
    return best


def compute_footway_fraction(samples, footways):
    """Share of sampled route points within FOOTWAY_SNAP_M of a footway."""
    if not samples or not footways:
        return 0.0
    on_footway = 0
    for point in samples:
        if _distance_to_nearest_footway_m(point, footways) <= FOOTWAY_SNAP_M:
            on_footway += 1
    return on_footway / len(samples)


def pedestrian_indicator(route_geometry, distance_m=None):
    """REAL pedestrian-infrastructure factor, or a missing-data indicator.

    The value is the share of the route that is NOT within FOOTWAY_SNAP_M of a
    mapped footway, expressed as a 0-100 risk. It is derived from real
    OpenStreetMap geometry, never simulated.

    Important limitation carried in the detail text: OpenStreetMap's footway
    coverage is incomplete, so an unmapped footway is not proof that none exists.
    """
    from src.data import _sample_geometry

    samples = _sample_geometry(route_geometry)
    if not samples:
        return {
            "value": None,
            "status": "missing",
            "source": ATTRIBUTION,
            "observed_at": None,
            "detail": "No route geometry was available to assess pedestrian "
            "infrastructure.",
        }

    try:
        infra = fetch_pedestrian_infrastructure(route_geometry)
    except OverpassError as exc:
        return {
            "value": None,
            "status": "missing",
            "source": ATTRIBUTION,
            "observed_at": None,
            "detail": (
                "Pedestrian infrastructure data could not be retrieved from "
                f"OpenStreetMap: {exc} This factor was excluded from the score "
                "rather than estimated."
            ),
        }

    footways = infra.get("footways") or []
    crossings = infra.get("crossings") or []

    if not footways:
        return {
            "value": None,
            "status": "missing",
            "source": ATTRIBUTION,
            "observed_at": None,
            "detail": (
                "No footways or pedestrian paths are mapped in this area of "
                "OpenStreetMap, so pedestrian infrastructure could not be "
                "assessed. Unmapped is not the same as absent."
            ),
        }

    fraction = compute_footway_fraction(samples, footways)
    value = round(100.0 * (1.0 - fraction), 1)

    on_footway = int(round(fraction * len(samples)))

    crossings_near = 0
    for point in samples:
        if crossings and _distance_to_nearest_crossing_m(point, crossings) <= CROSSING_RADIUS_M:
            crossings_near += 1

    timestamp = infra.get("timestamp_osm_base") or "unknown date"
    detail = (
        f"{on_footway} of {len(samples)} sampled points ({fraction:.0%}) lie "
        f"within {FOOTWAY_SNAP_M:.0f} m of a footway or pedestrian path mapped in "
        f"OpenStreetMap ({len(footways)} ways retrieved). "
        f"{crossings_near} sampled points are within {CROSSING_RADIUS_M:.0f} m of "
        f"a mapped crossing ({len(crossings)} total). "
        f"OSM snapshot: {timestamp}.\n"
        "This measures what OpenStreetMap has MAPPED, not ground conditions. "
        "Footway and sidewalk coverage in Jaipur is sparse, so a low score "
        "mostly means footpaths are unmapped here, not that no footpath exists. "
        "Read this factor as a mapping-completeness signal, not a ground "
        "inspection."
    )

    return {
        "value": value,
        "status": "observed",
        "source": "OpenStreetMap via Overpass API",
        "observed_at": f"OSM snapshot {timestamp}",
        "detail": detail,
        "footway_fraction": fraction,
        "footway_way_count": len(footways),
        "crossing_count": len(crossings),
    }
