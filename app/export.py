"""Export detections to GeoJSON, Shapefile, CSV and an annotated preview JPG."""
import csv
import json
import os
from datetime import datetime

import cv2
import numpy as np

from .georef import to_map, utm_epsg_from_lonlat
from .inference import PLANTS_PER_CLASS, CLASS_COLORS

try:
    from pyproj import CRS, Transformer
    _HAS_PYPROJ = True
except Exception:
    _HAS_PYPROJ = False

try:
    import fiona
    from fiona.crs import from_epsg
    _HAS_FIONA = True
except Exception:
    _HAS_FIONA = False


def _ts():
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")


def _best_text_color(bgr):
    b, g, r = bgr
    lum = 0.114 * b + 0.587 * g + 0.299 * r
    return (0, 0, 0) if lum > 180 else (255, 255, 255)


def build_features(detections, raster_src, model_names):
    """Polygon Features in source-map coords (if georef) or pixels."""
    gt = raster_src.gdal_geotransform if raster_src.crs else None
    feats = []
    for d in detections:
        cls_id = int(d["cls"])
        conf = float(d["conf"])
        x1, y1, x2, y2 = d["xyxy"]
        if gt is not None and raster_src.crs:
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
            ring = [list(to_map(px, py, gt)) for px, py in corners]
            coord_space = "map"
        else:
            ring = [[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]
            coord_space = "image_pixels"
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "cls_id": cls_id,
                "cls_name": str(model_names.get(cls_id, f"class_{cls_id}")),
                "conf": conf,
                "plants_per_box": int(PLANTS_PER_CLASS[cls_id]),
                "plants": int(PLANTS_PER_CLASS[cls_id]),
                "coord_space": coord_space,
                "source_image": os.path.basename(raster_src.path),
            },
        })
    return feats


def save_geojson(features, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)
    return path


def save_plot_counts_csv(per_plot_results, path, model_names=None):
    """One row per plot with class-wise box counts, plants, and total."""
    if not per_plot_results:
        return None
    classes = sorted(PLANTS_PER_CLASS.keys())
    names = model_names or {}
    with open(path, "w", newline="", encoding="utf-8") as cf:
        w = csv.writer(cf)
        header = ["plot_id"]
        for c in classes:
            label = str(names.get(c, f"class_{c}"))
            header += [f"{label}_boxes", f"{label}_plants"]
        header += ["total_plants"]
        w.writerow(header)
        for r in per_plot_results:
            row = [r["plot_id"]]
            for c in classes:
                row += [r["class_box_counts"].get(c, 0), r["per_class_plants"].get(c, 0)]
            row += [r["total_plants"]]
            w.writerow(row)
    return path


def save_centroids_csv(features, path, easting_name="x", northing_name="y"):
    with open(path, "w", newline="", encoding="utf-8") as cf:
        w = csv.writer(cf)
        w.writerow([easting_name, northing_name, "cls_id", "cls_name", "conf",
                    "plants_per_box", "plants", "coord_space"])
        for feat in features:
            ring = feat["geometry"]["coordinates"][0]
            xs = [p[0] for p in ring[:-1]]
            ys = [p[1] for p in ring[:-1]]
            cx, cy = sum(xs) / 4.0, sum(ys) / 4.0
            p = feat["properties"]
            w.writerow([cx, cy, p["cls_id"], p["cls_name"], p["conf"],
                        p["plants_per_box"], p["plants"], p.get("coord_space")])
    return path


def save_shapefile(features, path, epsg=None, crs_wkt=None):
    if not _HAS_FIONA:
        return None
    schema = {
        "geometry": "Polygon",
        "properties": {
            "cls_id": "int", "cls_name": "str", "conf": "float",
            "plants_pb": "int", "plants": "int", "src_image": "str",
        },
    }
    crs = from_epsg(int(epsg)) if epsg else (crs_wkt or None)
    with fiona.open(path, "w", driver="ESRI Shapefile", schema=schema, crs=crs) as dst:
        for feat in features:
            ring = feat["geometry"]["coordinates"][0]
            p = feat["properties"]
            dst.write({
                "geometry": {"type": "Polygon", "coordinates": [[tuple(pt) for pt in ring]]},
                "properties": {
                    "cls_id": int(p["cls_id"]),
                    "cls_name": str(p["cls_name"]),
                    "conf": float(p["conf"]),
                    "plants_pb": int(p["plants_per_box"]),
                    "plants": int(p["plants"]),
                    "src_image": str(p["source_image"]),
                },
            })
    return path


def reproject_features_to_utm(features, src_crs_obj, gt):
    """Returns (utm_features, utm_epsg) or (None, None) if pyproj unavailable."""
    if not _HAS_PYPROJ or src_crs_obj is None or not features:
        return None, None
    ring0 = features[0]["geometry"]["coordinates"][0]
    cx_map = sum(p[0] for p in ring0[:-1]) / 4.0
    cy_map = sum(p[1] for p in ring0[:-1]) / 4.0
    to_wgs = Transformer.from_crs(src_crs_obj, CRS.from_epsg(4326), always_xy=True)
    lon, lat = to_wgs.transform(cx_map, cy_map)
    utm_epsg = utm_epsg_from_lonlat(lon, lat)
    to_utm = Transformer.from_crs(src_crs_obj, CRS.from_epsg(utm_epsg), always_xy=True)

    out = []
    for feat in features:
        ring = feat["geometry"]["coordinates"][0]
        ring_utm = [list(to_utm.transform(x, y)) for (x, y) in ring]
        new_props = dict(feat["properties"])
        new_props["coord_space"] = f"UTM_{utm_epsg}"
        out.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring_utm]},
            "properties": new_props,
        })
    return out, utm_epsg


def render_preview(raster_src, roi_bbox_px, detections, model_names,
                   out_path, max_side=2400):
    """Render an annotated preview JPG of the ROI bbox (downsampled if huge)."""
    x1, y1, x2, y2 = [int(v) for v in roi_bbox_px]
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    long_side = max(w, h)
    scale = 1.0 if long_side <= max_side else max_side / long_side
    out_w, out_h = max(1, int(w * scale)), max(1, int(h * scale))

    from rasterio.windows import Window
    from rasterio.enums import Resampling
    bands = min(3, raster_src.count)
    idx = list(range(1, bands + 1))
    data = raster_src._ds.read(
        indexes=idx, window=Window(x1, y1, w, h),
        out_shape=(bands, out_h, out_w),
        resampling=Resampling.average,
    )
    rgb = raster_src._bands_to_rgb(data)
    bgr = rgb[:, :, ::-1].copy()

    for d in detections:
        cls_id = int(d["cls"])
        color = CLASS_COLORS.get(cls_id, (0, 255, 0))
        bx1 = int((d["xyxy"][0] - x1) * scale)
        by1 = int((d["xyxy"][1] - y1) * scale)
        bx2 = int((d["xyxy"][2] - x1) * scale)
        by2 = int((d["xyxy"][3] - y1) * scale)
        cv2.rectangle(bgr, (bx1, by1), (bx2, by2), color, max(1, int(2 * scale)))
        name = str(model_names.get(cls_id, f"class_{cls_id}"))
        cv2.putText(bgr, name, (bx1, max(0, by1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, max(0.3, 0.5 * scale),
                    _best_text_color(color), 1, cv2.LINE_AA)
    cv2.imwrite(out_path, bgr)
    return out_path


def export_all(detections, raster_src, model_names, roi_bbox_px, out_dir):
    """Write all output files; return dict of paths."""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(raster_src.path))[0]
    stamp = _ts()
    tag = f"{base}_roi_{int(roi_bbox_px[0])}_{int(roi_bbox_px[1])}_{int(roi_bbox_px[2])}_{int(roi_bbox_px[3])}_{stamp}"

    paths = {}
    feats_src = build_features(detections, raster_src, model_names)

    paths["geojson"] = save_geojson(feats_src, os.path.join(out_dir, f"{tag}.geojson"))
    paths["centroids_csv"] = save_centroids_csv(
        feats_src, os.path.join(out_dir, f"{tag}_centroids.csv")
    )

    if raster_src.crs_wkt:
        wkt_path = os.path.join(out_dir, f"{tag}.wkt")
        with open(wkt_path, "w", encoding="utf-8") as wf:
            wf.write(raster_src.crs_wkt)
        paths["crs_wkt"] = wkt_path

    if _HAS_PYPROJ and raster_src.crs:
        try:
            src_crs = CRS.from_user_input(raster_src.crs)
            utm_feats, utm_epsg = reproject_features_to_utm(
                feats_src, src_crs, raster_src.gdal_geotransform
            )
            if utm_feats:
                paths["utm_epsg"] = utm_epsg
                paths["utm_geojson"] = save_geojson(
                    utm_feats, os.path.join(out_dir, f"{tag}_utm{utm_epsg}.geojson")
                )
                paths["utm_centroids_csv"] = save_centroids_csv(
                    utm_feats,
                    os.path.join(out_dir, f"{tag}_utm{utm_epsg}_centroids.csv"),
                    easting_name="Easting", northing_name="Northing",
                )
                if _HAS_FIONA:
                    paths["utm_shapefile"] = save_shapefile(
                        utm_feats,
                        os.path.join(out_dir, f"{tag}_utm{utm_epsg}.shp"),
                        epsg=utm_epsg,
                    )
        except Exception as e:
            paths["utm_error"] = str(e)

    paths["preview_jpg"] = render_preview(
        raster_src, roi_bbox_px, detections, model_names,
        os.path.join(out_dir, f"{tag}_preview.jpg"),
    )

    return paths
