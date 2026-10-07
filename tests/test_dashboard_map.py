import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from dashboard_map import map_view, prepare_trace, track_segments
import kpler_handler
from kpler_handler import sample_positions


class RouteTests(unittest.TestCase):
    def test_sampling_covers_voyage_and_preserves_recent_positions(self):
        route = [{"receivedTime": str(i)} for i in range(400)]
        saved = sample_positions(route)
        self.assertEqual(len(saved), 180)
        self.assertEqual(saved[0], route[0])
        self.assertTrue(any(180 <= int(p["receivedTime"]) <= 220 for p in saved[:-75]))
        self.assertEqual(saved[-75:], route[-75:])

    def test_map_sorts_points_and_leaves_long_gap_open(self):
        base = datetime(2026, 9, 20, tzinfo=timezone.utc)
        raw = [
            {"receivedTime": (base + timedelta(hours=h)).isoformat(),
             "geo": {"lat": 5.0, "lon": 95.0 + h / 100}, "course": 90, "speed": 9}
            for h in (26, 1, 0, 27)
        ]
        points = prepare_trace(raw)
        self.assertEqual([p["time"].hour for p in points], [0, 1, 2, 3])
        segments, gaps = track_segments(points)
        self.assertEqual(len(segments), 2)
        self.assertEqual(gaps, 1)

    def test_dateline_view_and_implausible_jump(self):
        base = datetime(2026, 9, 20, tzinfo=timezone.utc)
        raw = [
            {"receivedTime": (base + timedelta(hours=h)).isoformat(),
             "geo": {"lat": 5, "lon": lon}}
            for h, lon in ((0, 179.8), (1, -179.8))
        ]
        points = prepare_trace(raw)
        self.assertAlmostEqual(abs(map_view(points)["longitude"]), 180, delta=0.5)
        self.assertEqual(track_segments(points)[1], 1)

    def test_stopped_ship_can_still_have_reported_heading(self):
        points = prepare_trace([{
            "receivedTime": "2026-09-25T00:00:00Z",
            "geo": {"lat": 1.2, "lon": 103.6},
            "heading": 135, "course": 0, "speed": 0,
        }])
        self.assertEqual(points[0]["heading"], 135)
        self.assertEqual(points[0]["speed"], 0)

    def test_worker_keeps_optional_ship_details_for_dashboard(self):
        class Response:
            status_code = 200

            def json(self):
                return [{
                    "geo": {"lat": 1.2, "lon": 103.6},
                    "receivedTime": "2026-09-25T00:00:00Z",
                    "speed": 8, "heading": 120,
                    "draught": "7.5", "volume": "inf",
                }]

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get(self, *args, **kwargs):
                return Response()

        with patch.object(kpler_handler, "load_tokens",
                          return_value={"refresh_token": "fake"}), \
             patch.object(kpler_handler.KplerSession, "ensure_access_token"), \
             patch.object(kpler_handler.httpx, "Client", Client):
            raw = kpler_handler.KplerSession().get_positions(119250, "2026-09-20")
        self.assertEqual(raw[0]["draught"], 7.5)
        self.assertNotIn("volume", raw[0])
        points = prepare_trace(raw)
        self.assertEqual(points[0]["draught"], 7.5)
        self.assertIsNone(points[0]["volume"])


if __name__ == "__main__":
    unittest.main()
