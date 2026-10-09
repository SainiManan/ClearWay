"""Route comparison cards rendered in Streamlit.

Visual structure is driven by classes in assets/theme.css so the styling can
change without touching this logic.
"""

import streamlit as st

from src.explanations import comparison_sentences, format_distance, format_minutes

RISK_COLOURS = {
    "Lower estimated risk": "var(--cw-low)",
    "Moderate estimated risk": "var(--cw-moderate)",
    "Higher estimated risk": "var(--cw-high)",
}

FALLBACK_COLOUR = "var(--cw-muted)"

STATUS_TAG = {
    "observed": "real data",
    "simulated": "simulated",
    "estimated": "estimated",
    "missing": "unavailable",
}


def _route_letter(route_id):
    return route_id.split("_")[-1].upper()


def render_route_selector(routes, selected_id):
    """A native control that mirrors map clicks.

    Clicking a 5px line on a touchscreen is unreliable, so the map click and
    this control both set the same selection. Returns the chosen route id.
    """
    if not routes:
        return selected_id

    labels = [f"Route {_route_letter(route['route_id'])}" for route in routes]
    index = 0
    for position, route in enumerate(routes):
        if route["route_id"] == selected_id:
            index = position
            break

    choice = st.radio(
        "Focus a route",
        labels,
        index=index,
        horizontal=True,
        label_visibility="collapsed",
    )
    return routes[labels.index(choice)]["route_id"]


def _escape(text):
    """Minimal HTML escaping for values interpolated into markup."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _score_block(score, assessment):
    """Render the score, its colour chip and a coverage bar."""
    if score is None:
        return f"""
        <div class="cw-callout" style="margin:14px 0 0;">
            <span class="cw-callout-icon">🚫</span>
            <div class="cw-callout-body">
                No risk score was calculated.
                {_escape(assessment.get('withheld_reason') or '')}
            </div>
        </div>
        """

    label = assessment.get("label") or ""
    colour = RISK_COLOURS.get(label, FALLBACK_COLOUR)
    provisional = " (provisional)" if assessment.get("provisional") else ""
    coverage = assessment.get("coverage", 0.0)

    return f"""
    <div class="cw-score">
        <span class="cw-score-value" style="color:{colour};">{score:.1f}</span>
        <span class="cw-score-chip" style="color:{colour};">{_escape(label)}</span>
        <span class="cw-score-note">out of 100{provisional}</span>
    </div>
    <div class="cw-score-bar">
        <div class="cw-score-bar-fill"
             style="width:{min(max(score, 0.0), 100.0):.1f}%;background:{colour};"></div>
    </div>
    <div style="font-size:0.79rem;color:var(--cw-muted);">
        Data coverage {coverage:.0%} of the total scoring weight —
        {_escape(assessment.get('coverage_label', 'unknown'))}.
    </div>
    """


def _factor_rows(assessment):
    """One row per scoring factor, with provenance and a status tag."""
    rows = []
    for detail in assessment.get("factors", {}).values():
        value = detail.get("value")
        label = _escape(detail.get("label", ""))
        tag = STATUS_TAG.get(detail.get("status"), "unavailable")

        if value is None:
            right = (
                f'<div class="cw-factor-right" style="color:var(--cw-muted);">'
                f"unavailable</div>"
            )
            body = (
                f'<div class="cw-factor-name">{label}</div>'
                f'<div class="cw-factor-detail">'
                f"{_escape(detail.get('detail') or '')}</div>"
            )
        else:
            colour = (
                "var(--cw-low)"
                if value < 30
                else "var(--cw-moderate)" if value < 60 else "var(--cw-high)"
            )
            right = (
                f'<div class="cw-factor-right" style="color:{colour};">'
                f"{value:.0f}/100</div>"
                f'<div class="cw-factor-tag">{tag}</div>'
            )
            body = (
                f'<div class="cw-factor-name">{label}</div>'
                f'<div class="cw-factor-detail">'
                f"Model weight {detail.get('weight', 0):.0%}. "
                f"{_escape(detail.get('detail') or '')}</div>"
            )

        rows.append(
            f'<div class="cw-factor"><div>{body}</div>{right}</div>'
        )

    return "".join(rows)


def render_route_cards(routes, assessments, indicators_by_route, selected_id=None):
    """Render one card per route with distance, time, score and explanations."""
    if not routes:
        st.info("No routes to display.")
        return

    for route in routes:
        route_id = route["route_id"]
        assessment = assessments.get(route_id, {})
        letter = _route_letter(route_id)
        score = assessment.get("score")
        is_selected = route_id == selected_id

        with st.container(border=True):
            if is_selected:
                st.markdown(
                    '<style>[data-testid="stVerticalBlock"] > '
                    '[data-testid="stContainer"][border="true"]:has(.cw-selected-badge)'
                    "{border-color:var(--cw-accent);box-shadow:0 0 0 1px "
                    "rgba(245,165,36,0.35);}</style>",
                    unsafe_allow_html=True,
                )
                st.markdown("<span class='cw-selected-badge'></span>",
                            unsafe_allow_html=True)

            # Header: route letter badge plus the selected marker.
            selected_note = (
                '<span class="cw-badge cw-badge-accent" style="margin-left:auto;">'
                "Focused</span>"
                if is_selected
                else ""
            )
            st.markdown(
                f"""
                <div class="cw-card-head">
                    <span class="cw-route-letter">{letter}</span>
                    <span>
                        <span class="cw-route-title">Route {letter}</span><br>
                        <span class="cw-route-sub">{_escape(
                            route.get("provider", "routing service")
                        )}</span>
                    </span>
                    {selected_note}
                </div>
                """,
                unsafe_allow_html=True,
            )

            metric_cols = st.columns(2)
            with metric_cols[0]:
                st.metric("Distance", format_distance(route.get("distance_m")))
            with metric_cols[1]:
                st.metric("Walking time", format_minutes(route.get("duration_s")))

            st.markdown(_score_block(score, assessment), unsafe_allow_html=True)

            with st.expander("Why this route received its assessment", expanded=False):
                st.markdown(
                    '<ul class="cw-list">'
                    + "".join(
                        f"<li>{_escape(sentence)}</li>"
                        for sentence in assessment.get("sentences", [])
                    )
                    + "</ul>",
                    unsafe_allow_html=True,
                )

            with st.expander("Indicator detail and data provenance", expanded=False):
                st.markdown(
                    _factor_rows(assessment), unsafe_allow_html=True
                )

                missing = assessment.get("missing_factors") or []
                if missing:
                    st.caption(
                        "Factors excluded from this score because no data was "
                        "available: "
                        + ", ".join(
                            assessment["factors"][f]["label"] for f in missing
                        )
                        + "."
                    )

            if route.get("provider_note"):
                st.caption(f"Routing note: {_escape(route['provider_note'])}")


def render_comparison(routes, assessments):
    """Render the route-versus-route explanation block."""
    if len(routes) < 2:
        return

    with st.expander("How these routes compare", expanded=True):
        sentences = comparison_sentences(
            routes, [assessments[route["route_id"]] for route in routes]
        )
        st.markdown(
            '<ul class="cw-list">'
            + "".join(f"<li>{_escape(sentence)}</li>" for sentence in sentences)
            + "</ul>",
            unsafe_allow_html=True,
        )
