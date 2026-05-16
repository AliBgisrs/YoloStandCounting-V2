"""Georeferencing helpers. Ported from the original Flask app."""
import math
import os

WORLD_FILE_EXTS = {
    ".tif": ".tfw", ".tiff": ".tfw",
    ".jpg": ".jgw", ".jpeg": ".jgw",
    ".png": ".pgw", ".bmp": ".bpw",
}


def read_worldfile(img_path):
    """Return (gt, epsg, crs_wkt) from a sidecar worldfile + optional .prj."""
    base, ext = os.path.splitext(img_path)
    wf_ext = WORLD_FILE_EXTS.get(ext.lower())
    if not wf_ext:
        return None, None, None
    wf_path = base + wf_ext
    if not os.path.exists(wf_path):
        return None, None, None
    try:
        with open(wf_path, "r", encoding="utf-8") as f:
            vals = [float(l.strip()) for l in f.readlines()[:6]]
        if len(vals) != 6:
            return None, None, None
        A, D, B, E, C, F = vals
        gt = [C - 0.5 * A - 0.5 * B, A, B, F - 0.5 * D - 0.5 * E, D, E]

        epsg, crs_wkt = None, None
        prj_path = base + ".prj"
        if os.path.exists(prj_path):
            with open(prj_path, "r", encoding="utf-8") as pf:
                crs_wkt = pf.read()
            try:
                from pyproj import CRS
                epsg = CRS.from_wkt(crs_wkt).to_epsg()
            except Exception:
                epsg = None
        return gt, epsg, crs_wkt
    except Exception:
        return None, None, None


def to_map(px, py, gt):
    """Pixel → map coords using a 6-element GDAL geotransform."""
    GT0, GT1, GT2, GT3, GT4, GT5 = gt
    return float(GT0 + px * GT1 + py * GT2), float(GT3 + px * GT4 + py * GT5)


def map_to_pixel(x, y, gt):
    """Inverse of to_map for affine (no rotation/shear-safe enough for orthos)."""
    GT0, GT1, GT2, GT3, GT4, GT5 = gt
    det = GT1 * GT5 - GT2 * GT4
    if det == 0:
        raise ValueError("Singular geotransform")
    px = ((x - GT0) * GT5 - (y - GT3) * GT2) / det
    py = ((y - GT3) * GT1 - (x - GT0) * GT4) / det
    return px, py


def utm_epsg_from_lonlat(lon, lat):
    zone = int(math.floor((lon + 180.0) / 6.0) + 1)
    return (32600 if lat >= 0 else 32700) + zone
