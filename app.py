"""Read-only watchlist dashboard; the worker updates Google Sheets separately."""

import colorsys
import hmac
import time
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

import pydeck as pdk
import streamlit as st
from gspread.exceptions import APIError

from dashboard_map import course_arrow_icon, map_view, prepare_trace, track_segments
from gsheet_handler import GSheet_Handler, get_all_ships

SGT = ZoneInfo("Asia/Singapore")
PALETTE = ((20, 147, 164), (54, 104, 184), (218, 95, 73), (123, 84, 183),
           (38, 151, 103), (193, 117, 35), (182, 77, 133), (88, 115, 50))

st.set_page_config(page_title="Vessel Tracker", page_icon="🚢", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown("""
<style>
.block-container {max-width: 1550px; padding-top: 1.5rem; padding-bottom: 3rem;}
.eyebrow {font-size: .76rem; letter-spacing: .13em; font-weight: 700; color: #168a87;}
.map-legend {font-size: .84rem; color: #516174; margin: .4rem 0 .8rem;}
.map-legend span {display: inline-block; margin-right: 1.1rem; white-space: nowrap;}
.map-legend i {display: inline-block; width: 11px; height: 11px;
               border-radius: 50%; margin-right: .32rem;}
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def sheet_handler():
    try:
        return GSheet_Handler(use_streamlit=True)
    except (KeyError, FileNotFoundError):
        # Local development may use secrets.env instead of Streamlit secrets.
        return GSheet_Handler(use_streamlit=False)


@st.cache_data(ttl=300, show_spinner=False)
def read_watchlist(_handler):
    for attempt in range(3):
        try:
            return get_all_ships(_handler.sheet)[0]
        except APIError as exc:
            if getattr(exc.response, "status_code", None) != 503 or attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def label(value, default="—"):
    result = str(value).strip() if value is not None else ""
    return result or default


def format_time(value):
    return value.astimezone(SGT).strftime("%d %b %Y, %H:%M SGT") if value else "Unknown"


def vessel_color(index):
    if index < len(PALETTE):
        rgb = PALETTE[index]
    else:
        rgb = tuple(round(channel * 255) for channel in
                    colorsys.hsv_to_rgb((index * 0.618034) % 1, .64, .74))
    return [*rgb, 240], "#{:02x}{:02x}{:02x}".format(*rgb)


def vessel_rows(records):
    """Show every watchlist row and report unusable or duplicate AIS points."""
    vessels, issues = [], []
    for index, (row_number, record) in enumerate(records):
        name = label(record.get("Name"), f"Unnamed row {row_number}")
        raw = record.get("Coord_Trace")
        points = prepare_trace(raw)
        if not str(record.get("Name") or "").strip():
            issues.append(f"Row {row_number}: missing vessel name")
        if not isinstance(raw, list) or not raw:
            issues.append(f"Row {row_number} · {name}: no position trace saved")
        elif not points:
            issues.append(f"Row {row_number} · {name}: no valid timestamped positions")
        elif len(raw) > len(points):
            issues.append(f"Row {row_number} · {name}: {len(raw) - len(points)} "
                          "invalid or repeated position(s) omitted")
        rgba, hex_color = vessel_color(index)
        vessels.append({"row": row_number, "name": name, "record": record,
                        "points": points, "rgba": rgba, "hex": hex_color})
    return vessels, issues


def direction_for(point):
    if point["heading"] is not None:
        return point["heading"], "Reported heading"
    if point["course"] is not None and point["speed"] is not None and point["speed"] >= 1:
        return point["course"], "Course over ground"
    return None, "Direction unavailable"


def point_detail(point):
    parts = [format_time(point["time"]), f"{point['lat']:.5f}°, {point['lon']:.5f}°"]
    if point["draught"] is not None:
        parts.append(f"Reported draught: {point['draught']:g} m")
    if point["volume"] is not None:
        parts.append(f"Reported volume: {point['volume']:g} (source units)")
    return "<br/>".join(parts)


def draw_map(vessels, focused, focus_latest, show_waypoints):
    paths, recent_paths, first_points = [], [], []
    waypoints, latest_markers, arrows = [], [], []
    total_breaks = 0
    for vessel in vessels:
        points = vessel["points"]
        if not points:
            continue
        name, color = escape(vessel["name"]), vessel["rgba"]
        segments, breaks = track_segments(points)
        total_breaks += breaks
        for segment in segments:
            paths.append({"path": [[p["lon"], p["lat"]] for p in segment],
                          "color": color, "name": name, "detail": "Recorded sailing track"})
        recent, _ = track_segments(points[-25:])
        for segment in recent:
            recent_paths.append({"path": [[p["lon"], p["lat"]] for p in segment],
                                 "color": [*color[:3], 255], "name": name,
                                 "detail": "Recent sailing track"})
        first, last = points[0], points[-1]
        first_points.append({"coordinates": [first["lon"], first["lat"]],
                             "color": color, "name": f"{name} · first saved position",
                             "detail": point_detail(first)})
        latest_markers.append({"coordinates": [last["lon"], last["lat"]],
                               "color": color, "name": f"{name} · last reported position",
                               "detail": point_detail(last)})
        bearing, source = direction_for(last)
        if bearing is not None:
            arrows.append({"coordinates": [last["lon"], last["lat"]],
                           "icon": {"url": course_arrow_icon(bearing, vessel["hex"]),
                                    "width": 64, "height": 64, "anchorX": 32, "anchorY": 32},
                           "name": f"{name} · {source}",
                           "detail": f"{bearing:.0f}° · {point_detail(last)}"})
        if show_waypoints:
            step = max(1, (len(points) - 1) // 59)
            for point in points[1:-1:step]:
                waypoints.append({"coordinates": [point["lon"], point["lat"]],
                                  "color": color, "name": name,
                                  "detail": point_detail(point)})

    layers = []
    if paths:
        layers.append(pdk.Layer("PathLayer", data=paths, get_path="path",
                                get_color="color", get_width=4, width_min_pixels=3,
                                pickable=True))
    if recent_paths:
        layers.append(pdk.Layer("PathLayer", data=recent_paths, get_path="path",
                                get_color="color", get_width=6, width_min_pixels=4,
                                pickable=True))
    if show_waypoints and waypoints:
        layers.append(pdk.Layer("ScatterplotLayer", data=waypoints,
                                get_position="coordinates", get_fill_color="color",
                                get_radius=15, radius_min_pixels=4, pickable=True))
    layers.append(pdk.Layer("ScatterplotLayer", data=first_points,
                            get_position="coordinates", get_fill_color="color",
                            get_line_color=[255, 255, 255], line_width_min_pixels=2,
                            get_radius=26, radius_min_pixels=8, pickable=True))
    layers.append(pdk.Layer("ScatterplotLayer", data=latest_markers,
                            get_position="coordinates", get_fill_color="color",
                            get_line_color=[255, 255, 255], line_width_min_pixels=3,
                            get_radius=85, radius_min_pixels=17, pickable=True))
    if arrows:
        layers.append(pdk.Layer("IconLayer", data=arrows, get_position="coordinates",
                                get_icon="icon", get_size=43, pickable=True))

    visible = [point for vessel in vessels for point in vessel["points"]]
    camera_points = focused["points"] if focus_latest and focused["points"] else visible
    camera = map_view(camera_points, focus_latest and bool(focused["points"]))
    st.pydeck_chart(pdk.Deck(
        layers=layers, initial_view_state=pdk.ViewState(**camera, pitch=0, bearing=0),
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"html": "<b>{name}</b><br/>{detail}",
                 "style": {"backgroundColor": "#102a43", "color": "#ffffff"}},
    ), width="stretch", height=600)
    return total_breaks


# This optional password complements private hosting and invited viewers.
password = st.secrets.get("DASHBOARD_PASSWORD", "")
if password:
    supplied = st.text_input("Dashboard password", type="password")
    if not hmac.compare_digest(supplied, password):
        st.stop()

header, refresh = st.columns([6, 1], vertical_alignment="bottom")
with header:
    st.markdown('<div class="eyebrow">PERTAMINA · VESSEL INTELLIGENCE</div>',
                unsafe_allow_html=True)
    st.title("Vessel Tracker")
    st.caption("Compare saved routes, latest AIS positions, and possible diversions.")
with refresh:
    if st.button("↻ Refresh data", width="stretch",
                 help="Fetch the latest watchlist values from Google Sheets"):
        read_watchlist.clear()
        st.rerun()

try:
    with st.spinner("Loading the Google Sheets watchlist..."):
        all_vessels, issues = vessel_rows(read_watchlist(sheet_handler()))
except Exception:
    st.error("Could not read the watchlist. Check spreadsheet sharing and dashboard secrets.")
    st.stop()

if not all_vessels:
    st.info("The watchlist contains no vessel rows yet.")
    st.stop()

by_row = {v["row"]: v for v in all_vessels}
with st.sidebar:
    st.subheader("Vessels")
    st.caption("Choose the tracks to compare on the map.")
    chosen_rows = st.multiselect(
        "Displayed vessels", options=list(by_row), default=list(by_row),
        format_func=lambda row: f"{by_row[row]['name']} · IMO "
                                f"{label(by_row[row]['record'].get('IMO'))}",
    )
    st.divider()
    if issues:
        with st.expander(f"Data issues · {len(issues)}"):
            for issue in issues:
                st.write(issue)
    else:
        st.caption("All vessel rows have usable position traces.")

selected = [by_row[row] for row in chosen_rows]
flagged_count = sum(str(v["record"].get("Diversion_Flag", "")).strip().upper() == "TRUE"
                    for v in all_vessels)
metrics = st.columns(4)
metrics[0].metric("Watchlist vessels", len(all_vessels))
metrics[1].metric("Displayed", len(selected))
metrics[2].metric("Possible diversions", flagged_count)
metrics[3].metric("Data issues", len(issues))

if not selected:
    st.info("Select at least one vessel in the sidebar to display its route.")
    st.stop()

focused_row = st.selectbox(
    "Details and map focus", chosen_rows,
    format_func=lambda row: f"{by_row[row]['name']} · IMO "
                            f"{label(by_row[row]['record'].get('IMO'))}",
)
focused = by_row[focused_row]
ship = focused["record"]
st.subheader(focused["name"])
st.caption(
    f"IMO {label(ship.get('IMO'))} · Kpler ID {label(ship.get('KPLER_ID'))} · "
    f"Cargo: {label(ship.get('Cargo'), 'Unknown')} · "
    f"Planned destination: {label(ship.get('Original_Dest'), 'Unknown')} · "
    f"Voyage start: {label(ship.get('Departure'), 'Unknown')}"
)
if str(ship.get("Diversion_Flag", "")).strip().upper() == "TRUE":
    st.warning("Possible diversion flagged. Verify in Kpler before changing a balance.")

points = focused["points"]
if points:
    latest = points[-1]
    age = datetime.now(timezone.utc) - latest["time"]
    if age > timedelta(hours=48):
        st.warning(f"The last position for {focused['name']} is about "
                   f"{age.total_seconds() / 3600:.0f} hours old.")
    bearing, source = direction_for(latest)
    summary = st.columns(4)
    summary[0].metric("Last reported (SGT)", format_time(latest["time"]))
    summary[1].metric("Speed", f"{latest['speed']:.1f} kn" if latest["speed"] is not None
                      else "Unknown")
    summary[2].metric(source, f"{bearing:.0f}°" if bearing is not None else "Unavailable")
    summary[3].metric("Saved positions", len(points))
else:
    st.info("This vessel has no valid saved trace yet. It will appear on the map after "
            "the monitoring worker saves its positions.")

focus_latest, show_waypoints = st.columns(2)
with focus_latest:
    zoom_to_latest = st.toggle("Focus on this vessel's last report", value=False)
with show_waypoints:
    waypoint_markers = st.toggle("Show sampled position markers", value=False)

legend = "".join(
    f'<span><i style="background:{v["hex"]}"></i>{escape(v["name"])}</span>'
    for v in selected
)
st.markdown(f'<div class="map-legend">{legend}</div>', unsafe_allow_html=True)

mapped = [v for v in selected if v["points"]]
if mapped:
    breaks = draw_map(mapped, focused, zoom_to_latest, waypoint_markers)
    st.caption("Lines join saved AIS reports; the route between reports is unknown. "
               f"{breaks} gap{'s' if breaks != 1 else ''} left open for long "
               "reporting intervals, implausible jumps, or dateline crossings. "
               "Colored arrows mark the last reported position and show heading "
               "when available, or course over ground while moving. Hover over "
               "the map to inspect reports. This is not a live vessel feed.")
else:
    st.info("None of the selected vessels has a valid position trace yet.")

with st.expander("Selected vessel details", expanded=False):
    rows = []
    for vessel in selected:
        record = vessel["record"]
        last = vessel["points"][-1] if vessel["points"] else None
        bearing, source = direction_for(last) if last else (None, "Unavailable")
        rows.append({
            "Vessel": vessel["name"], "Sheet row": vessel["row"],
            "IMO": label(record.get("IMO")), "Kpler ID": label(record.get("KPLER_ID")),
            "Departure": label(record.get("Departure")),
            "Destination": label(record.get("Original_Dest")),
            "Cargo": label(record.get("Cargo")),
            "Diversion flag": label(record.get("Diversion_Flag")),
            "Saved positions": len(vessel["points"]),
            "Last report (SGT)": format_time(last["time"]) if last else "Unknown",
            "Last latitude": round(last["lat"], 5) if last else None,
            "Last longitude": round(last["lon"], 5) if last else None,
            "Speed (kn)": last["speed"] if last else None,
            "Direction (°)": bearing, "Direction source": source,
            "Draught (m)": last["draught"] if last else None,
            "Volume (source units)": last["volume"] if last else None,
            "Sheet updated": label(record.get("Last_Updated")),
        })
    st.dataframe(rows, hide_index=True, width="stretch")
