import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import select_status  # noqa: E402


class SelectStatusTests(unittest.TestCase):
    def write_status(
        self, root: Path, name: str, checked: str, *, complete: bool = True
    ) -> Path:
        path = root / name
        canyons = (
            {canyon_id: {} for canyon_id in select_status.EXPECTED_CANYON_IDS}
            if complete
            else {"poe": {}}
        )
        path.write_text(
            json.dumps(
                {
                    "schema_version": 5,
                    "last_checked_utc": checked,
                    "canyons": canyons,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_newest_valid_state_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            published = self.write_status(root, "published.json", "2026-09-06T20:00:00Z")
            backup = self.write_status(root, "backup.json", "2026-09-06T20:05:00Z")
            source, _ = select_status.choose_status([published, backup])
            self.assertEqual(source, backup)

    def test_invalid_newer_candidate_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            published = self.write_status(root, "published.json", "2026-09-06T20:00:00Z")
            backup = root / "backup.json"
            backup.write_text("not json", encoding="utf-8")
            source, _ = select_status.choose_status([published, backup])
            self.assertEqual(source, published)

    def test_explicit_recovery_overrides_fresher_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = self.write_status(root, "current.json", "2026-09-07T00:00:00Z")
            recovery = self.write_status(root, "recovery.json", "2026-09-05T00:02:51Z")
            source, _ = select_status.choose_status([current], recovery=recovery)
            self.assertEqual(source, recovery)

    def test_incomplete_newer_state_cannot_replace_complete_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            published = self.write_status(
                root,
                "published.json",
                "2026-09-07T00:10:00Z",
                complete=False,
            )
            backup = self.write_status(
                root, "backup.json", "2026-09-07T00:05:00Z"
            )
            source, status = select_status.choose_status([published, backup])
            self.assertEqual(source, backup)
            self.assertEqual(
                set(status["canyons"]), select_status.EXPECTED_CANYON_IDS
            )


if __name__ == "__main__":
    unittest.main()
