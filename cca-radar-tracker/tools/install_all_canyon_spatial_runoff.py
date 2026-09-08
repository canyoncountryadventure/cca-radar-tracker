#!/usr/bin/env python3
"""Standardize spatial NRCS runoff across all modeled canyons.

Zero G's logger-derived field thresholds remain canyon-specific calibration evidence.
The hydrologic runoff calculation itself becomes identical for every canyon: apply
the nonlinear NRCS runoff equation to each retained accumulated-rainfall grid cell,
then area-weight runoff over the watershed. Older events without a compatible grid
fall back transparently to the basin-average equation.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "tracker.py"
README = ROOT / "README.md"
TEST = ROOT / "tests" / "test_all_canyon_spatial_runoff.py"

text = TRACKER.read_text(encoding="utf-8")

if 'RUNOFF_METHOD_VERSION = "spatial_nrcs_all_canyons_v1"' not in text:
    text = text.replace(
        "STATUS_SCHEMA_VERSION = 5\n",
        'STATUS_SCHEMA_VERSION = 5\nRUNOFF_METHOD_VERSION = "spatial_nrcs_all_canyons_v1"\n',
        1,
    )

old_signature = '''        "pothole_modifier": canyon.model.get("pothole_modifier"),
    }
'''
new_signature = '''        "pothole_modifier": canyon.model.get("pothole_modifier"),
        "runoff_method_version": RUNOFF_METHOD_VERSION,
    }
'''
if old_signature in text:
    text = text.replace(old_signature, new_signature, 1)
elif '"runoff_method_version": RUNOFF_METHOD_VERSION' not in text:
    raise SystemExit("Could not add runoff method to canyon model signature")

old_doc = '''    This preserves localized slickrock storm cores that can exceed initial
    abstraction even when watershed-average rainfall does not. The field-validated
    production use is currently limited to Zero G; other canyons retain the lumped
    calculation until they have comparable observations.
'''
new_doc = '''    This preserves localized storm cores that can exceed initial abstraction even
    when watershed-average rainfall does not. The same spatial calculation is used
    for every modeled canyon whenever the retained event has a compatible rainfall
    grid; basin-average runoff is retained only as a transparent historical fallback.
'''
if old_doc in text:
    text = text.replace(old_doc, new_doc, 1)

old_spatial = '''        spatial_depth = (
            spatial_nrcs_runoff_depth(event, canyon, curve_number)
            if canyon.canyon_id == "zerog"
            else None
        )
'''
new_spatial = '''        spatial_depth = spatial_nrcs_runoff_depth(event, canyon, curve_number)
'''
if old_spatial in text:
    text = text.replace(old_spatial, new_spatial, 1)
elif "spatial_depth = spatial_nrcs_runoff_depth(event, canyon, curve_number)" not in text:
    raise SystemExit("Could not standardize spatial runoff call")

old_flag = '''    event["runoff_spatialization_applied"] = bool(
        canyon.canyon_id == "zerog"
        and any(value is not None for value in event.get("spatial_runoff_depth_inches", {}).values())
    )
    event["runoff_spatialization_basis"] = (
        "Zero G field-calibrated: NRCS runoff applied cell-by-cell to accumulated radar rainfall before watershed weighting"
        if event["runoff_spatialization_applied"]
        else "Basin-average NRCS fallback"
    )
'''
new_flag = '''    event["runoff_method_version"] = RUNOFF_METHOD_VERSION
    event["runoff_spatialization_applied"] = bool(
        any(
            value is not None
            for value in event.get("spatial_runoff_depth_inches", {}).values()
        )
    )
    event["runoff_spatialization_basis"] = (
        "Spatial NRCS: runoff applied cell-by-cell to accumulated radar rainfall before watershed weighting"
        if event["runoff_spatialization_applied"]
        else "Basin-average NRCS fallback because no compatible accumulated rainfall grid is retained for this event"
    )
'''
if old_flag in text:
    text = text.replace(old_flag, new_flag, 1)
elif 'event["runoff_method_version"] = RUNOFF_METHOD_VERSION' not in text:
    raise SystemExit("Could not update spatial runoff metadata")

old_experimental = '''            "experimental_comparison_explanation": (
                "Weak-echo persistence, connected-core area, watershed-size scaling, "
                "MRMS QPE, and spatial curve-number runoff are reserved as disabled "
                "comparison modes until historical and field calibration show that they "
                "outperform the fixed baseline."
            ),
'''
new_experimental = '''            "experimental_comparison_explanation": (
                "Weak-echo persistence, connected-core area, watershed-size scaling, "
                "and MRMS QPE remain comparison modes. Spatial curve-number runoff is "
                "the production runoff method for all 22 canyons whenever a retained "
                "accumulated-rainfall grid is available."
            ),
'''
if old_experimental in text:
    text = text.replace(old_experimental, new_experimental, 1)

old_direct = '''            "direct_runoff_explanation": (
                "No fixed runoff coefficient is used. Zero G now applies the adjusted "
                "NRCS equation to each accumulated radar-rainfall cell before area weighting, "
                "because paired logger events showed that basin averaging can erase localized "
                "slickrock runoff. Other canyons retain the basin-average calculation pending "
                "field calibration. Dry, normal, and wet estimates use canyon-specific composite "
                "curve numbers from SSURGO soils and 2021 NLCD land cover. Pixels without a "
                "usable SSURGO hydrologic soil group are conservatively assigned to HSG D. "
                "The central display uses normal conditions. Zero G also carries an independent "
                "field-calibrated storm-core response test: 0.20 in for major refill evidence "
                "and 1.00 in for strong-flush evidence, each requiring the normal duration check."
            ),
'''
new_direct = '''            "direct_runoff_explanation": (
                "No fixed runoff coefficient is used. All 22 modeled canyons apply the adjusted "
                "NRCS equation to each accumulated radar-rainfall cell before area weighting, "
                "so a localized convective core is not erased by watershed averaging. Dry, "
                "normal, and wet estimates use canyon-specific composite curve numbers from "
                "SSURGO soils and 2021 NLCD land cover. Pixels without a usable SSURGO hydrologic "
                "soil group are conservatively assigned to HSG D. The central display uses normal "
                "conditions. A gridless retained historical event is explicitly tagged and uses "
                "the basin-average NRCS fallback. Zero G separately carries an independent "
                "field-calibrated storm-core response test: 0.20 in for major refill evidence "
                "and 1.00 in for strong-flush evidence, each requiring the normal duration check."
            ),
'''
if old_direct in text:
    text = text.replace(old_direct, new_direct, 1)
elif "All 22 modeled canyons apply the adjusted" not in text:
    raise SystemExit("Could not update direct runoff explanation")

old_condition = '''                "confidence ages. Below 25% of the empty-storage target is no meaningful refill. 'Likely full' "
                "requires the storage-volume and minimum wet-duration tests; dBZ footprints are "
                "context only."
'''
new_condition = '''                "confidence ages. Below 25% of the empty-storage target is no meaningful refill. 'Likely full' "
                "normally requires the storage-volume and minimum wet-duration tests; a canyon-specific "
                "field calibration may provide an additional independent decision test where observations exist. "
                "dBZ footprints alone remain context only."
'''
if old_condition in text:
    text = text.replace(old_condition, new_condition, 1)

TRACKER.write_text(text, encoding="utf-8")

readme = README.read_text(encoding="utf-8")
readme = readme.replace(
    "The old fixed 5% runoff coefficient is not used. Accumulated basin-average rainfall is converted to NRCS direct runoff for dry, normal, and wet antecedent conditions using canyon-specific curve numbers from SSURGO soils and 2021 NLCD land cover:",
    "The old fixed 5% runoff coefficient is not used. For all 22 canyons, the NRCS direct-runoff equation is applied to each cell of the accumulated spatial rainfall grid first, then runoff is area-weighted across the watershed. Dry, normal, and wet antecedent conditions use canyon-specific curve numbers from SSURGO soils and 2021 NLCD land cover:",
)
readme = readme.replace(
    "The traditional table curve numbers are converted to the adjusted Ia/S = 0.05 retention basis before runoff is calculated. Pixels without a usable SSURGO hydrologic soil group are assigned to HSG D (high runoff potential). The central classification uses the normal-condition estimate. This is runoff generated by the modeled watershed, not measured water delivered to every pothole.",
    "The traditional table curve numbers are converted to the adjusted Ia/S = 0.05 retention basis before runoff is calculated. Pixels without a usable SSURGO hydrologic soil group are assigned to HSG D (high runoff potential). Applying the nonlinear runoff equation before spatial averaging preserves localized storm cores that would otherwise disappear in a basin mean. The central classification uses the normal-condition estimate. Retained historical events without a compatible accumulated-rainfall grid are explicitly marked and use the basin-average NRCS fallback. This is runoff generated by the modeled watershed, not measured water delivered to every pothole.",
)
readme = readme.replace(
    "These measurements describe whether intense echoes were isolated or widespread. They do not gate classification. A likely-full or strong-flush classification requires the runoff-volume threshold and at least two wet five-minute frames.",
    "These measurements describe whether intense echoes were isolated or widespread. They do not gate classification. For every canyon, the standard likely-full or strong-flush path requires the spatial-runoff volume threshold and at least two wet five-minute frames. Canyon-specific field calibration may add an independent evidence path where actual observations exist; currently that applies only to Zero G.",
)
readme = readme.replace(
    "Weak-echo persistence, connected-core area, watershed-size scaling, MRMS QPE, spatial curve-number response units, and delivery factors are configured as disabled comparison work. They do not replace the fixed baseline until known events and field observations show a measurable improvement.",
    "Weak-echo persistence, connected-core area, watershed-size scaling, MRMS QPE, and delivery factors remain comparison work. Spatial curve-number runoff is now the production method across all 22 canyons whenever the event retains a compatible rainfall grid; gridless historical events use a clearly labeled basin-average fallback.",
)
README.write_text(readme, encoding="utf-8")

TEST.write_text('''import unittest\nfrom types import SimpleNamespace\n\nimport numpy as np\n\nimport tracker\n\n\nclass AllCanyonSpatialRunoffTests(unittest.TestCase):\n    def canyon(self, canyon_id="neon"):\n        return SimpleNamespace(\n            canyon_id=canyon_id,\n            area_sq_mi=1.0,\n            weights=np.array([[0.5, 0.5]], dtype=float),\n            model={\n                "hydrology": {\n                    "lag_hours": 0.5,\n                    "curve_number": {"dry": 77.3, "normal": 88.6, "wet": 94.8},\n                }\n            },\n        )\n\n    def config(self):\n        return {"model": {"frame_minutes": 5}}\n\n    def event(self, include_grid=True):\n        event = {\n            "start_utc": "2026-08-29T20:00:00Z",\n            "end_utc": "2026-08-29T20:05:00Z",\n            "frames": 2,\n            "wet_frames": 2,\n            "basin_rain_inches": 0.10,\n        }\n        if include_grid:\n            event["accumulated_rain_grid_inches"] = [[0.0, 0.20]]\n        return event\n\n    def test_non_zero_g_uses_spatial_runoff(self):\n        event = self.event()\n        canyon = self.canyon("neon")\n        tracker.apply_hydrologic_model(event, canyon, self.config())\n        self.assertTrue(event["runoff_spatialization_applied"])\n        self.assertEqual(event["runoff_method_version"], tracker.RUNOFF_METHOD_VERSION)\n        self.assertGreater(\n            event["direct_runoff_ft3_range"]["normal"],\n            event["lumped_direct_runoff_ft3_range"]["normal"],\n        )\n\n    def test_gridless_history_is_explicit_fallback(self):\n        event = self.event(include_grid=False)\n        canyon = self.canyon("woody")\n        tracker.apply_hydrologic_model(event, canyon, self.config())\n        self.assertFalse(event["runoff_spatialization_applied"])\n        self.assertIn("fallback", event["runoff_spatialization_basis"].lower())\n        self.assertEqual(\n            event["direct_runoff_ft3_range"],\n            event["lumped_direct_runoff_ft3_range"],\n        )\n\n    def test_method_version_changes_model_signature(self):\n        canyon = SimpleNamespace(\n            geometry={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]},\n            model={\n                "fill_target_ft3": 100,\n                "flush_target_ft3": 200,\n                "technical_length_miles": 1.0,\n                "pothole_modifier": 0.0,\n            },\n        )\n        first = tracker.canyon_model_signature(canyon)\n        prior = tracker.RUNOFF_METHOD_VERSION\n        try:\n            tracker.RUNOFF_METHOD_VERSION = "different-test-method"\n            second = tracker.canyon_model_signature(canyon)\n        finally:\n            tracker.RUNOFF_METHOD_VERSION = prior\n        self.assertNotEqual(first, second)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

print("Installed consistent spatial NRCS runoff for all 22 canyons")
