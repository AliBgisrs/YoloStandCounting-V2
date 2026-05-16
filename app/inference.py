"""YOLO tile-based inference over an ROI polygon, driven by rasterio windows.

The full ROI is never materialised — each tile is read directly from the source
file. Detections are stitched in full-res pixel coords, NMS'd class-wise, then
filtered so only detections whose centers lie inside the ROI polygon are kept.
"""
import numpy as np
from shapely.geometry import box, Point


TILE_SIZE = 896
TILE_OVERLAP = 224
CORE_MARGIN = 32
IOU_NMS = 0.65

PLANTS_PER_CLASS = {0: 1, 1: 2, 2: 3}
CLASS_COLORS = {
    0: (0, 255, 255),
    1: (128, 0, 0),
    2: (255, 255, 255),
    3: (0, 255, 0),
}


def box_iou(a, b):
    xx1 = max(a[0], b[0]); yy1 = max(a[1], b[1])
    xx2 = min(a[2], b[2]); yy2 = min(a[3], b[3])
    iw = max(0.0, xx2 - xx1); ih = max(0.0, yy2 - yy1)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def nms_classwise(dets, iou_thr=IOU_NMS):
    by_cls, out = {}, []
    for d in dets:
        by_cls.setdefault(d["cls"], []).append(d)
    for items in by_cls.values():
        items = sorted(items, key=lambda d: d["conf"], reverse=True)
        keep = []
        while items:
            best = items.pop(0)
            keep.append(best)
            items = [d for d in items if box_iou(best["xyxy"], d["xyxy"]) < iou_thr]
        out.extend(keep)
    return out


def _iter_tile_origins(minx, miny, maxx, maxy, tile, overlap):
    stride = max(1, tile - overlap)
    xs = list(range(minx, maxx, stride))
    ys = list(range(miny, maxy, stride))
    if xs and xs[-1] + tile < maxx:
        xs.append(max(minx, maxx - tile))
    if ys and ys[-1] + tile < maxy:
        ys.append(max(miny, maxy - tile))
    for y in ys:
        for x in xs:
            yield x, y


def analyze(raster_src, roi_polygon_px, model, conf_min=0.20,
            tile=TILE_SIZE, overlap=TILE_OVERLAP, core_margin=CORE_MARGIN,
            progress_cb=None):
    """Run inference over the ROI.

    raster_src: RasterSource (full-res pixel coords).
    roi_polygon_px: shapely Polygon/MultiPolygon in image-pixel coords.
    Returns list of detections [{cls, conf, xyxy}] in full-res pixel coords.
    """
    minx, miny, maxx, maxy = roi_polygon_px.bounds
    minx = max(0, int(np.floor(minx)))
    miny = max(0, int(np.floor(miny)))
    maxx = min(raster_src.width, int(np.ceil(maxx)))
    maxy = min(raster_src.height, int(np.ceil(maxy)))
    if maxx <= minx or maxy <= miny:
        return []

    origins = list(_iter_tile_origins(minx, miny, maxx, maxy, tile, overlap))
    total = len(origins)
    collected = []

    for i, (x, y) in enumerate(origins):
        tw = min(tile, maxx - x)
        th = min(tile, maxy - y)
        tile_box = box(x, y, x + tw, y + th)
        if not roi_polygon_px.intersects(tile_box):
            if progress_cb:
                progress_cb(i + 1, total)
            continue

        rgb = raster_src.read_window_rgb(x, y, tile, tile)
        bgr = rgb[:, :, ::-1]
        res = model.predict(bgr, imgsz=tile, conf=0.0, verbose=False)
        if not res or getattr(res[0], "boxes", None) is None:
            if progress_cb:
                progress_cb(i + 1, total)
            continue

        for b in res[0].boxes:
            cls_id = int(b.cls[0]) if b.cls is not None else -1
            conf = float(b.conf[0]) if b.conf is not None else 0.0
            x1b, y1b, x2b, y2b = [float(v) for v in b.xyxy[0].tolist()]
            cx, cy = 0.5 * (x1b + x2b), 0.5 * (y1b + y2b)

            on_edge_x = (x == minx and cx <= core_margin) or (x + tile >= maxx and cx >= tile - core_margin)
            on_edge_y = (y == miny and cy <= core_margin) or (y + tile >= maxy and cy >= tile - core_margin)
            in_core = (core_margin <= cx <= tile - core_margin) and (core_margin <= cy <= tile - core_margin)
            if not (in_core or on_edge_x or on_edge_y):
                continue

            abs_x1, abs_y1 = x1b + x, y1b + y
            abs_x2, abs_y2 = x2b + x, y2b + y
            ax, ay = 0.5 * (abs_x1 + abs_x2), 0.5 * (abs_y1 + abs_y2)
            if not roi_polygon_px.contains(Point(ax, ay)):
                continue

            collected.append({"cls": cls_id, "conf": conf,
                              "xyxy": [abs_x1, abs_y1, abs_x2, abs_y2]})

        if progress_cb:
            progress_cb(i + 1, total)

    collected = nms_classwise(collected, iou_thr=IOU_NMS)
    collected = [d for d in collected if d["conf"] >= conf_min and d["cls"] in PLANTS_PER_CLASS]
    return collected


def count(detections):
    """Return (class_box_counts, per_class_plants, total_plants)."""
    class_box_counts = {c: 0 for c in PLANTS_PER_CLASS}
    for d in detections:
        c = int(d["cls"])
        if c in class_box_counts:
            class_box_counts[c] += 1
    per_class_plants = {c: class_box_counts[c] * PLANTS_PER_CLASS[c] for c in PLANTS_PER_CLASS}
    total = sum(per_class_plants.values())
    return class_box_counts, per_class_plants, total


def count_per_plot(detections, plots):
    """Match each detection's center to a plot polygon, return per-plot counts.

    plots: list of {'plot_id': str, 'polygon_px': shapely Polygon/MultiPolygon}.
    Returns list of dicts (same order as plots):
        {'plot_id', 'class_box_counts': {cls: n}, 'per_class_plants': {cls: n},
         'total_plants', 'unmatched'}
    Plus a final 'unmatched' tally for detections that fell outside every plot.
    """
    from shapely.geometry import Point

    results = []
    for p in plots:
        results.append({
            "plot_id": p["plot_id"],
            "class_box_counts": {c: 0 for c in PLANTS_PER_CLASS},
            "polygon_px": p["polygon_px"],
        })
    unmatched = 0

    for d in detections:
        x1, y1, x2, y2 = d["xyxy"]
        pt = Point((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        cls = int(d["cls"])
        if cls not in PLANTS_PER_CLASS:
            continue
        hit = False
        for r in results:
            if r["polygon_px"].contains(pt):
                r["class_box_counts"][cls] += 1
                hit = True
                break
        if not hit:
            unmatched += 1

    for r in results:
        r["per_class_plants"] = {
            c: r["class_box_counts"][c] * PLANTS_PER_CLASS[c] for c in PLANTS_PER_CLASS
        }
        r["total_plants"] = sum(r["per_class_plants"].values())
        r.pop("polygon_px", None)  # don't expose geometry in the result

    return results, unmatched
