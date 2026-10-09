"""Route selection from map clicks.

Kept free of Streamlit and Folium imports so the parsing rules can be unit
tested directly.

The map returns whatever tooltip the clicked layer was carrying. These helpers
build that tooltip and turn a click back into a route id, with two safety
rules:

1. An unparseable click is ignored rather than guessed.
2. A route id that is not part of the current search is ignored, because
   st_folium keeps returning the previous click until the user clicks again.
"""

import re

# "Route A — 5.07 km" or "Route A".
TOOLTIP_PATTERN = re.compile(r"^Route\s+([A-Za-z0-9]+)\b")


def build_route_tooltip(route_id, distance_m=None):
    """The tooltip a route layer carries, used as its click identity."""
    letter = route_id.split("_")[-1].upper() if route_id else "?"
    if distance_m is None:
        return f"Route {letter}"
    return f"Route {letter} — {float(distance_m) / 1000.0:.2f} km"


def route_letter(route_id):
    """'route_b' -> 'B'. Also tolerates an already-short id."""
    if not route_id:
        return ""
    return route_id.split("_")[-1].upper()


def route_id_from_tooltip(tooltip, valid_route_ids):
    """Return the route id a map click refers to, or None.

    ``valid_route_ids`` is the set of route ids from the current search; a
    click carrying anything else is treated as no selection at all.
    """
    if not isinstance(tooltip, str):
        return None

    match = TOOLTIP_PATTERN.match(tooltip.strip())
    if not match:
        return None

    letter = match.group(1).upper()
    valid = set(valid_route_ids or [])
    for candidate in valid:
        if route_letter(candidate) == letter:
            return candidate
    return None


def resolve_selection(tooltip, routes, previous=None):
    """Decide which route should be selected after a map interaction.

    Returns the new selection (a route id or None). ``previous`` is the current
    selection, retained only while it is still part of this result set, so a
    stale click from an earlier search can never highlight a route that no
    longer exists. When there is nothing valid to retain, the first route is
    focused so the map and the selector always agree on something.
    """
    ids = [route.get("route_id") for route in routes or []]
    chosen = route_id_from_tooltip(tooltip, ids)
    if chosen:
        return chosen

    if previous in ids:
        return previous

    return routes[0]["route_id"] if routes else None
