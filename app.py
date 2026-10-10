"""ClearWay — compare walking routes using available nighttime indicators.

Run from the repository root:

    streamlit run app.py
"""

import streamlit as st

from components.map_view import build_route_map
from components.route_cards import (
    render_comparison,
    render_route_cards,
    render_route_selector,
)
from src.data import (
    ATTRIBUTION,
    dataset_summary,
    route_indicators,
)
from src.explanations import route_sentences, scoring_status_text
from src.geocoding import GeocodingError, geocode
from html import escape

from src.risk_engine import assess_route
from src.route_selection import resolve_selection, route_letter
from src.routing import RoutingError, get_walking_routes
from src.theme import load_theme
from streamlit_folium import st_folium

st.set_page_config(
    page_title="ClearWay",
    page_icon="🌙",
    layout="wide",
)

load_theme()

# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="cw-hero">
        <h1 class="cw-hero-brand">
            <span class="cw-hero-logo">🌙</span>
            ClearWay
        </h1>
        <p class="cw-hero-tagline">
            Compare walking routes at night using the safety indicators that are
            actually available — and see exactly where the data runs out.
        </p>
        <div class="cw-hero-badges">
            <span class="cw-badge cw-badge-accent">
                <span class="cw-badge-dot"></span>OFFGRID · PS1
            </span>
            <span class="cw-badge">Live routing</span>
            <span class="cw-badge">Explainable scores</span>
            <span class="cw-badge">Missing data shown, never hidden</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="cw-callout">
        <span class="cw-callout-icon">⚠️</span>
        <div class="cw-callout-body">
            <strong>ClearWay provides estimates based on available data. It cannot
            guarantee personal safety.</strong> Missing or outdated data may
            affect route assessments. A low score is not proof that a route is safe.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Search panel
# ---------------------------------------------------------------------------
with st.form("route_search"):
    col_start, col_dest = st.columns(2)
    with col_start:
        st.markdown('<span class="cw-field-label">Starting location</span>',
                    unsafe_allow_html=True)
        start_input = st.text_input(
            "Starting location",
            value=st.session_state.get("start_value", ""),
            placeholder="e.g. Hawa Mahal, Jaipur",
            label_visibility="collapsed",
        )
    with col_dest:
        st.markdown('<span class="cw-field-label">Destination</span>',
                    unsafe_allow_html=True)
        destination_input = st.text_input(
            "Destination",
            value=st.session_state.get("destination_value", ""),
            placeholder="e.g. Jaipur Junction railway station",
            label_visibility="collapsed",
        )

    submitted = st.form_submit_button("Find routes")

    if submitted:
        st.session_state["start_value"] = start_input
        st.session_state["destination_value"] = destination_input
        st.session_state["search_triggered"] = True

# Initialised up front so this module stays importable outside Streamlit,
# where st.stop() does not halt execution.
origin = None
destination = None
routes = []
assessments = {}
indicators_by_route = {}

if not st.session_state.get("search_triggered"):
    st.info("Enter a starting location and a destination, then select **Find routes**.")
    st.stop()

start_input = (st.session_state.get("start_value") or "").strip()
destination_input = (st.session_state.get("destination_value") or "").strip()

if not start_input or not destination_input:
    st.error("Both a starting location and a destination are required.")
    st.stop()

if start_input.lower() == destination_input.lower():
    st.error(
        "The starting location and destination are the same. "
        "Enter two different places."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------
with st.spinner(f"Resolving '{start_input}' and '{destination_input}'..."):
    try:
        origin = geocode(start_input)
        destination = geocode(destination_input)
    except GeocodingError as error:
        st.error(f"Could not resolve the locations: {error}")
        st.stop()

if origin is None:
    st.error(
        f"No place called '{start_input}' could be found. Try a more specific "
        "name, for example a landmark plus a city."
    )
    st.stop()

if destination is None:
    st.error(
        f"No place called '{destination_input}' could be found. Try a more "
        "specific name, for example a landmark plus a city."
    )
    st.stop()

st.markdown(
    f"""
    <div class="cw-resolved">
        <div class="cw-resolved-item">
            <span class="cw-resolved-label">Start</span>
            <span class="cw-resolved-value">{origin['display_name']}</span>
        </div>
        <div class="cw-resolved-item">
            <span class="cw-resolved-label">Destination</span>
            <span class="cw-resolved-value">{destination['display_name']}</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
with st.spinner("Requesting walking routes..."):
    try:
        routes = get_walking_routes(origin, destination)
    except RoutingError as error:
        st.error(f"Could not retrieve walking routes: {error}")
        st.stop()

# ---------------------------------------------------------------------------
# Indicators and scoring
# ---------------------------------------------------------------------------
# This is the slowest step by far. On a cold cache each route needs an
# OpenStreetMap Overpass query, which measured ~17s for two routes, so it gets
# a determinate progress bar rather than a bare spinner that looks frozen.
assessments = {}
indicators_by_route = {}
notes_by_route = {}

scoring_status = st.empty()
scoring_bar = st.progress(0.0)

for index, route in enumerate(routes):
    scoring_status.markdown(
        f'<div class="cw-loading-row"><span class="cw-spinner"></span>'
        f"{escape(scoring_status_text(route['route_id'], index + 1, len(routes)))}"
        f"</div>",
        unsafe_allow_html=True,
    )
    scoring_bar.progress((index + 1) / len(routes))

    indicators, notes = route_indicators(route)
    assessment = assess_route(indicators)
    assessment["sentences"] = route_sentences(route, assessment)

    indicators_by_route[route["route_id"]] = indicators
    notes_by_route[route["route_id"]] = notes
    assessments[route["route_id"]] = assessment

    # Keep the internal route structure in step with the assessment so the map
    # legend and the cards always agree.
    route["risk_score"] = assessment["score"]
    route["risk_label"] = assessment["label"]
    route["data_coverage"] = assessment["coverage"]
    route["indicators"] = indicators
    route["explanation"] = assessment["sentences"]

scoring_bar.empty()
scoring_status.empty()

# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------
st.markdown('<div class="cw-section-title">Route map</div>',
            unsafe_allow_html=True)

# A pending state while the map is built and its tiles load. The animation is
# CSS-driven and self-terminating, so it can never get stuck showing after the
# map has arrived.
st.markdown(
    """
    <div class="cw-map-pending">
        <div class="cw-map-pending-inner">
            <div class="cw-spinner cw-spinner-lg"></div>
            <div class="cw-map-pending-text">Building the map…</div>
            <div class="cw-map-pending-sub">Fetching basemap tiles</div>
        </div>
        <svg class="cw-route-draw" viewBox="0 0 320 120" preserveAspectRatio="none">
            <path d="M8 96 C 70 96, 62 30, 128 34 S 214 108, 312 22"
                  fill="none" stroke="#f5a524" stroke-width="3"
                  stroke-linecap="round" />
        </svg>
    </div>
    """,
    unsafe_allow_html=True,
)

# A new search invalidates any previous map click, because st_folium keeps
# returning the last click until the user clicks again.
search_signature = f"{origin['display_name']}|{destination['display_name']}"
if st.session_state.get("search_signature") != search_signature:
    st.session_state["search_signature"] = search_signature
    st.session_state["selected_route"] = None

selected_route = st.session_state.get("selected_route")

# Native selector, kept in step with map clicks.
radio_selection = render_route_selector(routes, selected_route)
if radio_selection != selected_route:
    st.session_state["selected_route"] = radio_selection
    st.rerun()

map_error = None
route_map = None
try:
    route_map = build_route_map(
        origin, destination, routes, selected_id=selected_route
    )
except Exception as error:  # noqa: BLE001 - the page must survive a map failure
    map_error = str(error)

if route_map is None:
    st.error(f"The map could not be rendered ({map_error}). Route details are shown below.")
    st.write("Base map © OpenStreetMap contributors")
else:
    st.markdown('<div class="cw-map-frame">', unsafe_allow_html=True)
    map_output = st_folium(
        route_map,
        width=None,
        height=520,
        returned_objects=["last_object_clicked_tooltip"],
    )
    st.markdown("</div>", unsafe_allow_html=True)

    # Turn a map click into a selection. A background click returns no tooltip,
    # so the previous selection is retained rather than cleared.
    clicked_tooltip = (map_output or {}).get("last_object_clicked_tooltip")
    new_selection = resolve_selection(
        clicked_tooltip, routes, previous=selected_route
    )
    if new_selection != selected_route:
        st.session_state["selected_route"] = new_selection
        st.rerun()

st.caption(
    f"Route geometry, distance and duration are live data from the "
    f"{routes[0].get('provider', 'routing service')}. {ATTRIBUTION}. "
    "Click a route on the map to focus it."
)

# ---------------------------------------------------------------------------
# Route comparison
# ---------------------------------------------------------------------------
st.markdown('<div class="cw-section-title">Route comparison</div>',
            unsafe_allow_html=True)
render_comparison(routes, assessments)
render_route_cards(
    routes,
    assessments,
    indicators_by_route,
    selected_id=st.session_state.get("selected_route"),
)

# ---------------------------------------------------------------------------
# Data provenance and limitations
# ---------------------------------------------------------------------------
summary = dataset_summary()

with st.expander("Data sources, coverage and limitations", expanded=False):
    st.markdown('<div class="cw-subhead">Why lighting and hazards are simulated</div>',
                unsafe_allow_html=True)
    st.markdown(
        """
        <p style="font-size:0.88rem;line-height:1.65;color:var(--cw-muted);">
        OpenStreetMap has no <code>lit=*</code> tags and no
        <code>highway=street_lamp</code> nodes in Jaipur — the counts are
        literally zero. The <em>Street Lights Data Jaipur</em> dataset on
        data.gov.in publishes aggregate LED counts, not per-light coordinates,
        and its resource is not publicly downloadable. No open hazard or
        incident feed exists. Substituting a labelled simulated dataset is
        honest; inventing lighting coverage would not be.
        </p>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="cw-subhead">What is real and what is not</div>',
                unsafe_allow_html=True)

    provider = routes[0].get("provider", "routing service")
    st.markdown(
        f'<div class="cw-loading-row" style="border-left:3px solid var(--cw-accent);">'
        f'<span class="cw-spinner" style="animation:none;"></span>'
        f"<span>Routing provider actually used for these routes: "
        f"<strong>{provider}</strong></span></div>",
        unsafe_allow_html=True,
    )
    simulated_items = [
        f"Street lighting — {summary['lighting_rows']} simulated records.",
        f"Reported hazards — {summary['hazard_rows']} simulated records.",
        "Neither file contains real municipal data. Both are generated by "
        "`data/generate_sample_data.py` and cover only the "
        f"{summary['region']}.",
        "Route isolation has **no data source** and is reported as missing, "
        "never estimated.",
    ]

    st.markdown(
        '<div class="cw-split">'
        '<div><strong style="font-size:0.8rem;letter-spacing:0.06em;'
        'text-transform:uppercase;color:var(--cw-low);">Real data</strong>'
        '<ul class="cw-list">'
        + "".join(
            [
                f"<li>Route geometry, distance and walking time — live from the {provider}.</li>",
                "<li>Start and destination coordinates — live from OpenStreetMap Nominatim.</li>",
                "<li>Pedestrian infrastructure — <strong>real</strong>, fetched live from "
                "OpenStreetMap via the Overpass API (<code>src/osm_data.py</code>).</li>",
            ]
        )
        + "</ul></div>"
        '<div><strong style="font-size:0.8rem;letter-spacing:0.06em;'
        'text-transform:uppercase;color:var(--cw-moderate);">Simulated data</strong>'
        '<ul class="cw-list">'
        + "".join(f"<li>{item}</li>" for item in simulated_items)
        + "</ul></div>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="cw-subhead">Limitation of the real pedestrian data</div>',
                unsafe_allow_html=True)
    st.markdown(
        """
        <p style="font-size:0.88rem;line-height:1.65;color:var(--cw-muted);">
        Footway and sidewalk coverage in Jaipur is sparse in OpenStreetMap, so the
        pedestrian factor measures what has been <strong>mapped</strong>, not
        ground conditions. An unmapped footpath is not proof that no footpath exists.
        </p>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="cw-subhead">Limitations of the model</div>',
                unsafe_allow_html=True)
    limitations = [
        "A score is only calculated when at least 50% of the scoring weight is "
        "backed by available data. Otherwise no score is shown.",
        "A missing factor is never treated as zero risk.",
        "An absence of hazard reports is not evidence that an area is safe.",
        "The weights (lighting 35%, hazards 30%, pedestrian infrastructure 25%, "
        "isolation 10%) are illustrative prototype assumptions, not validated "
        "coefficients.",
        "Scores are not a statistically validated probability of harm.",
    ]
    st.markdown(
        '<ul class="cw-list">' + "".join(f"<li>{item}</li>" for item in limitations) + "</ul>",
        unsafe_allow_html=True,
    )

st.markdown(
    f"""
    <div class="cw-footer">
        Map data © OpenStreetMap contributors (ODbL). Indicator data in this
        prototype is partly simulated and partly real OpenStreetMap data.
        ClearWay is a hackathon prototype, not safety advice.
    </div>
    """,
    unsafe_allow_html=True,
)
