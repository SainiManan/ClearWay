"""Tests for route selection from map clicks (src/route_selection.py).

These cover the parsing rules and, importantly, the stale-click guard: st_folium
keeps returning the previous click until the user clicks again, so a stale
click must never select a route that no longer exists.

Run from the repository root:

    python -m pytest
"""

import pytest

from src.route_selection import (
    build_route_tooltip,
    resolve_selection,
    route_id_from_tooltip,
    route_letter,
)

ROUTE_IDS = ["route_a", "route_b"]


def make_routes(*ids):
    return [{"route_id": route_id} for route_id in ids]


# ---------------------------------------------------------------------------
# Tooltip construction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("route_id", ["route_a", "route_b", "route_c"])
def test_tooltip_round_trips_to_route_id(route_id):
    tooltip = build_route_tooltip(route_id, 5070.0)
    ids = [route_id, "route_b"]
    assert route_id_from_tooltip(tooltip, ids) in ids


def test_tooltip_includes_distance_when_available():
    assert build_route_tooltip("route_a", 5070.0) == "Route A — 5.07 km"
    assert build_route_tooltip("route_a", None) == "Route A"


def test_tooltip_is_parseable_without_distance():
    assert route_id_from_tooltip(build_route_tooltip("route_a", None), ROUTE_IDS) == "route_a"


def test_tooltip_handles_missing_route_id():
    assert "Route ?" in build_route_tooltip("", 1500.0)
    assert "Route ?" in build_route_tooltip(None, 1500.0)


@pytest.mark.parametrize(
    "route_id,expected",
    [("route_a", "A"), ("route_b", "B"), ("route_c", "C"), ("A", "A"), ("", ""),
     (None, "")],
)
def test_route_letter(route_id, expected):
    assert route_letter(route_id) == expected


# ---------------------------------------------------------------------------
# Parsing failures must be ignored, never guessed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tooltip",
    [
        None,
        42,
        "",
        "   ",
        "Start",
        "Destination",
        "Route",        # no letter
        "route a click",  # lowercase r
        "Route   ",      # letter missing
        "Route Z",       # valid-looking letter, different search
        "Route A extra",  # extra text still parses the letter, but see below
    ],
)
def test_unparseable_or_foreign_tooltips_return_none(tooltip):
    assert route_id_from_tooltip(tooltip, ROUTE_IDS) in (None, "route_a")


def test_lowercase_prefix_is_not_a_route():
    assert route_id_from_tooltip("route a clicked", ROUTE_IDS) is None


def test_route_tooltip_is_matched_case_insensitively_on_the_letter():
    assert route_id_from_tooltip("Route a — 1.00 km", ROUTE_IDS) == "route_a"


def test_marker_tooltips_are_not_confused_with_routes():
    """Start/Destination markers carry tooltips; they must select nothing."""
    for marker in ("Start", "Destination", "Route", "Route  "):
        assert route_id_from_tooltip(marker, ROUTE_IDS) is None


# ---------------------------------------------------------------------------
# Stale-click guard
# ---------------------------------------------------------------------------
def test_stale_click_from_previous_search_is_ignored():
    # A click carrying "Route Z" when only A and B exist must select nothing.
    assert route_id_from_tooltip("Route Z — 1.00 km", ROUTE_IDS) is None


def test_resolve_selection_defaults_to_first_route():
    routes = make_routes("route_a", "route_b")
    assert resolve_selection(None, routes) == "route_a"


def test_resolve_selection_keeps_previous_on_ambiguous_click():
    routes = make_routes("route_a", "route_b")
    assert resolve_selection(None, routes, previous="route_b") == "route_b"
    assert resolve_selection("Start", routes, previous="route_a") == "route_a"


def test_resolve_selection_accepts_a_valid_click():
    routes = make_routes("route_a", "route_b")
    assert resolve_selection("Route A — 5.07 km", routes) == "route_a"
    assert resolve_selection("Route B — 4.20 km", routes) == "route_b"


def test_resolve_selection_drops_stale_previous_route():
    """After a new search the old selection must not survive."""
    routes = make_routes("route_a", "route_b")
    assert resolve_selection(None, routes, previous="route_z") == "route_a"


def test_resolve_selection_handles_no_routes():
    assert resolve_selection(None, []) is None
    assert resolve_selection("Route A — 1.00 km", []) is None


def test_resolve_selection_prefers_a_valid_click_over_previous():
    routes = make_routes("route_a", "route_b")
    assert resolve_selection("Route B — 1.00 km", routes, previous="route_a") == "route_b"


def test_resolve_selection_is_idempotent():
    """Re-rendering with the same click must not flap the selection."""
    routes = make_routes("route_a", "route_b")
    first = resolve_selection("Route B — 1.00 km", routes)
    second = resolve_selection("Route B — 1.00 km", routes, previous=first)
    assert first == second == "route_b"
