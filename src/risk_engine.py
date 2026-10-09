def calculate_route_risk(
    lighting_risk,
    pedestrian_risk,
    hazard_risk,
    isolation_risk,
):
    """Calculate an illustrative route risk score from 0 to 100."""

    factors = {
        "lighting": lighting_risk,
        "pedestrian": pedestrian_risk,
        "hazard": hazard_risk,
        "isolation": isolation_risk,
    }

    for name, value in factors.items():
        if not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError(
                f"{name} risk must be a number from 0 to 100"
            )

    weights = {
        "lighting": 0.35,
        "pedestrian": 0.25,
        "hazard": 0.30,
        "isolation": 0.10,
    }

    score = sum(
        factors[name] * weights[name]
        for name in weights
    )

    return round(score, 1)


def describe_risk(score):
    """Describe the model estimate, not actual route safety."""

    if not 0 <= score <= 100:
        raise ValueError("Score must be between 0 and 100")

    if score < 30:
        return "Lower estimated risk"
    elif score < 60:
        return "Moderate estimated risk"
    else:
        return "Higher estimated risk"
