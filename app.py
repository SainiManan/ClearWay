"""ClearWay — compare walking routes using available nighttime indicators.

Run from the repository root:

    streamlit run app.py
"""

import streamlit as st

from components.map_view import build_route_map
from components.route_cards import render_comparison, render_route_cards
from src.data import (
    ATTRIBUTION,
    dataset_summary,
    route_indicators,
)
from src.explanations import route_sentences
from src.geocoding import GeocodingError, geocode
from src.risk_engine import assess_route
from src.routing import RoutingError, get_walking_routes
from streamlit_folium import st_folium

st.set_page_config(
    page_title="ClearWay",
    page_icon="🌙",
    layout="wide",
)

st.title("ClearWay 🌙")
st.subheader("Compare walking routes using available nighttime safety indicators")

st.write(
    "ClearWay compares candidate walking routes on distance, walking time and "
    "the safety indicators that are actually available. It shows where data is "
    "missing instead of filling gaps with assumptions."
)

st.warning(
    "**ClearWay provides estimates based on available data. It cannot guarantee "
    "personal safety. Missing or outdated data may affect route assessments.**"
)

# ---------------------------------------------------------------------------
# Search panel
# ---------------------------------------------------------------------------
with st.form("route_search"):
    col_start, col_dest = st.columns(2)
    with col_start:
        start_input = st.text_input(
            "Starting location",
            value=st.session_state.get("start_value", ""),
            placeholder="e.g. Hawa Mahal, Jaipur",
        )
    with col_dest:
        destination_input = st.text_input(
            "Destination",
            value=st.session_state.get("destination_value", ""),
            placeholder="e.g. Jaipur Junction railway station",
        )

    submitted = st.form_submit_button("Find routes", type="primary")

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
    st.error("The starting location and destination are the same. Enter two different places.")
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

resolved_cols = st.columns(2)
with resolved_cols[0]:
    st.success(f"**Start:** {origin['display_name']}")
with resolved_cols[1]:
    st.success(f"**Destination:** {destination['display_name']}")

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
assessments = {}
indicators_by_route = {}
notes_by_route = {}

for route in routes:
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

# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------
st.subheader("Route map")

map_error = None
route_map = None
try:
    route_map = build_route_map(origin, destination, routes)
except Exception as error:  # noqa: BLE001 - the page must survive a map failure
    map_error = str(error)

if route_map is None:
    st.error(f"The map could not be rendered ({map_error}). Route details are shown below.")
    st.write("Base map © OpenStreetMap contributors")
else:
    st_folium(route_map, width=None, height=520, returned_objects=[])

st.caption(
    f"Route geometry, distance and duration are live data from the "
    f"{routes[0].get('provider', 'routing service')}. "
    f"{ATTRIBUTION}."
)

# ---------------------------------------------------------------------------
# Route comparison
# ---------------------------------------------------------------------------
st.subheader("Route comparison")
render_comparison(routes, assessments)
render_route_cards(routes, assessments, indicators_by_route)

# ---------------------------------------------------------------------------
# Data provenance and limitations
# ---------------------------------------------------------------------------
summary = dataset_summary()

with st.expander("Data sources, coverage and limitations", expanded=False):
    st.markdown("**What is real and what is not**")

    real_items = [
        "Route geometry, distance and walking time — live from the "
        f"{routes[0].get('provider', 'routing service')}.",
        "Start and destination coordinates — live from OpenStreetMap Nominatim.",
        "Pedestrian infrastructure — **real**, fetched live from OpenStreetMap "
        "via the Overpass API (`src/osm_data.py`).",
    ]
    simulated_items = [
        f"Street lighting — {summary['lighting_rows']} simulated records.",
        f"Reported hazards — {summary['hazard_rows']} simulated records.",
        "Neither file contains real municipal data. Both are generated by "
        "`data/generate_sample_data.py` and cover only the "
        f"{summary['region']}.",
        "Route isolation has **no data source** and is reported as missing, "
        "never estimated.",
    ]

    st.markdown("**Why lighting and hazards are simulated, not real**")
    st.markdown(
        "OpenStreetMap has no `lit=*` tags and no `highway=street_lamp` nodes in "
        "Jaipur — the counts are literally zero. The *Street Lights Data Jaipur* "
        "dataset on data.gov.in publishes aggregate LED counts, not per-light "
        "coordinates, and its resource is not publicly downloadable. No open "
        "hazard or incident feed exists. Substituting a labelled simulated "
        "dataset is honest; inventing lighting coverage would not be."
    )

    st.markdown("**Limitation of the real pedestrian data**")
    st.markdown(
        "Footway and sidewalk coverage in Jaipur is sparse in OpenStreetMap, so "
        "the pedestrian factor measures what has been **mapped**, not ground "
        "conditions. An unmapped footpath is not proof that no footpath exists."
    )

    st.markdown("**Real data**")
    for item in real_items:
        st.markdown(f"- {item}")

    st.markdown("**Simulated data**")
    for item in simulated_items:
        st.markdown(f"- {item}")

    st.markdown("**Limitations**")
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
    for item in limitations:
        st.markdown(f"- {item}")

st.caption(
    "Map data © OpenStreetMap contributors (ODbL). Indicator data in this "
    "prototype is simulated. ClearWay is a hackathon prototype, not safety "
    "advice."
)
