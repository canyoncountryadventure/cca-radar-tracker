# Slot Canyon Pool Conditions

Automated five-minute radar analysis for 17 Canyon Country Adventures watersheds. The GitHub Pages dashboard provides an all-canyon overview and detailed canyon view with an interactive topographic/satellite map, radar pixels, the last rain event, retained last likely-full storm, canyon-specific calculations, and NOAA Atlas 14 context.

## Canyons

ZeroG; Black Hole of White Canyon; Leprechaun; Woody; Hog Canyons; No Kidding; Angel Cove; Constrychnine; Alcatraz; Poe; Entrajo; Pool Arch; The Squeeze; Cable Canyon; Eardley; North Fork of Iron Wash; and Upper Greasewood.

## Automated operation

GitHub Actions runs four times per hour. It restores the published state, analyzes all new IEM N0Q five-minute frames against all 17 polygons, groups wet frames into events, sends a grouped email for newly likely-full canyons, and republishes the dashboard. The last qualifying event remains available after later weak or dry weather.

## Radar-to-rainfall calculation

IEM N0Q reflectivity is decoded to dBZ. Fractional cell-area masks calculate each radar cell's share of a watershed. The NWS default convective relationship is used:

```text
Z = 300 R^1.4
Z = 10^(dBZ/10)
R = (Z/300)^(1/1.4) millimeters/hour
```

This relationship is a compromise estimate, not a rain gauge. The rainfall-rate calculation is capped at 55 dBZ because very high reflectivity may contain hail and should not be converted into ever-increasing liquid rainfall. The original uncapped reflectivity remains available for the heavy-rain footprint test.

## Delivered runoff and the 5% coefficient

Storm rainfall depth is accumulated for every fractional radar cell and averaged across the entire polygon. Rainfall volume is:

```text
basin-average rain depth × watershed area
```

Estimated water delivered to the pour point is:

```text
radar rainfall volume × 5%
```

The 5% is a **provisional effective runoff-and-delivery coefficient**, not a measured soil-absorption rate. It collectively represents infiltration, surface storage, evaporation, and transmission losses before runoff reaches the canyon. Antecedent moisture and storm intensity can change it dramatically. It should be recalibrated as field observations become available.

## Fill targets and one-hour cfs

ZeroG is anchored to the field estimate that about 5 cfs for one hour would refill its channel pools:

```text
5 ft³/s × 3,600 seconds = 18,000 ft³
```

Other canyon targets are not automatically 5 cfs for one hour. They are provisionally scaled by watershed area:

```text
fill target = 18,000 ft³ × (watershed area / 1.36 mi²)^0.4
```

The dashboard converts every target and delivered-runoff volume into a one-hour-equivalent cfs value for intuitive comparison. The 0.4 exponent is a provisional regional transfer informed by the supplied StreamStats comparisons.

## Estimated fill ratio and heavy-rain footprint

```text
estimated fill ratio = delivered runoff / canyon fill target
```

`1.0×` means modeled delivered volume equals the provisional target. It is not a measured pool-depth percentage.

The heavy-rain footprint is a second reality check. A scaled minimum area must reach 50, 55, or 60 dBZ. This prevents widespread gentle rain on a huge basin—or one isolated noisy pixel—from being labeled a full refill based on volume alone.

ZeroG reference footprints are 50+ dBZ over 0.68 mi², 55+ over 0.34 mi², or 60+ over 0.136 mi². Other watersheds use the same area exponent.

## Condition tiers

- **Little to no expected change:** fill ratio below 1.0 and no heavy-rain footprint.
- **Some new water possible / partial refill:** ratio at least 1.0 or a heavy-rain footprint, but the complete likely-full test was not met.
- **Likely substantially or fully refilled:** ratio at least 1.0, a heavy-rain footprint, and at least two wet five-minute frames.
- **Full flush / completely new water:** ratio at least 2.0, a heavy-rain footprint, and at least two wet frames.

## NOAA Atlas 14 equivalent

The dashboard now compares the **watershed-average accumulated radar rainfall** over the event's full duration with local NOAA Atlas 14 point-frequency depths at the canyon outlet. Atlas depths are interpolated between standard durations when necessary, and return period is interpolated between the published recurrence curves.

This is labeled an **Atlas 14 equivalent**, not a formal watershed return interval. Atlas 14 supplies point precipitation frequencies; the tracker does not presently apply an areal-reduction factor or model spatial storm probability. It is rainfall-rarity context, not evidence that pools filled.

## Files

- `tracker.py` — multi-canyon radar, rainfall, runoff, event, and history engine.
- `watersheds.geojson` — normalized 17-polygon collection.
- `atlas14.json` — NOAA Atlas 14 point-frequency tables at canyon outlets.
- `config.json` — model and scheduling constants.
- `send_alert.py` — grouped Gmail notifications with duplicate suppression.
- `docs/` — interactive GitHub Pages dashboard and generated data.
- `tools/prepare_watersheds.py` — polygon normalization and Atlas retrieval.
- `tests/` — calculation, migration, and notification tests.

## Run and verify

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python tracker.py
```

Historical frame without changing status:

```bash
python tracker.py --at 2024-06-21T22:25:00Z --dry-run
```

The June 21, 2024 ZeroG peak produces approximately 92.5% at 50+ dBZ, 53.8% at 55+ dBZ, and 27.9% at 60+ dBZ.

## Sources

- [IEM historical NEXRAD mosaic viewer](https://mesonet.agron.iastate.edu/current/mcview.phtml)
- [IEM NEXRAD mosaic documentation](https://mesonet.agron.iastate.edu/docs/nexrad_composites/)
- [IEM N0Q raster and dBZ encoding](https://mesonet.agron.iastate.edu/GIS/rasters.php?rid=2)
- [NWS radar rainfall estimation and default Z–R relationship](https://www.weather.gov/mrx/radarrainfallestimates)
- [NOAA Atlas 14 PFDS](https://hdsc.nws.noaa.gov/pfds/)
- [USGS StreamStats](https://streamstats.usgs.gov/ss/)

These are experimental model estimates, not field confirmation, professional hydrologic advice, or flash-flood guidance.
