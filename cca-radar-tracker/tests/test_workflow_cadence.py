import re
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/update-radar.yml"


class WorkflowCadenceTests(unittest.TestCase):
    def test_self_dispatch_chain_has_watchdog_and_write_permission(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertRegex(
            text,
            re.compile(
                r'^\s*-\s*cron:\s*["\']11,41 \* \* \* \*["\']\s*$',
                re.MULTILINE,
            ),
        )
        self.assertIn("actions: write", text)
        self.assertIn("if: always() && needs.cadence-gate.outputs.run_update == 'true'", text)
        self.assertIn("next_epoch=\"$((CHAIN_STARTED_EPOCH + 300))\"", text)
        self.assertIn("gh workflow run update-radar.yml", text)
        self.assertIn("age_seconds < 900", text)


if __name__ == "__main__":
    unittest.main()
