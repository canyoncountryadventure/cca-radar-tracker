# CCA Global Canyon Pool-Fill Radar

This project checks the Iowa Environmental Mesonet five-minute N0Q radar composite against the supplied Global Canyon watershed polygon. It publishes a responsive status page for embedding in the Hostinger Website Builder.

## Trigger logic

A radar frame qualifies when any one condition is true:

- 50 dBZ or greater covers at least 50% of the watershed.
- 55 dBZ or greater covers at least 25% of the watershed.
- 60 dBZ or greater covers at least 10% of the watershed.

Coverage uses fractional cell-area weights rather than counting only radar-cell centers. This matters because the watershed spans only about nine by five native N0Q grid cells. The static polygon mask is supersampled before each cell's share of the watershed is calculated.

## Files

- `tracker.py`: downloads the small WMS crop, calculates coverage, and maintains event state.
- `watershed.geojson`: watershed supplied from ArcGIS Pro in WGS84.
- `config.json`: thresholds and tracker settings.
- `n0q_palette.json`: IEM N0Q color-index palette used to decode WMS RGB values to dBZ.
- `docs/`: the website embedded into Hostinger.
- `.github/workflows/update-radar.yml`: runs four times per hour and republishes GitHub Pages.

## Validate the known benchmark

The June 21, 2024 peak identified in the IEM viewer was 4:25 PM MDT, or 22:25 UTC. Test it with:

```bash
python -m pip install -r requirements.txt
python tracker.py --at 2024-06-21T22:25:00Z --dry-run
```

The provided polygon produces approximately:

- 92.6% at or above 50 dBZ
- 53.4% at or above 55 dBZ
- 27.5% at or above 60 dBZ

Small differences below one percentage point can occur if the IEM WMS rendering changes.

## Publish with GitHub Pages

1. Create a new public GitHub repository named `cca-radar-tracker`.
2. Upload everything in this folder, including the hidden `.github` folder.
3. Open the repository's **Settings → Pages**.
4. Under **Build and deployment**, choose **GitHub Actions**.
5. Open **Actions → Update pool-fill radar → Run workflow** for the first run.
6. After it succeeds, the page will be available at:

   `https://YOUR-GITHUB-USERNAME.github.io/cca-radar-tracker/`

The scheduled workflow retrieves the last published state before analyzing new frames, so event history survives without creating constant repository commits.

## Embed in Hostinger Website Builder

In the Hostinger editor, use **Add elements → Embed code** and paste:

```html
<iframe
  src="https://YOUR-GITHUB-USERNAME.github.io/cca-radar-tracker/"
  title="Global Canyon pool-fill radar"
  width="100%"
  height="1200"
  style="border:0; border-radius:14px; background:#101213;"
  loading="lazy">
</iframe>
```

Resize the Hostinger embed element until the complete tracker is visible on desktop and mobile, then publish the website.

## Status meaning

The public card says **Recent pool-filling radar trigger detected** for 14 days after the most recent qualifying frame. Change both `recent_window_days` in `config.json` and `RECENT_DAYS` in `docs/app.js` if another window better represents how long the pools remain useful.

This is a radar-based indicator. It does not visually confirm pool depth and is not flash-flood guidance.

## Data source

Iowa Environmental Mesonet N0Q composite reflectivity. The source is a roughly one-kilometer, five-minute national mosaic in EPSG:4326. IEM documents the archive, WMS access, and palette/index conversion at:

- https://mesonet.agron.iastate.edu/docs/nexrad_composites/
- https://mesonet.agron.iastate.edu/GIS/rasters.php?rid=2
