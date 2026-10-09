"""Access to the route indicator data used by the scoring engine.

THREE KINDS OF DATA EXIST IN THIS PROJECT:

1. REAL data - route distance, duration and geometry, obtained live from the
   OpenStreetMap routing service (see src/routing.py).
2. REAL data - mapped pedestrian infrastructure (footways and crossings),
   fetched live from OpenStreetMap via the Overpass API
   (see src/osm_data.py). Genuine data with a genuine limitation: footway
   coverage in Jaipur is sparse, so this factor mostly reflects what has been
   MAPPED, not ground conditions.
3. SIMULATED data - the lighting and hazard records in data/sample_*.csv.
   These are pseudo-random values produced by data/generate_sample_data.py.
   They are NOT municipal data, NOT verified observations, and they only cover
   the demonstration region declared below.

Why lighting and hazards are simulated: OpenStreetMap has no `lit=*` tags and
no `highway=street_lamp` nodes in Jaipur, and the "Street Lights Data Jaipur"
dataset on data.gov.in publishes aggregate LED counts rather than per-light
coordinates. No open hazard or incident feed exists. Fabricating either one
would be worse than labelling a substitute.

Route isolation has no data source and is reported as missing.
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from src.geocoding import ATTRIBUTION as _GEOCODING_ATTRIBUTION
from src.osm_data import pedestrian_indicator

ATTRIBUTION = _GEOCODING_ATTRIBUTION

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LIGHTING_CSV = DATA_DIR / "sample_lighting.csv"
HAZARD_CSV = DATA_DIR / "sample_hazards.csv"

SIMULATED_SOURCE_LABEL = "Simulated sample data (data/sample_*.csv)"

# Simulated data exists ONLY inside this box. Outside it, indicators are
# reported as unavailable rather than extrapolated.
DEMO_REGION = {
    "name": "Jaipur demonstration area",
    "min_lat": 26.75,
    "max_lat": 27.02,
    "min_lon": 75.55,
    "max_lon": 75.90,
    "status": "simulated",
}

# A point on the route is treated as "lit" by the nearest simulated record
# within this distance.
LIGHTING_LOOKUP_RADIUS_M = 450.0
# If fewer than this share of the route has nearby lighting records, the
# lighting factor is reported as unavailable instead of guessed.
LIGHTING_MIN_COVERAGE = 0.5
# At least this share of the route must lie inside the demonstration area.
MIN_IN_REGION_SHARE = 0.5

LIGHTING_STATUS_RISK = {
    "lit": 10.0,
    "partial": 45.0,
    "unlit": 85.0,
}

# How far apart route points are sampled when inspecting indicators.
SAMPLE_STEP_M = 50.0

# A hazard record within this distance counts toward the route's hazard count.
HAZARD_COUNT_RADIUS_M = 200.0
# Hazard data is considered to cover a route only if a record exists within
# this distance somewhere along it.
HAZARD_PRESENCE_RADIUS_M = 1200.0

# Hazard risk = reported density (per km of route) + proximity to the nearest
# report. Both terms are capped so no single factor can dominate a score.
HAZARD_DENSITY_RISK_SCALE = 35.0
HAZARD_DENSITY_RISK_CAP = 70.0
HAZARD_PROXIMITY_TIERS = [
    (150.0, 30.0),
    (300.0, 22.0),
    (600.0, 12.0),
    (1200.0, 5.0),
]

EARTH_RADIUS_M = 6371000.0


class DataError(Exception):
    """Raised when an indicator dataset cannot be loaded."""


_lighting_cache = None
_hazard_cache = None


def _empty_dataframe(columns):
    return pd.DataFrame({column: pd.Series(dtype="float64") for column in columns})


def load_lighting():
    """Return the simulated lighting records as a DataFrame."""
    global _lighting_cache
    if _lighting_cache is not None:
        return _lighting_cache

    if not LIGHTING_CSV.exists():
        raise DataError(f"Lighting dataset not found: {LIGHTING_CSV}")

    try:
        frame = pd.read_csv(LIGHTING_CSV)
    except Exception as exc:  # pandas raises several unrelated error types
        raise DataError(f"Could not read the lighting dataset: {exc}") from exc

    required = {"latitude", "longitude", "lighting_status"}
    if frame.empty or not required.issubset(frame.columns):
        _lighting_cache = _empty_dataframe(
            ["latitude", "longitude", "lighting_status", "source"]
        )
        return _lighting_cache

    frame = frame.dropna(subset=["latitude", "longitude"])
    _lighting_cache = frame.reset_index(drop=True)
    return _lighting_cache


def load_hazards():
    """Return the simulated hazard records as a DataFrame."""
    global _hazard_cache
    if _hazard_cache is not None:
        return _hazard_cache

    if not HAZARD_CSV.exists():
        raise DataError(f"Hazard dataset not found: {HAZARD_CSV}")

    try:
        frame = pd.read_csv(HAZARD_CSV)
    except Exception as exc:
        raise DataError(f"Could not read the hazard dataset: {exc}") from exc

    required = {"latitude", "longitude", "category"}
    if frame.empty or not required.issubset(frame.columns):
        _hazard_cache = _empty_dataframe(
            ["latitude", "longitude", "category", "source"]
        )
        return _hazard_cache

    frame = frame.dropna(subset=["latitude", "longitude"])
    _hazard_cache = frame.reset_index(drop=True)
    return _hazard_cache


def dataset_summary():
    """Row counts and provenance, shown in the UI so nothing is implied."""
    try:
        lighting = load_lighting()
        lighting_count = len(lighting)
    except DataError:
        lighting_count = 0
    try:
        hazards = load_hazards()
        hazard_count = len(hazards)
    except DataError:
        hazard_count = 0

    return {
        "lighting_rows": lighting_count,
        "hazard_rows": hazard_count,
        "region": DEMO_REGION["name"],
        "region_status": DEMO_REGION["status"],
        "source": SIMULATED_SOURCE_LABEL,
        "pedestrian_source": "OpenStreetMap via Overpass API (real)",
        "pedestrian_caveat": (
            "Footway and sidewalk coverage in Jaipur is sparse in OSM, so this "
            "factor reflects mapping completeness more than ground conditions."
        ),
    }


def region_contains(lat, lon):
    """True when a coordinate falls inside the simulated-data region."""
    return (
        DEMO_REGION["min_lat"] <= lat <= DEMO_REGION["max_lat"]
        and DEMO_REGION["min_lon"] <= lon <= DEMO_REGION["max_lon"]
    )


def _distance_matrix_m(lat, lon, frame):
    """Great-circle distance in metres from one point to every record."""
    lat_rad = math.radians(lat)
    record_lat = np.radians(frame["latitude"].to_numpy(dtype="float64"))
    record_lon = np.radians(frame["longitude"].to_numpy(dtype="float64"))

    delta_lat = record_lat - lat_rad
    delta_lon = record_lon - math.radians(lon)

    a = (
        np.sin(delta_lat / 2.0) ** 2
        + np.cos(lat_rad) * np.cos(record_lat) * np.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _sample_geometry(geometry, step_m=SAMPLE_STEP_M):
    """Reduce a route path to evenly spaced sample points."""
    if not geometry or len(geometry) < 2:
        return list(geometry or [])

    samples = [tuple(geometry[0])]
    distance_since_last = 0.0

    for start, end in zip(geometry, geometry[1:]):
        (lat1, lon1), (lat2, lon2) = start, end
        segment_m = _haversine_m(lat1, lon1, lat2, lon2)
        if segment_m <= 0:
            continue

        position = step_m - distance_since_last
        while position < segment_m:
            ratio = position / segment_m
            samples.append((lat1 + (lat2 - lat1) * ratio, lon1 + (lon2 - lon1) * ratio))
            position += step_m
        distance_since_last = (distance_since_last + segment_m) % step_m

    if tuple(geometry[-1]) not in samples:
        samples.append(tuple(geometry[-1]))
    return samples


def _haversine_m(lat1, lon1, lat2, lon2):
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _missing_factor(display_name, reason, source=SIMULATED_SOURCE_LABEL):
    return {
        "value": None,
        "status": "missing",
        "source": source,
        "observed_at": None,
        "detail": reason,
    }


def _region_shares(samples):
    """Split sampled points into those inside and outside the demo region."""
    inside = [(lat, lon) for lat, lon in samples if region_contains(lat, lon)]
    outside = [
        (lat, lon) for lat, lon in samples if not region_contains(lat, lon)
    ]
    return inside, outside


def lighting_indicator(geometry):
    """Estimated lighting factor for a route, or a missing-data indicator.

    The value is a weighted average of the nearest simulated lighting status at
    each sampled route point. If the records do not cover enough of the route,
    the factor is reported as unavailable rather than guessed.
    """
    samples = _sample_geometry(geometry)
    if not samples:
        return _missing_factor(
            "Street lighting", "No route geometry was available to assess lighting."
        )

    try:
        frame = load_lighting()
    except DataError as exc:
        return _missing_factor("Street lighting", str(exc))

    if frame.empty:
        return _missing_factor(
            "Street lighting", "The lighting dataset is empty."
        )

    statuses = frame["lighting_status"].astype(str).str.lower()
    valid = statuses.isin(LIGHTING_STATUS_RISK)
    if not valid.any():
        return _missing_factor(
            "Street lighting",
            "The lighting dataset contains no recognised lighting statuses.",
        )
    frame = frame.loc[valid]
    statuses = statuses.loc[valid].to_numpy()

    in_region, out_region = _region_shares(samples)
    share_in_region = len(in_region) / len(samples)
    if share_in_region < MIN_IN_REGION_SHARE:
        reason = (
            f"Only {share_in_region:.0%} of this route lies inside the "
            f"{DEMO_REGION['name']}, where the simulated lighting data exists, "
            "so no lighting estimate was produced."
        )
        return _missing_factor("Street lighting", reason)

    tallies = {"lit": 0, "partial": 0, "unlit": 0}
    covered = 0

    for lat, lon in in_region:
        distances = _distance_matrix_m(lat, lon, frame)
        nearest = int(np.argmin(distances))
        if distances[nearest] <= LIGHTING_LOOKUP_RADIUS_M:
            tallies[statuses[nearest]] += 1
            covered += 1

    coverage = covered / len(in_region)
    if covered == 0 or coverage < LIGHTING_MIN_COVERAGE:
        return _missing_factor(
            "Street lighting",
            f"Lighting data only covers {coverage:.0%} of this route "
            f"(minimum {LIGHTING_MIN_COVERAGE:.0%} required), so no lighting "
            "estimate was produced.",
        )

    percentages = {name: tallies[name] / covered for name in tallies}
    value = sum(
        percentages[status] * risk for status, risk in LIGHTING_STATUS_RISK.items()
    )

    parts = [
        f"{percentages['lit']:.0%} lit",
        f"{percentages['partial']:.0%} partial",
        f"{percentages['unlit']:.0%} unlit",
    ]
    detail = (
        f"Based on the nearest simulated lighting record at {covered} of "
        f"{len(in_region)} sampled points inside the demonstration area "
        f"({coverage:.0%} of that stretch): "
        + ", ".join(parts)
        + ". Lighting was NOT inferred from the presence of a road."
    )

    return {
        "value": round(float(value), 1),
        "status": "simulated",
        "source": SIMULATED_SOURCE_LABEL,
        "observed_at": "simulated sample rows, see data/sample_lighting.csv",
        "detail": detail,
        "percentages": percentages,
    }


def hazard_indicator(geometry, distance_m):
    """Estimated hazard factor for a route, or a missing-data indicator.

    An absence of reports is NOT treated as evidence of safety. If no hazard
    record exists anywhere near the route, the factor is reported as missing.
    """
    samples = _sample_geometry(geometry)
    if not samples:
        return _missing_factor(
            "Reported hazards", "No route geometry was available to assess hazards."
        )

    try:
        frame = load_hazards()
    except DataError as exc:
        return _missing_factor("Reported hazards", str(exc))

    if frame.empty:
        return _missing_factor("Reported hazards", "The hazard dataset is empty.")

    in_region, _ = _region_shares(samples)
    share_in_region = len(in_region) / len(samples)
    if share_in_region < MIN_IN_REGION_SHARE:
        reason = (
            f"Only {share_in_region:.0%} of this route lies inside the "
            f"{DEMO_REGION['name']}, where the simulated hazard data exists, "
            "so no hazard estimate was produced."
        )
        return _missing_factor("Reported hazards", reason)

    hazard_ids = (
        frame["hazard_id"].tolist()
        if "hazard_id" in frame.columns
        else list(range(len(frame)))
    )

    nearest_m = None
    nearby_ids = set()

    for lat, lon in in_region:
        distances = _distance_matrix_m(lat, lon, frame)
        nearest_index = int(np.argmin(distances))
        distance = float(distances[nearest_index])

        if nearest_m is None or distance < nearest_m:
            nearest_m = distance

        within_count = np.where(distances <= HAZARD_COUNT_RADIUS_M)[0]
        for index in within_count:
            nearby_ids.add(hazard_ids[index])

    if nearest_m is None or nearest_m > HAZARD_PRESENCE_RADIUS_M:
        return _missing_factor(
            "Reported hazards",
            "No hazard reports are recorded near this route. An absence of "
            "reports is NOT evidence that a route is safe; it only means no "
            "reports were collected here.",
        )

    route_km = max(distance_m or 0.0, 1.0) / 1000.0
    per_km = len(nearby_ids) / route_km

    density_risk = min(HAZARD_DENSITY_RISK_CAP, per_km * HAZARD_DENSITY_RISK_SCALE)

    proximity_risk = 0.0
    for threshold, risk in HAZARD_PROXIMITY_TIERS:
        if nearest_m <= threshold:
            proximity_risk = risk
            break

    value = min(100.0, density_risk + proximity_risk)

    detail = (
        f"Nearest simulated hazard report is about {nearest_m:.0f} m from the "
        f"route; {len(nearby_ids)} report(s) sit within "
        f"{HAZARD_COUNT_RADIUS_M:.0f} m of it ({per_km:.1f} per km)."
    )

    return {
        "value": round(float(value), 1),
        "status": "simulated",
        "source": SIMULATED_SOURCE_LABEL,
        "observed_at": "simulated sample rows, see data/sample_hazards.csv",
        "detail": detail,
        "nearest_m": nearest_m,
        "count_within_radius": len(nearby_ids),
    }


def route_indicators(route):
    """Build the indicator bundle for one internal route structure.

    Returns (indicators, notes) where indicators is accepted directly by
    src.risk_engine.assess_route.
    """
    geometry = route.get("geometry") or []
    distance_m = route.get("distance_m") or 0.0

    if not geometry:
        indicators = {
            "lighting": _missing_factor(
                "Street lighting", "No route geometry was returned by the router."
            ),
            "hazard": _missing_factor(
                "Reported hazards", "No route geometry was returned by the router."
            ),
            "pedestrian": None,
            "isolation": None,
        }
        return indicators, ["No route geometry was available for indicator analysis."]

    indicators = {
        "lighting": lighting_indicator(geometry),
        "hazard": hazard_indicator(geometry, distance_m),
        # REAL data from OpenStreetMap, with its own stated limitations.
        "pedestrian": pedestrian_indicator(geometry, distance_m),
        # No route-isolation dataset is available for this prototype.
        "isolation": None,
    }

    notes = [
        "Route isolation data is unavailable for this prototype; this factor "
        "was excluded from the score rather than estimated."
    ]

    if indicators["pedestrian"].get("status") == "missing":
        notes.append(
            "Pedestrian infrastructure could not be retrieved from "
            "OpenStreetMap for this route, so it was excluded from the score."
        )

    return indicators, notes
