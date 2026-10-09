"""Folium map rendering for ClearWay route comparison."""

import folium

ROUTE_COLOURS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]
ORIGIN_COLOUR = "green"
DESTINATION_COLOUR = "darkred"

FALLBACK_CENTRE = [26.9190, 75.7870]  # central Jaipur
FALLBACK_ZOOM = 12


def _route_label(route_id):
    return "Route " + route_id.split("_")[-1].upper()


def _popup_html(route, label, colour):
    score = route.get("risk_score")
    score_text = (
        f"{score:.1f}/100 estimated ({route.get('risk_label') or 'n/a'})"
        if score is not None
        else "No score - insufficient indicator data"
    )
    distance = route.get("distance_m")
    duration = route.get("duration_s")
    return f"""
    <div style="font-family: sans-serif; min-width: 210px;">
        <h4 style="margin:0 0 6px 0; color:{colour};">{label}</h4>
        <div><b>Distance:</b> {distance / 1000.0:.2f} km</div>
        <div><b>Walking time:</b> {round((duration or 0) / 60.0)} min</div>
        <div><b>Estimated risk:</b> {score_text}</div>
        <div style="margin-top:6px; font-size:11px; color:#555;">
            Simulated indicator data. Not a guarantee of safety.
        </div>
    </div>
    """


def build_route_map(origin, destination, routes):
    """Build a Folium map with origin, destination and every route geometry.

    Never raises on bad input: a degraded map is returned instead so the rest
    of the page can still render.
    """
    centre = FALLBACK_CENTRE
    zoom = FALLBACK_ZOOM

    origin_point = None
    if origin and origin.get("lat") is not None:
        origin_point = [float(origin["lat"]), float(origin["lon"])]
        centre = origin_point

    destination_point = None
    if destination and destination.get("lat") is not None:
        destination_point = [float(destination["lat"]), float(destination["lon"])]
        if origin_point is None:
            centre = destination_point

    route_map = folium.Map(
        location=centre,
        zoom_start=zoom,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    if origin_point:
        folium.Marker(
            origin_point,
            tooltip="Start",
            popup=f"<b>Start</b><br>{origin.get('display_name', '')}",
            icon=folium.Icon(color="green", icon="play", prefix="fa"),
        ).add_to(route_map)

    if destination_point:
        folium.Marker(
            destination_point,
            tooltip="Destination",
            popup=f"<b>Destination</b><br>{destination.get('display_name', '')}",
            icon=folium.Icon(color="darkred", icon="flag-checkered", prefix="fa"),
        ).add_to(route_map)

    points = [p for p in (origin_point, destination_point) if p]

    for index, route in enumerate(routes or []):
        geometry = route.get("geometry") or []
        if len(geometry) < 2:
            continue

        colour = ROUTE_COLOURS[index % len(ROUTE_COLOURS)]
        label = _route_label(route["route_id"])

        folium.PolyLine(
            geometry,
            color=colour,
            weight=6,
            opacity=0.85,
            tooltip=f"{label} - {route['distance_m'] / 1000.0:.2f} km",
            popup=folium.Popup(_popup_html(route, label, colour), max_width=300),
        ).add_to(route_map)

        # A hollow midpoint marker so each route is identifiable on the map.
        midpoint = geometry[len(geometry) // 2]
        folium.CircleMarker(
            midpoint,
            radius=6,
            color=colour,
            fill=True,
            fill_color=colour,
            fill_opacity=1.0,
            tooltip=label,
        ).add_to(route_map)

        points.extend(geometry)

    if len(points) >= 2:
        route_map.fit_bounds(points, padding=(24, 24))

    _add_legend(route_map, routes or [])
    return route_map


def _add_legend(route_map, routes):
    """A simple HTML legend describing what each colour means."""
    rows = []
    for index, route in enumerate(routes):
        colour = ROUTE_COLOURS[index % len(ROUTE_COLOURS)]
        label = _route_label(route["route_id"])
        distance = route.get("distance_m")
        score = route.get("risk_score")
        distance_text = f"{distance / 1000.0:.2f} km" if distance is not None else "n/a"
        score_text = (
            f"score {score:.1f}/100" if score is not None else "no score"
        )
        rows.append(
            f'<div style="margin:2px 0;">'
            f'<span style="display:inline-block;width:14px;height:4px;'
            f'background:{colour};margin-right:6px;"></span>'
            f"{label} &mdash; {distance_text}, {score_text}</div>"
        )

    if not rows:
        rows.append(
            '<div style="margin:2px 0;">No route geometry was returned.</div>'
        )

    rows.append(
        '<div style="margin:6px 0 0 0;font-size:11px;color:#555;">'
        'Base map &copy; OpenStreetMap contributors. Indicator data is simulated.'
        "</div>"
    )

    legend_html = (
        '<div style="position:fixed;bottom:18px;left:18px;z-index:9999;'
        "background:rgba(255,255,255,0.94);padding:10px 12px;border-radius:6px;"
        "border:1px solid #bbb;font-family:sans-serif;font-size:12px;"
        'max-width:280px;box-shadow:0 1px 4px rgba(0,0,0,0.25);">'
        '<div style="font-weight:bold;margin-bottom:4px;">Routes</div>'
        + "".join(rows)
        + "</div>"
    )

    route_map.get_root().html.add_child(folium.Element(legend_html))
