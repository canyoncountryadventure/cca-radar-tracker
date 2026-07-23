# CCA Canyon Pool Conditions — Version 2

Automated five-minute radar analysis for 17 Canyon Country Adventure watersheds. The GitHub Pages dashboard shows every canyon at once and provides a detailed dropdown view with the last rain event, retained last qualifying storm, radar pixels over the watershed, modeled pool condition, local NOAA Atlas 14 context, and the complete canyon-specific calculation.

## Canyons

ZeroG; Black Hole of White Canyon; Leprechaun; Woody; Hog Canyons; No Kidding; Angel Cove; Constrychnine; Alcatraz; Poe; Entrajo; Pool Arch; The Squeeze; Cable Canyon; Eardley; North Fork of Iron Wash; and Upper Greasewood.

## What runs every 15 minutes

GitHub Actions restores the last published state, finds all unprocessed IEM N0Q five-minute frames, analyzes each frame against all 17 polygons, groups wet frames into rain events, sends a grouped email for newly qualifying canyons, and republishes `docs/` to GitHub Pages. The last qualifying storm is retained even after later weak or dry weather.

## Model

The tool performs deterministic radar/GIS calculations; it does not ask AI to judge radar colors.

1. Decode IEM N0Q composite reflectivity to dBZ and use fractional radar-cell coverage within each watershed.
2. Convert dBZ to radar-equivalent rainfall with the NWS default relation `Z = 300 R^1.4`. Reflectivity is capped at 55 dBZ for rainfall-volume calculations to reduce hail inflation.
3. Calculate storm rainfall volume across the watershed and multiply by a provisional 5% combined runoff-and-delivery coefficient.
4. Anchor ZeroG to the field estimate that approximately 5 cfs for one hour, or 18,000 ft³, fills its channel pools.
5. Transfer the target to other watersheds with `18,000 × (area / 1.36)^0.4`. The 0.4 exponent is a provisional regional drainage-area scaling informed by the supplied USGS StreamStats comparisons, not a claim that every canyon behaves identically.
6. Require both sufficient delivered volume and a scaled high-intensity footprint before declaring likely full or fully flushed/new water.

The ZeroG spatial reference gates are:

- 50+ dBZ over 0.68 mi² (50% of ZeroG), or
- 55+ dBZ over 0.34 mi² (25%), or
- 60+ dBZ over 0.136 mi² (10%).

For another watershed, each required high-dBZ area is multiplied by `(area / 1.36)^0.4` and capped at the watershed's full area.

## Condition classes

- **Minor:** runoff ratio below 0.5 and no spatial intensity gate.
- **Moderate / possibly filled somewhat:** runoff ratio at least 0.5, or an intensity gate was reached.
- **Likely full / new water:** runoff ratio at least 1.0, an intensity gate was reached, and at least two wet five-minute frames occurred.
- **Full flush / completely new water:** runoff ratio at least 2.0, an intensity gate was reached, and at least two wet frames occurred.

These are likelihood estimates, not field confirmation. ZeroG is field-informed; the other canyon targets are provisional until observations can be used for calibration. Radar can miss low-level orographic effects, beam blockage, evaporation, antecedent moisture, and channel losses. This tool is not flash-flood guidance.

## Repository files

- `tracker.py` — multi-canyon radar, rainfall, runoff, event, and history engine.
- `watersheds.geojson` — normalized 17-polygon collection.
- `atlas14.json` — NOAA Atlas 14 point-frequency tables at canyon outlets.
- `config.json` — model and scheduling constants.
- `send_alert.py` — grouped Gmail notifications with per-event duplicate suppression.
- `docs/` — responsive GitHub Pages dashboard and generated data.
- `tools/prepare_watersheds.py` — reproducible polygon normalization and Atlas 14 retrieval.
- `tests/` — calculation, migration, and notification tests.

## Run and verify

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python tracker.py
```

For a single historical frame without changing status:

```bash
python tracker.py --at 2024-06-21T22:25:00Z --dry-run
```

The known June 21, 2024 ZeroG peak should show roughly 92.6% at 50+ dBZ, 53.4% at 55+ dBZ, and 27.5% at 60+ dBZ. Small differences can occur if IEM rendering changes.

## GitHub Pages and email

Set **Settings → Pages → Source** to **GitHub Actions**. The workflow runs at minutes 7, 22, 37, and 52 of every hour and may start a few minutes late when GitHub is busy.

For alerts, store the Gmail app password as the Actions secret `GMAIL_APP_PASSWORD`. Messages are sent from and to `canyoncountryadventure@gmail.com`. A normal scheduled run sends only for a newly qualifying event; the workflow's manual `Send a test email` option verifies delivery.

## Hostinger Website Builder embed

Add an Embed Code element and paste:

```html
<iframe
  src="https://canyoncountryadventure.github.io/cca-radar-tracker/"
  title="CCA canyon pool conditions"
  width="100%"
  height="1800"
  style="border:0; border-radius:14px; background:#101213;"
  loading="lazy">
</iframe>
```

## Sources

- [IEM N0Q composite documentation](https://mesonet.agron.iastate.edu/docs/nexrad_composites/)
- [IEM N0Q raster and dBZ encoding](https://mesonet.agron.iastate.edu/GIS/rasters.php?rid=2)
- [NOAA Atlas 14 PFDS](https://hdsc.nws.noaa.gov/pfds/)
- [USGS StreamStats](https://streamstats.usgs.gov/ss/)
