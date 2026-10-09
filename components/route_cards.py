"""Route comparison cards rendered in Streamlit."""

import streamlit as st

from src.explanations import comparison_sentences, format_distance, format_minutes

RISK_COLOURS = {
    "Lower estimated risk": "#2e7d32",
    "Moderate estimated risk": "#ef6c00",
    "Higher estimated risk": "#c62828",
}


def _route_letter(route_id):
    return route_id.split("_")[-1].upper()


def render_route_cards(routes, assessments, indicators_by_route, selected_id=None):
    """Render one card per route with distance, time, score and explanations."""
    if not routes:
        st.info("No routes to display.")
        return

    for route in routes:
        route_id = route["route_id"]
        assessment = assessments.get(route_id, {})
        indicators = indicators_by_route.get(route_id, {})
        letter = _route_letter(route_id)

        with st.container(border=True):
            header_cols = st.columns([3, 2, 2])

            with header_cols[0]:
                st.markdown(f"### Route {letter}")

            with header_cols[1]:
                distance = route.get("distance_m")
                st.metric(
                    "Distance",
                    format_distance(distance),
                )

            with header_cols[2]:
                duration = route.get("duration_s")
                st.metric(
                    "Walking time",
                    format_minutes(duration),
                )

            score = assessment.get("score")
            coverage = assessment.get("coverage", 0.0)

            if score is None:
                st.warning(
                    "No risk score was calculated. "
                    + (assessment.get("withheld_reason") or "")
                )
            else:
                label = assessment.get("label") or ""
                colour = RISK_COLOURS.get(label, "#555555")
                provisional = " (provisional)" if assessment.get("provisional") else ""
                st.markdown(
                    f"<div style='display:flex;align-items:baseline;gap:10px;'>"
                    f"<span style='font-size:30px;font-weight:700;color:{colour};'>"
                    f"{score:.1f}</span>"
                    f"<span style='color:{colour};font-weight:600;'>{label}</span>"
                    f"<span style='color:#777;'>out of 100{provisional}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.progress(min(max(score / 100.0, 0.0), 1.0))

            st.caption(
                f"Data coverage: {coverage:.0%} of the total scoring weight — "
                f"{assessment.get('coverage_label', 'unknown')}."
            )

            with st.expander("Why this route received its assessment", expanded=False):
                for sentence in assessment.get("sentences", []):
                    st.markdown(f"- {sentence}")

            with st.expander("Indicator detail and data provenance", expanded=False):
                for factor, detail in assessment.get("factors", {}).items():
                    value = detail.get("value")
                    status = detail.get("status", "missing")

                    if value is None:
                        st.markdown(
                            f"- **{detail['label']}** — data unavailable. "
                            f"{detail.get('detail') or ''}"
                        )
                    else:
                        st.markdown(
                            f"- **{detail['label']}** — {value:.0f}/100 "
                            f"({status}). Model weight "
                            f"{detail.get('weight', 0):.0%}. "
                            f"{detail.get('detail') or ''}"
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
                st.caption(f"Routing note: {route['provider_note']}")


def render_comparison(routes, assessments):
    """Render the route-versus-route explanation block."""
    if len(routes) < 2:
        return

    with st.expander("How these routes compare", expanded=True):
        sentences = comparison_sentences(
            routes, [assessments[route["route_id"]] for route in routes]
        )
        for sentence in sentences:
            st.markdown(f"- {sentence}")
