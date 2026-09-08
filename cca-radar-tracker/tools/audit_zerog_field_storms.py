#!/usr/bin/env python3
"""Audit Zero G field-observed rises against exact IEM historical N0Q frames.

Calibration/QA only. The audit rebuilds all five field-anchor storms, scans beyond
the watershed for displaced convective cores, and compares the current lumped
NRCS calculation with cell-by-cell rainfall runoff using the same composite CN.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tracker

UTC = timezone.utc
WINDOWS = {
    "2026-08-12_major_field_rise": {"start":"2026-08-12T20:30:00Z","end":"2026-08-12T22:45:00Z","field_rise_mdt":"2026-08-12 16:02 MDT","upper_rise_ft":3.1781,"lower_rise_ft":3.06,"field_response":"major refill","purpose":"Recover storm preceding dual-logger ~3.1 ft rise."},
    "2026-08-21_small_field_rise": {"start":"2026-08-22T04:00:00Z","end":"2026-08-22T06:35:00Z","field_rise_mdt":"2026-08-21 22:32 MDT","upper_rise_ft":0.37,"lower_rise_ft":0.15,"field_response":"small response","purpose":"Low-rain calibration anchor."},
    "2026-08-29_major_refill": {"start":"2026-08-29T19:50:00Z","end":"2026-08-29T21:35:00Z","field_rise_mdt":"2026-08-29 14:47 MDT","upper_rise_ft":3.16,"lower_rise_ft":2.85,"field_response":"major refill","purpose":"Primary fill-threshold calibration anchor."},
    "2026-08-30_strong_flush": {"start":"2026-08-30T20:25:00Z","end":"2026-08-30T21:50:00Z","field_rise_mdt":"2026-08-30 15:17-15:47 MDT","upper_rise_ft":2.64,"lower_rise_ft":2.14,"field_response":"strong flush","purpose":"Primary flush calibration anchor; upper logger preferred during lower physical disturbance."},
    "2026-08-31_evening_field_rise": {"start":"2026-08-31T23:30:00Z","end":"2026-09-01T01:30:00Z","field_rise_mdt":"2026-08-31 18:32-19:13 MDT","upper_rise_ft":0.96,"lower_rise_ft":1.57,"field_response":"delayed/secondary hydraulic rise","purpose":"Test whether the secondary rise is supported by new radar runoff."},
}

def parse(value): return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

def haversine_miles(lat1, lon1, lat2, lon2):
    r=3958.7613; p1,p2=math.radians(lat1),math.radians(lat2); dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))

def expanded_grid(base,cells=30):
    return tracker.Grid(left=base.left-cells*tracker.GRID_RESOLUTION,bottom=base.bottom-cells*tracker.GRID_RESOLUTION,right=base.right+cells*tracker.GRID_RESOLUTION,top=base.top+cells*tracker.GRID_RESOLUTION,width=base.width+2*cells,height=base.height+2*cells)

def pixel_center(grid,row,column):
    return grid.top-(row+0.5)*tracker.GRID_RESOLUTION, grid.left+(column+0.5)*tracker.GRID_RESOLUTION

def nearest_basin_distance_miles(canyon,lat,lon):
    points=[]
    for row in range(canyon.grid.height):
        for col in range(canyon.grid.width):
            if float(canyon.weights[row,col])>0.05: points.append(pixel_center(canyon.grid,row,col))
    return min(haversine_miles(lat,lon,plat,plon) for plat,plon in points)

def spatial_runoff_diagnostics(canyon,basin_rain_grid,basin_mean):
    hydrology=canyon.model["hydrology"]; weights=np.asarray(canyon.weights,dtype=float); finite=np.isfinite(basin_rain_grid); valid=np.where(finite,weights,0.0); denom=float(valid.sum()); area_ft2=canyon.area_sq_mi*tracker.SQUARE_FEET_PER_SQUARE_MILE; output={}
    for state in ("dry","normal","wet"):
        cn=float(hydrology["curve_number"][state]); lump=tracker.nrcs_runoff_depth(basin_mean,cn)
        spatial_grid=np.vectorize(lambda v: tracker.nrcs_runoff_depth(float(v),cn))(np.nan_to_num(basin_rain_grid,nan=0.0)); spatial=0.0 if denom<=0 else float((spatial_grid*valid).sum()/denom)
        output[state]={"curve_number":cn,"initial_abstraction_inches":round(tracker.nrcs_initial_abstraction(cn),4),"lumped_runoff_depth_inches":round(lump,4),"lumped_runoff_ft3":round(lump/12*area_ft2),"spatial_runoff_depth_inches":round(spatial,4),"spatial_runoff_ft3":round(spatial/12*area_ft2)}
    return output

def scan_window(name,spec,canyon,config,palette):
    grid=expanded_grid(canyon.grid,30); accumulated=np.zeros((grid.height,grid.width),dtype=float); basin_total=0.0; peak_dbz=None; peak_dbz_utc=None; peak_frame=0.0; peak_frame_utc=None; frames=[]
    for ts in tracker.iter_five_minutes(parse(spec["start"]),parse(spec["end"])):
        image=tracker.fetch_radar_image(ts,grid,config,latest_reference=None); dbz,_=tracker.image_to_dbz(image,palette); rain=tracker.rain_depth_inches(dbz,config["model"]); accumulated+=rain
        basin_image=tracker.crop_for_grid(image,grid,canyon.grid); analysis,_=tracker.analyze_canyon_image(basin_image,canyon,palette,config); frame=float(analysis.get("frame_basin_rain_inches") or 0.0); basin_total+=frame; mx=analysis.get("maximum_dbz")
        if mx is not None and (peak_dbz is None or float(mx)>peak_dbz): peak_dbz=float(mx); peak_dbz_utc=tracker.utc_text(ts)
        if frame>peak_frame: peak_frame=frame; peak_frame_utc=tracker.utc_text(ts)
        frames.append({"utc":tracker.utc_text(ts),"basin_rain_in":round(frame,4),"basin_max_dbz":mx})
    finite=np.where(np.isfinite(accumulated),accumulated,-1.0); row,col=np.unravel_index(np.argmax(finite),finite.shape); core=float(accumulated[row,col]); lat,lon=pixel_center(grid,int(row),int(col)); distance=nearest_basin_distance_miles(canyon,lat,lon)
    inside=accumulated[30:30+canyon.grid.height,30:30+canyon.grid.width]; mask=canyon.weights>0.05; max_inside=float(np.max(inside[mask])) if np.any(mask) else 0.0; runoff=spatial_runoff_diagnostics(canyon,inside,basin_total)
    return {"name":name,**spec,"modeled_basin_area_sq_mi":canyon.area_sq_mi,"basin_rain_total_inches":round(basin_total,4),"basin_peak_dbz":peak_dbz,"basin_peak_dbz_utc":peak_dbz_utc,"peak_basin_5min_rain_inches":round(peak_frame,4),"peak_basin_5min_rain_utc":peak_frame_utc,"max_accumulated_pixel_inside_basin_inches":round(max_inside,4),"max_nearby_accumulated_pixel_inches":round(core,4),"max_nearby_core_lat":round(lat,5),"max_nearby_core_lon":round(lon,5),"max_nearby_core_distance_from_basin_miles":round(distance,2),"nearby_core_to_basin_average_ratio":None if basin_total<=0 else round(core/basin_total,2),"runoff_comparison":runoff,"strongest_basin_frames":sorted(frames,key=lambda x:x["basin_rain_in"],reverse=True)[:12]}

def main():
    config=json.loads((ROOT/"config.json").read_text()); collection=json.loads((ROOT/"watersheds.geojson").read_text()); atlas=json.loads((ROOT/"atlas14.json").read_text()); hydrology=json.loads((ROOT/"hydrology.json").read_text()); palette=tracker.load_palette(ROOT/"n0q_palette.json"); canyons,_=tracker.build_canyons(collection,atlas,config,hydrology); canyon=next(x for x in canyons if x.canyon_id=="zerog")
    results={"generated_utc":tracker.utc_text(datetime.now(UTC)),"purpose":"Five field-anchor audit using exact historical IEM N0Q frames; compares lumped versus spatial-rainfall NRCS runoff.","windows":{}}
    for name,spec in WINDOWS.items(): results["windows"][name]=scan_window(name,spec,canyon,config,palette)
    (ROOT/"docs/data/zerog_field_calibration.json").write_text(json.dumps(results,indent=2)+"\n")
    lines=["# Zero G field-event radar audit",""]
    for item in results["windows"].values():
        n=item["runoff_comparison"]["normal"]; w=item["runoff_comparison"]["wet"]
        lines += [f"## {item['name']}","",f"- Field response: {item['field_response']}; upper +{item['upper_rise_ft']:.2f} ft; lower +{item['lower_rise_ft']:.2f} ft",f"- Basin rain: **{item['basin_rain_total_inches']:.4f} in**; wettest in-basin pixel: **{item['max_accumulated_pixel_inside_basin_inches']:.4f} in**",f"- Lumped normal runoff: **{n['lumped_runoff_ft3']:,} ft3**; spatial normal: **{n['spatial_runoff_ft3']:,} ft3**; spatial wet: **{w['spatial_runoff_ft3']:,} ft3**",f"- Strongest nearby core: **{item['max_nearby_accumulated_pixel_inches']:.4f} in**, {item['max_nearby_core_distance_from_basin_miles']:.2f} mi from modeled basin","" ]
    (ROOT/"docs/zerog-field-calibration.md").write_text("\n".join(lines)+"\n")
    print(json.dumps(results,indent=2))

if __name__=="__main__": main()
