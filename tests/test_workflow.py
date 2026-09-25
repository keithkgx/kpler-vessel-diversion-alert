import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import gsheet_handler as gs
import shipment_update_flagging_workflow as worker


class FakeSheet:
    def __init__(self):
        self.rows = [gs.COLUMNS[:], [""], [
            "", "9579509", "Test vessel", "89161", "2026-06-16", "", "",
            "Singapore Anchorage", "1.20045", "103.590085", "150kt LSFO",
        ]]
        self.writes = []

    def get_all_values(self):
        return self.rows

    def batch_update(self, changes, value_input_option):
        self.writes.extend(changes)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        # The tests exercise alert and sheet-write behaviour even if the caller's
        # PowerShell session still has DRY_RUN=1 from a live-worker check.
        dry_run_patch = patch.object(worker, "DRY_RUN", False)
        dry_run_patch.start()
        self.addCleanup(dry_run_patch.stop)
        self.sheet = FakeSheet()
        now = datetime.now(timezone.utc)
        self.pings = [{
            "receivedTime": (now - timedelta(hours=12-i)).isoformat(),
            "geo": {"lat": 5 + i / 10, "lon": 95 + i / 10},
            "course": 95, "speed": 12,
        } for i in range(12)]
        self.result = worker.DiversionResult(
            ship_id="9579509", ship_name="Test vessel", cargo="150kt LSFO",
            score=2, flagged=True, latest_ping=self.pings[-1], mode="sg_bound",
        )

    def test_sheet_keeps_physical_row_indices(self):
        ships, mapping = gs.get_all_ships(self.sheet)
        self.assertEqual(ships[0][0], 3)
        self.assertEqual(mapping["Diversion_Flag"], "G")

    def test_first_alert_for_blank_flag_and_correct_origin(self):
        class SheetHandler:
            sheet = self.sheet
        class Kpler:
            def get_positions(_, **kwargs):
                return self.pings
        with patch.object(worker, "GSheet_Handler", return_value=SheetHandler()), \
             patch.object(worker, "KplerSession", return_value=Kpler()), \
             patch.object(worker, "detect_diversion", return_value=self.result) as detect, \
             patch.object(worker, "send_telegram_alert") as send:
            worker.run()
        self.assertEqual(detect.call_args.kwargs["origin_lat"], 5)
        self.assertEqual(detect.call_args.kwargs["origin_lon"], 95)
        send.assert_called_once()
        self.assertFalse(send.call_args.kwargs["flag_reverted"])
        self.assertEqual(next(c for c in self.sheet.writes if c["range"] == "G3")["values"], [[True]])

    def test_failed_telegram_preserves_flag_for_retry(self):
        class SheetHandler:
            sheet = self.sheet
        class Kpler:
            def get_positions(_, **kwargs):
                return self.pings
        with patch.object(worker, "GSheet_Handler", return_value=SheetHandler()), \
             patch.object(worker, "KplerSession", return_value=Kpler()), \
             patch.object(worker, "detect_diversion", return_value=self.result), \
             patch.object(worker, "send_telegram_alert", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                worker.run()
        self.assertEqual(next(c for c in self.sheet.writes if c["range"] == "G3")["values"], [[False]])

    def test_singapore_mode_uses_coordinates(self):
        result = worker.detect_diversion(
            "Test vessel", "9579509", "LSFO", "Singapore Anchorage", 1.20045,
            103.590085, 5, 95, self.pings,
        )
        self.assertEqual(result.mode, "sg_bound")


if __name__ == "__main__":
    unittest.main()
