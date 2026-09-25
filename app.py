"""Read-only vessel watchlist. The worker updates the Google Sheet separately."""

import hmac
from html import escape
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pydeck as pdk
import streamlit as st

from dashboard_map import course_arrow_icon, map_view, prepare_trace, track_segments
from gsheet_handler import GSheet_Handler, get_all_ships


SGT = ZoneInfo("Asia/Singapore")
st.set_page_config(page_title="Vessel movements", page_icon="🚢", layout="wide")
st.markdown("""
<style>
.block-container {max-width: 1520px; padding-top: 2rem; padding-bottom: 3rem;}
.eyebrow {font-size: .76rem; letter-spacing: .14em; font-weight: 700; color: #168a87;}
.map-legend {font-size: .86rem; color: #516174; margin: .5rem 0 .8rem 0;}
.map-legend span {display: inline-block; margin-right: 1.2rem; white-space: nowrap;}
.map-legend i {display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: .32rem;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def read_watchlist():
    sheet = GSheet_Handler(use_streamlit=True).sheet
    return [record for _, record in get_all_ships(sheet)[0]]


def format_time(value):
    if value is None:
        return "Unknown"
    return value.astimezone(SGT).strftime("%d %b %Y, %H:%M SGT")


def draw_map(points, vessel_name, focus_latest):
    safe_name = escape(vessel_name)
    segments, breaks = track_segments(points)
    recent_segments, _ = track_segments(points[-25:])
    layers = []
    if segments:
        layers.append(pdk.Layer(
            "PathLayer",
            data=[{"path": [[p["lon"], p["lat"]] for p in segment],
                   "name": "Recorded sailing track", "detail": "Lines join saved AIS positions"}
                  for segment in segments],
            get_path="path", get_color=[30, 101, 151, 215], get_width=4,
            width_min_pixels=3, pickable=True,
        ))
    if recent_segments:
        layers.append(pdk.Layer(
            "PathLayer",
            data=[{"path": [[p["lon"], p["lat"]] for p in segment],
                   "name": "Recent track", "detail": "Most recently saved positions"}
                  for segment in recent_segments],
            get_path="path", get_color=[20, 171, 151, 245], get_width=6,
            width_min_pixels=4, pickable=True,
        ))

    first, last = points[0], points[-1]
    layers.append(pdk.Layer(
        "ScatterplotLayer",
        data=[{"coordinates": [first["lon"], first["lat"]],
               "name": "First saved position", "detail": format_time(first["time"])}],
        get_position="coordinates", get_fill_color=[59, 111, 166, 255],
        get_line_color=[255, 255, 255], line_width_min_pixels=2,
        get_radius=30, radius_min_pixels=7, pickable=True,
    ))
    layers.append(pdk.Layer(
        "ScatterplotLayer",
        data=[{"coordinates": [last["lon"], last["lat"]],
               "name": f"{safe_name} · last reported position",
               "detail": format_time(last["time"])}],
        get_position="coordinates", get_fill_color=[20, 171, 151, 80],
        get_line_color=[9, 113, 106], line_width_min_pixels=2,
        get_radius=90, radius_min_pixels=17, pickable=True,
    ))
    # True heading (bow orientation) is preferred; moving COG is the fallback.
    if last["heading"] is not None:
        bearing, direction_label = last["heading"], "Reported heading"
    elif last["course"] is not None and last["speed"] is not None and last["speed"] >= 1:
        bearing, direction_label = last["course"], "Course over ground"
    else:
        bearing, direction_label = None, None
    if bearing is not None:
        layers.append(pdk.Layer(
            "IconLayer",
            data=[{"coordinates": [last["lon"], last["lat"]],
                   "icon": {"url": course_arrow_icon(bearing),
                            "width": 64, "height": 64, "anchorX": 32, "anchorY": 32},
                   "name": f"{safe_name} · {direction_label}",
                   "detail": f"{bearing:.0f}° · {format_time(last['time'])}"}],
            get_position="coordinates", get_icon="icon", get_size=43, pickable=True,
        ))

    view = pdk.ViewState(**map_view(points, focus_latest), pitch=0, bearing=0)
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"html": "<b>{name}</b><br/>{detail}",
                 "style": {"backgroundColor": "#102a43", "color": "#ffffff"}},
    )
    st.pydeck_chart(deck, width="stretch", height=560)
    return breaks


# The optional password supplements private hosting and invited viewers.
password = st.secrets.get("DASHBOARD_PASSWORD", "")
if password:
    supplied = st.text_input("Dashboard password", type="password")
    if not hmac.compare_digest(supplied, password):
        st.stop()

header, refresh = st.columns([6, 1], vertical_alignment="bottom")
with header:
    st.markdown('<div class="eyebrow">VESSEL INTELLIGENCE · WATCHLIST</div>', unsafe_allow_html=True)
    st.title("Vessel movements")
    st.caption("Track saved AIS positions and review possible diversions to or from Singapore.")
with refresh:
    if st.button("↻ Refresh", width="stretch", help="Read the latest values from the Google Sheet"):
        read_watchlist.clear()
        st.rerun()

try:
    records = read_watchlist()
except Exception:
    st.error("Could not read the watchlist. Check spreadsheet sharing and Streamlit secrets.")
    st.stop()

if not records:
    st.info("No vessels are in the watchlist yet.")
    st.stop()

flags = sum(str(r.get("Diversion_Flag", "")).strip().upper() == "TRUE" for r in records)
stats = st.columns(3)
stats[0].metric("Vessels tracked", len(records))
stats[1].metric("Possible diversions", flags)
stats[2].metric("Watchlist status", "Needs review" if flags else "No flags")

options = list(range(len(records)))
selected = st.selectbox(
    "Choose a vessel", options,
    format_func=lambda i: f"{records[i].get('Name') or 'Unknown'}  ·  IMO {records[i].get('IMO') or '—'}",
)
ship = records[selected]
name = str(ship.get("Name") or "Unknown vessel")
points = prepare_trace(ship.get("Coord_Trace"))
is_flagged = str(ship.get("Diversion_Flag", "")).strip().upper() == "TRUE"

st.subheader(name)
st.caption(
    f"IMO {ship.get('IMO') or '—'}   ·   {ship.get('Cargo') or 'Cargo unknown'}   ·   "
    f"Planned destination: {ship.get('Original_Dest') or 'Unknown'}   ·   "
    f"Voyage start: {ship.get('Departure') or 'Unknown'}"
)
if is_flagged:
    st.warning("Possible diversion flagged. Check this vessel in Kpler before changing a balance.")

if not points:
    st.info("No valid position trace has been saved for this vessel yet. Run the monitoring worker first.")
else:
    latest = points[-1]
    age = datetime.now(timezone.utc) - latest["time"]
    if age > timedelta(hours=48):
        st.warning(f"Last position is {age.total_seconds() / 3600:.0f} hours old. The marker shows the last report, not a live location.")
    details = st.columns(4)
    details[0].metric("Last reported (SGT)", latest["time"].astimezone(SGT).strftime("%d %b, %H:%M"))
    details[1].metric("Speed", f"{latest['speed']:.1f} kn" if latest["speed"] is not None else "Unknown")
    direction = latest["heading"] if latest["heading"] is not None else (
        latest["course"] if latest["course"] is not None and latest["speed"] is not None
        and latest["speed"] >= 1 else None
    )
    direction_label = "Reported heading" if latest["heading"] is not None else "Course over ground"
    details[2].metric(direction_label, f"{direction:.0f}°" if direction is not None else "Unavailable")
    details[3].metric("Recorded positions", len(points))

    focus_latest = st.toggle("Focus on latest position", value=False,
                             help="Switch between the complete saved route and the last reported location")
    st.markdown(
        '<div class="map-legend">'
        '<span><i style="background:#1e6597"></i>Recorded path</span>'
        '<span><i style="background:#14ab97"></i>Recent path and last report</span>'
        '<span><i style="background:#3b6fa6"></i>First saved point</span>'
        '</div>', unsafe_allow_html=True,
    )
    breaks = draw_map(points, name, focus_latest)
    st.caption(
        "Lines join saved AIS reports; positions between reports are unknown. "
        f"{breaks} gap{'s' if breaks != 1 else ''} left open for long reporting intervals, "
        "implausible jumps, or dateline crossings. The arrow shows reported heading "
        "when available, otherwise course over ground at speeds of at least 1 kn. "
        "Refresh the sheet to update the last reported position."
    )
    with st.expander("Latest coordinates and update details"):
        st.write(f"Last reported position: {latest['lat']:.5f}°, {latest['lon']:.5f}°")
        st.write(f"AIS report time: {format_time(latest['time'])}")
        st.write(f"Sheet last updated: {ship.get('Last_Updated') or 'Unknown'}")

with st.expander("View complete watchlist"):
    st.dataframe(
        [{key: r.get(key, "") for key in (
            "Name", "IMO", "Cargo", "Original_Dest", "Departure", "Last_Updated", "Diversion_Flag",
        )} for r in records], hide_index=True, width="stretch",
    )
