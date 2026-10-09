"""Folium map rendering for ClearWay route comparison."""

import folium

from src.route_selection import build_route_tooltip

ROUTE_COLOURS = ["#4c8dff", "#ff6b6b", "#31c48d", "#a78bfa"]
ORIGIN_COLOUR = "#22c55e"
DESTINATION_COLOUR = "#f43f5e"

FALLBACK_CENTRE = [26.9190, 75.7870]  # central Jaipur
FALLBACK_ZOOM = 12

# The selected route is drawn heavier and fully opaque while the others are
# dimmed. Route colours stay stable so the legend keeps matching the cards.
SELECTED_WEIGHT = 9
UNSELECTED_WEIGHT = 5
UNSELECTED_OPACITY = 0.42

# A dark basemap keeps the map consistent with the app's night theme and makes
# the coloured routes far easier to read than the default light tiles.
# This Esri XYZ endpoint needs no API key. CartoDB dark_matter was tried first
# but now requires a key, which would have silently blanked the map.
DARK_TILES_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
    "World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)
TILE_ATTRIBUTION = (
    "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, OpenStreetMap "
    'contributors &copy; <a href="https://www.openstreetmap.org/copyright">'
    "OpenStreetMap</a> contributors"
)


def _route_label(route_id):
    from src.route_selection import route_letter

    return "Route " + route_letter(route_id)


def _popup_html(route, label, colour):
    score = route.get("risk_score")
    score_text = (
        f"{score:.1f}/100 estimated ({route.get('risk_label') or 'n/a'})"
        if score is not None
        else "No score — insufficient indicator data"
    )
    distance = route.get("distance_m")
    duration = route.get("duration_s")
    return f"""
    <div style="font-family:system-ui,sans-serif;min-width:210px;
                background:#131c2e;color:#e8eefb;padding:4px 2px;">
        <h4 style="margin:0 0 6px 0;color:{colour};">{label}</h4>
        <div><b>Distance:</b> {distance / 1000.0:.2f} km</div>
        <div><b>Walking time:</b> {round((duration or 0) / 60.0)} min</div>
        <div><b>Estimated risk:</b> {score_text}</div>
        <div style="margin-top:6px;font-size:11px;color:#94a3b8;">
            Indicator data is partly simulated. Not a guarantee of safety.
        </div>
    </div>
    """


def build_route_map(origin, destination, routes, selected_id=None):
    """Build a Folium map with origin, destination and every route geometry.

    ``selected_id`` highlights one route; the rest are dimmed. Every route
    layer carries a tooltip built by build_route_tooltip, which is how a click
    is turned back into a route id.

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
        tiles=None,
        control_scale=True,
    )

    folium.TileLayer(
        tiles=DARK_TILES_URL,
        attr=TILE_ATTRIBUTION,
        name="ClearWay dark basemap",
        overlay=False,
        control=False,
    ).add_to(route_map)

    if origin_point:
        folium.Marker(
            origin_point,
            tooltip="Start",
            popup=folium.Popup(
                "<b style='color:#22c55e'>Start</b><br>"
                f"<span style='color:#94a3b8'>{origin.get('display_name', '')}</span>",
                max_width=280,
            ),
            icon=folium.Icon(color="green", icon="play", prefix="fa"),
        ).add_to(route_map)

    if destination_point:
        folium.Marker(
            destination_point,
            tooltip="Destination",
            popup=folium.Popup(
                "<b style='color:#f43f5e'>Destination</b><br>"
                f"<span style='color:#94a3b8'>{destination.get('display_name', '')}</span>",
                max_width=280,
            ),
            icon=folium.Icon(color="darkred", icon="flag-checkered", prefix="fa"),
        ).add_to(route_map)

    points = [p for p in (origin_point, destination_point) if p]

    for index, route in enumerate(routes or []):
        geometry = route.get("geometry") or []
        if len(geometry) < 2:
            continue

        colour = ROUTE_COLOURS[index % len(ROUTE_COLOURS)]
        label = _route_label(route["route_id"])
        tooltip = build_route_tooltip(
            route["route_id"], route.get("distance_m")
        )
        is_selected = route["route_id"] == selected_id

        folium.PolyLine(
            geometry,
            color=colour,
            weight=SELECTED_WEIGHT if is_selected else UNSELECTED_WEIGHT,
            opacity=1.0 if is_selected else UNSELECTED_OPACITY,
            tooltip=tooltip,
            popup=folium.Popup(_popup_html(route, label, colour), max_width=300),
        ).add_to(route_map)

        # A midpoint chip so each route has a large, obvious click target.
        midpoint = geometry[len(geometry) // 2]
        folium.CircleMarker(
            midpoint,
            radius=10 if is_selected else 7,
            color=colour,
            fill=True,
            fill_color=colour,
            fill_opacity=1.0,
            weight=2,
            tooltip=tooltip,
            popup=folium.Popup(_popup_html(route, label, colour), max_width=300),
        ).add_to(route_map)

        points.extend(geometry)

    if len(points) >= 2:
        route_map.fit_bounds(points, padding=(24, 24))

    _add_legend(route_map, routes or [], selected_id)
    return route_map


def _add_legend(route_map, routes, selected_id=None):
    """A simple HTML legend describing what each colour means."""
    rows = []
    for index, route in enumerate(routes):
        colour = ROUTE_COLOURS[index % len(ROUTE_COLOURS)]
        label = _route_label(route["route_id"])
        distance = route.get("distance_m")
        score = route.get("risk_score")
        distance_text = f"{distance / 1000.0:.2f} km" if distance is not None else "n/a"
        score_text = f"score {score:.1f}/100" if score is not None else "no score"
        is_selected = route["route_id"] == selected_id
        weight = "700" if is_selected else "400"
        opacity = "1" if is_selected else "0.55"

        rows.append(
            '<div style="display:flex;align-items:center;gap:8px;'
            f'margin:5px 0;opacity:{opacity};">'
            '<span style="display:inline-block;width:16px;height:4px;border-radius:2px;'
            f'background:{colour};flex:0 0 16px;"></span>'
            f'<span style="color:#e8eefb;font-weight:{weight};">{label}</span>'
            f'<span style="color:#94a3b8;">{distance_text} · {score_text}</span>'
            "</div>"
        )

    if not rows:
        rows.append(
            '<div style="margin:5px 0;color:#94a3b8;">No route geometry was returned.</div>'
        )

    rows.append(
        '<div style="margin:9px 0 0 0;font-size:10.5px;line-height:1.5;color:#7c8ba4;">'
        "Click a route on the map to focus it.<br>"
        "Basemap © Esri, HERE, Garmin, OpenStreetMap contributors.<br>"
        "Indicator data is partly simulated. Not a safety guarantee."
        "</div>"
    )

    legend_html = (
        '<div style="position:fixed;bottom:16px;left:16px;z-index:9999;'
        "background:rgba(19,28,46,0.94);padding:11px 13px;border-radius:11px;"
        "border:1px solid #22304a;font-family:system-ui,sans-serif;font-size:12px;"
        "max-width:290px;box-shadow:0 8px 26px rgba(0,0,0,0.45);"
        'backdrop-filter:blur(6px);">'
        '<div style="font-weight:700;margin-bottom:6px;color:#e8eefb;">Routes</div>'
        + "".join(rows)
        + "</div>"
    )

    route_map.get_root().html.add_child(folium.Element(legend_html))
