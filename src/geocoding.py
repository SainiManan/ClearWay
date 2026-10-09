"""Geocoding via OpenStreetMap Nominatim.

Nominatim is a free service with a strict usage policy: a maximum of roughly
one request per second and a meaningful User-Agent. Both are enforced here.

Attribution required by the ODbL: "Data (c) OpenStreetMap contributors".
"""

import time

import requests

ENDPOINT = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ClearWay/1.0 (OFFGRID hackathon prototype; student project)"
MIN_SECONDS_BETWEEN_REQUESTS = 1.1
TIMEOUT_SECONDS = 10

ATTRIBUTION = "Data (c) OpenStreetMap contributors"

_last_request_time = 0.0
_cache = {}


class GeocodingError(Exception):
    """Raised when a location string cannot be resolved to coordinates."""


def _respect_rate_limit():
    """Block until Nominatim's one-request-per-second policy is satisfied."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
        time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
    _last_request_time = time.monotonic()


def geocode(query, limit=1):
    """Resolve a location string to a dict with lat, lon and display_name.

    Returns None when the location is recognised by nobody. Raises
    GeocodingError when the service itself fails.
    """
    if not isinstance(query, str) or not query.strip():
        raise GeocodingError("Location cannot be empty.")

    cleaned = query.strip()
    if cleaned in _cache:
        return _cache[cleaned]

    params = {
        "q": cleaned,
        "format": "jsonv2",
        "limit": limit,
        "addressdetails": 0,
    }

    _respect_rate_limit()
    try:
        response = requests.get(
            ENDPOINT,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise GeocodingError(
            f"The geocoding service timed out looking up '{cleaned}'."
        ) from exc
    except requests.RequestException as exc:
        raise GeocodingError(
            f"Could not reach the geocoding service: {exc}"
        ) from exc

    if response.status_code != 200:
        raise GeocodingError(
            f"Geocoding service returned HTTP {response.status_code}."
        )

    try:
        results = response.json()
    except ValueError as exc:
        raise GeocodingError("Geocoding service returned an unreadable response.") from exc

    if not isinstance(results, list) or not results:
        result = None
    else:
        best = results[0]
        try:
            result = {
                "query": cleaned,
                "lat": float(best["lat"]),
                "lon": float(best["lon"]),
                "display_name": best.get("display_name", cleaned),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocodingError(
                f"Geocoding result for '{cleaned}' was missing coordinates."
            ) from exc

    _cache[cleaned] = result
    return result
