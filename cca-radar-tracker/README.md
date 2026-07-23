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

## Canyon-specific runoff losses

Storm rainfall depth is accumulated for every fractional radar cell and averaged across the entire polygon. Rainfall volume is:

```text
basin-average rain depth × watershed area
```

The former fixed 5% coefficient has been removed. Each watershed now has a
composite NRCS curve number built from USDA SSURGO hydrologic soil groups and
2021 NLCD land cover. Direct-runoff depth is calculated as:

```text
S = 1000 / CN - 10
Ia = 0.20S
Q = 0                         when P ≤ Ia
Q = (P - Ia)² / (P + 0.80S)  when P > Ia
```

The dashboard calculates dry, normal, and wet antecedent-condition cases.
Initial abstraction is therefore canyon-specific rather than a universal
0.15-inch loss. SSURGO, NLCD, and NRCS equations are science-based screening
inputs, but they cannot observe current soil moisture, sandstone fractures,
channel transmission loss, or pool geometry.

## Predicted peak CFS

Runoff volume and flow rate are different quantities. The tracker no longer
divides event volume by 3,600 and calls the result CFS. It routes each
dry/normal/wet runoff volume with a volume-conserving triangular hydrograph:

```text
hydrograph base = rain duration + 2 × watershed lag
peak CFS = 2 × runoff volume / hydrograph base
```

Lag is estimated from USGS 3DEP terrain, watershed slope, basin extent, and the
supplied pour point using the NRCS lag relation. The displayed CFS is a broad
screening range, not measured discharge. This model correctly reduces the
recent weak Black Hole, Angel Cove, and Entrajo events to no modeled runoff,
while retaining the June 21, 2024 ZeroG storm as a full-flush benchmark.

## Fill targets and one-hour cfs

ZeroG is anchored to the field estimate that about 5 cfs for one hour would refill its channel pools:

```text
5 ft³/s × 3,600 seconds = 18,000 ft³
```

Other canyon targets are not automatically 5 cfs for one hour. They are provisionally scaled by watershed area:

```text
fill target = 18,000 ft³ × (watershed area / 1.36 mi²)^0.4
```

The dashboard still converts the *fill target* to its one-hour equivalent so
the ZeroG 5-cfs-for-one-hour field anchor remains understandable. It does not
use that conversion as predicted event flow.

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
- `hydrology.json` — SSURGO, NLCD, 3DEP, StreamStats, curve-number, and lag inventory.
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
- [USDA Web Soil Survey / SSURGO](https://websoilsurvey.nrcs.usda.gov/)
- [USGS National Land Cover Database](https://www.usgs.gov/centers/eros/science/national-land-cover-database)
- [USGS 3D Elevation Program](https://www.usgs.gov/3d-elevation-program)
- [NRCS National Engineering Handbook, direct runoff](https://directives.nrcs.usda.gov/sites/default/files2/1720460920/Chapter%2010%20-%20Estimation%20of%20Direct%20Runoff%20from%20Storm%20Rainfall.pdf)

These are experimental model estimates, not field confirmation, professional hydrologic advice, or flash-flood guidance.
