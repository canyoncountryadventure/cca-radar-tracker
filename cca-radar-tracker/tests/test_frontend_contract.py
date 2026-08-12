import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        cls.page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    def test_history_click_rerenders_all_selected_storm_outputs(self):
        history_bindings = self.app.split(
            'panel.querySelectorAll("[data-history-index]")', 1
        )[1]
        click_handler = history_bindings.split(
            'button.addEventListener("click", () => {', 1
        )[1].split("});", 1)[0]
        self.assertIn("app.selectedEvent = event", click_handler)
        self.assertIn("renderSelectedStorm(model, event)", click_handler)
        self.assertIn("drawSelectedRadar()", click_handler)

    def test_current_condition_is_separate_from_selected_storm(self):
        self.assertIn("CURRENT CANYON CONDITION ESTIMATE", self.app)
        self.assertIn("SELECTED STORM:", self.app)
        self.assertIn("This decides how much refill this one radar event", self.app)

    def test_obsolete_labels_are_removed(self):
        combined = self.app + self.page
        self.assertNotIn("LAST RAIN EVENT", combined)
        self.assertNotIn("dBZ footprint observations", combined)
        self.assertNotIn("Open archived radar animation", combined)
        self.assertIn("MOST RECENT RAIN EVENT", combined)
        self.assertIn("Storm intensity distribution", combined)
        self.assertIn("Open interactive radar map", combined)


if __name__ == "__main__":
    unittest.main()
