"""Turn raw scoring output into sentences a user can act on.

Every sentence is derived from an actual value in the assessment dict, so a
missing factor always produces an explicit "unavailable" statement rather than
silence or a fabricated value.
"""

from src.risk_engine import FACTOR_LABELS

MISSING_EXPLANATION = (
    "{label} data unavailable for this route. Nothing was assumed, and this "
    "factor was excluded from the score."
)

SIMULATED_EXPLANATION = (
    "{label}: {value}/100 estimated risk, from {status_word} data. {detail}"
)

STATUS_WORD = {
    "observed": "real OpenStreetMap",
    "simulated": "simulated sample",
    "estimated": "estimated",
    "missing": "unavailable",
}

# Appended to every comparison so the honest caveat can never be dropped on an
# early-return path.
SAFETY_DISCLAIMER = (
    "Scores are illustrative model estimates. Some indicators are real "
    "OpenStreetMap data and some are simulated, and coverage is partial. "
    "A lower score does not mean a route is safe."
)


def format_minutes(duration_s):
    """Format seconds as whole minutes, tolerating None."""
    if duration_s is None:
        return "unknown"
    minutes = round(float(duration_s) / 60.0)
    if minutes < 1:
        return "under a minute"
    return f"{minutes} min"


def format_distance(distance_m):
    """Format metres as km with two decimals, tolerating None."""
    if distance_m is None:
        return "unknown"
    return f"{float(distance_m) / 1000.0:.2f} km"


def factor_sentences(assessment):
    """One sentence per scoring factor, in weight order."""
    sentences = []
    for factor, detail in assessment["factors"].items():
        label = FACTOR_LABELS[factor]

        if detail["value"] is None:
            sentences.append(MISSING_EXPLANATION.format(label=label))
            continue

        sentences.append(
            SIMULATED_EXPLANATION.format(
                label=label,
                value=f"{detail['value']:.0f}",
                status_word=STATUS_WORD.get(detail["status"], detail["status"]),
                detail=detail.get("detail") or "No further detail available.",
            )
        )

    return sentences


def route_sentences(route, assessment):
    """The list of statements shown on one route card."""
    sentences = [
        f"Route {route['route_id'].split('_')[-1].upper()}: "
        f"{format_distance(route['distance_m'])} on foot, about "
        f"{format_minutes(route['duration_s'])}, returned live by the "
        f"{route.get('provider', 'routing service')}."
    ]

    if assessment["score"] is not None:
        sentences.append(
            f"Illustrative risk score {assessment['score']:.1f}/100 - "
            f"{assessment['label']}. This is a model estimate, not a "
            "guarantee of safety."
        )
        sentences.append(
            f"Data coverage {assessment['coverage']:.0%} of the total scoring "
            f"weight ({assessment['coverage_label']}), so this score is "
            "provisional."
            if assessment["provisional"]
            else f"Data coverage 100% of the scoring weight."
        )
    else:
        sentences.append(
            "No risk score was calculated: " + (assessment["withheld_reason"] or "")
        )

    sentences.extend(factor_sentences(assessment))

    if route.get("provider_note"):
        sentences.append(route["provider_note"])

    return sentences


def comparison_sentences(routes, assessments):
    """Explain the trade-off between the routes, using only real numbers."""
    if len(routes) < 2:
        return ["Only one route was returned, so there is nothing to compare."]

    scored = [
        (route, assessment)
        for route, assessment in zip(routes, assessments)
        if assessment["score"] is not None
    ]

    if len(scored) < 2:
        return [
            "Fewer than two routes received a score, so a comparison of scores "
            "is not available. Distance and duration are still compared below.",
            SAFETY_DISCLAIMER,
        ]

    sentences = []

    def by_duration(item):
        return item[0]["duration_s"]

    fastest = min(scored, key=by_duration)
    lowest_score = min(scored, key=lambda item: item[1]["score"])

    if fastest is lowest_score:
        sentences.append(
            f"Route {fastest[0]['route_id'].split('_')[-1].upper()} is both the "
            f"quickest ({format_minutes(fastest[0]['duration_s'])}) and has the "
            "lowest estimated risk score, though shorter routes are not "
            "automatically safer."
        )
    else:
        extra_minutes = round(
            (lowest_score[0]["duration_s"] - fastest[0]["duration_s"]) / 60.0
        )
        score_gap = fastest[1]["score"] - lowest_score[1]["score"]
        sentences.append(
            f"Route {fastest[0]['route_id'].split('_')[-1].upper()} is Quickest "
            f"({format_minutes(fastest[0]['duration_s'])}, estimated score "
            f"{fastest[1]['score']:.0f}/100)."
        )
        sentences.append(
            f"Route {lowest_score[0]['route_id'].split('_')[-1].upper()} takes "
            f"about {extra_minutes} min longer ({format_minutes(lowest_score[0]['duration_s'])}) "
            f"and has an estimated score {score_gap:.0f} points lower "
            f"({lowest_score[1]['score']:.0f}/100), based on the indicators that "
            "were available."
        )

    scored_ids = {route["route_id"] for route, _ in scored}
    unscored = [route["route_id"] for route in routes if route["route_id"] not in scored_ids]
    if unscored:
        sentences.append(
            "No score was calculated for route "
            + ", ".join(rid.split("_")[-1].upper() for rid in unscored)
            + ", so it cannot be placed in this comparison."
        )

    sentences.append(SAFETY_DISCLAIMER)

    return sentences
