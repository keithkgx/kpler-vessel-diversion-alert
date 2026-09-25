"""Pure helpers for turning sampled AIS positions into a readable map."""

import base64
import math
from datetime import datetime, timezone


def parse_time(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def finite_number(value, low=None, high=None):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result) or (low is not None and result < low) or (high is not None and result > high):
        return None
    return result


def prepare_trace(raw):
    """Sort valid, timestamped AIS points; don't invent missing locations."""
    if not isinstance(raw, list):
        return []
    points = []
    for ping in raw:
        if not isinstance(ping, dict):
            continue
        geo = ping.get("geo") or {}
        if not isinstance(geo, dict):
            continue
        lat = finite_number(geo.get("lat"), -90, 90)
        lon = finite_number(geo.get("lon"), -180, 180)
        timestamp = parse_time(ping.get("receivedTime"))
        if lat is None or lon is None or timestamp is None:
            continue
        points.append({
            "lat": lat, "lon": lon, "time": timestamp,
            "course": finite_number(ping.get("course"), 0, 360),
            "heading": finite_number(ping.get("heading"), 0, 359.99),
            "speed": finite_number(ping.get("speed"), 0),
        })
    points.sort(key=lambda p: p["time"])
    # A repeated AIS message should not add a zero-length segment.
    return list({p["time"]: p for p in points}.values())


def distance_nm(a, b):
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp = p2 - p1
    dl = math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3440.065 * 2 * math.asin(min(1, math.sqrt(h)))


def track_segments(points, max_gap_hours=24):
    """Join plausible consecutive samples, leaving AIS gaps and jumps open."""
    if not points:
        return [], 0
    segments, current, breaks = [], [points[0]], 0
    for point in points[1:]:
        prev = current[-1]
        hours = (point["time"] - prev["time"]).total_seconds() / 3600
        # Dateline jumps need a split rather than a line across the entire map.
        connected = (0 < hours <= max_gap_hours
                     and abs(point["lon"] - prev["lon"]) <= 180
                     and distance_nm(prev, point) <= 40 * hours + 10)
        if not connected:
            if len(current) >= 2:
                segments.append(current)
            current = [point]
            breaks += 1
        else:
            current.append(point)
    if len(current) >= 2:
        segments.append(current)
    return segments, breaks


def map_view(points, focus_latest=False):
    """Return a camera covering observed samples, or one centered near the last ping."""
    last = points[-1]
    if focus_latest:
        return {"latitude": last["lat"], "longitude": last["lon"], "zoom": 8.5}
    lats = [p["lat"] for p in points]
    # Find the shortest longitude interval; ordinary averages fail near the dateline.
    circle = sorted(p["lon"] % 360 for p in points)
    gaps = [((circle[(i + 1) % len(circle)] - circle[i]) % 360) for i in range(len(circle))]
    cut = gaps.index(max(gaps))
    start = circle[(cut + 1) % len(circle)]
    span_lon = (circle[cut] - start) % 360
    center = ((start + span_lon / 2 + 180) % 360) - 180
    span = max(span_lon, (max(lats) - min(lats)) * 1.7, 0.15)
    zoom = max(1.0, min(11.0, 8.0 - math.log2(span)))
    return {"latitude": (min(lats) + max(lats)) / 2, "longitude": center, "zoom": zoom}


def course_arrow_icon(bearing):
    """An inlined arrow pointing along an AIS bearing in degrees from north."""
    angle = bearing % 360
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
        f'<g transform="rotate({angle:.2f} 32 32)">'
        '<path d="M32 4 L53 52 L32 42 L11 52 Z" fill="#0B5C80" stroke="white" stroke-width="4" '
        'stroke-linejoin="round"/></g></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
