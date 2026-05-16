"""Load ROI polygons from a Shapefile or File Geodatabase and convert to image-pixel coords.

Read-only — relies on GDAL's OpenFileGDB driver, which is bundled with modern
rasterio/fiona wheels. Writing to .gdb requires Esri's proprietary FileGDB API
and is intentionally not supported here.
"""
import os
import fiona
from shapely.geometry import shape, MultiPolygon, Polygon
from shapely.ops import unary_union, transform as shp_transform

try:
    from pyproj import CRS, Transformer
    _HAS_PYPROJ = True
except Exception:
    _HAS_PYPROJ = False

from .georef import map_to_pixel


# Candidate field names for "plot id", checked case-insensitively in this order.
PLOT_ID_FIELDS = ["plotid", "plot_id", "plot", "id", "fid", "plotnum", "plot_num"]


def _open_layer(path, layer=None):
    """Open a shapefile (.shp) or GDB (.gdb) layer for read."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gdb" or path.endswith(".gdb"):
        layers = fiona.listlayers(path)
        if not layers:
            raise ValueError(f"No layers found in GDB: {path}")
        chosen = layer or layers[0]
        return fiona.open(path, layer=chosen, driver="OpenFileGDB")
    return fiona.open(path, "r")


def list_layers(path):
    """Return list of layer names; useful for GDBs."""
    try:
        return fiona.listlayers(path)
    except Exception:
        return [os.path.basename(path)]


def _pick_id_field(schema_props):
    """Return the schema field name to use as plot ID, or None."""
    if not schema_props:
        return None
    lower_map = {k.lower(): k for k in schema_props.keys()}
    for cand in PLOT_ID_FIELDS:
        if cand in lower_map:
            return lower_map[cand]
    return None


def _reproject_pixel(geom, raster_src, src_crs_data):
    """Reproject a single geometry from source CRS → raster CRS → pixel coords."""
    if raster_src.crs is not None and _HAS_PYPROJ and src_crs_data:
        try:
            src_crs = CRS.from_wkt(src_crs_data) if isinstance(src_crs_data, str) else CRS.from_user_input(src_crs_data)
            dst_crs = CRS.from_user_input(raster_src.crs)
            if src_crs != dst_crs:
                tf = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
                geom = shp_transform(lambda x, y, z=None: tf.transform(x, y), geom)
        except Exception:
            pass
    gt = raster_src.gdal_geotransform
    return shp_transform(lambda x, y, z=None: map_to_pixel(x, y, gt), geom)


def load_roi_with_plots(roi_path, raster_src, layer=None):
    """Read polygon features; return (merged_pixel_multipolygon, plots).

    plots is a list of dicts, one per input feature:
        {'plot_id': str, 'polygon_px': shapely Polygon/MultiPolygon}

    plot_id is taken from the first attribute matching PlotID / ID / plot
    (case-insensitive). If no such field exists, plots are auto-numbered.
    """
    plots = []
    geoms_for_merge = []

    with _open_layer(roi_path, layer) as src:
        src_crs_data = src.crs_wkt or (src.crs and CRS.from_dict(src.crs).to_wkt())
        schema_props = (src.schema or {}).get("properties", {}) or {}
        id_field = _pick_id_field(schema_props)

        for i, feat in enumerate(src, start=1):
            g = shape(feat["geometry"])
            if g.is_empty:
                continue
            if g.geom_type not in ("Polygon", "MultiPolygon"):
                if not g.is_valid:
                    g = g.buffer(0)
                if g.geom_type not in ("Polygon", "MultiPolygon"):
                    continue
            geoms_for_merge.append(g)

            if id_field:
                raw = feat["properties"].get(id_field)
                plot_id = str(raw) if raw is not None else f"plot_{i}"
            else:
                plot_id = f"plot_{i}"

            poly_px = _reproject_pixel(g, raster_src, src_crs_data)
            plots.append({"plot_id": plot_id, "polygon_px": poly_px})

    if not geoms_for_merge:
        raise ValueError("No polygon features found in ROI file.")

    merged = unary_union(geoms_for_merge)
    merged_px = _reproject_pixel(merged, raster_src, src_crs_data)
    if merged_px.geom_type == "Polygon":
        merged_px = MultiPolygon([merged_px])

    return merged_px, plots, id_field


def load_roi_as_pixel_polygon(roi_path, raster_src, layer=None):
    """Backward-compatible single-polygon loader."""
    merged_px, _plots, _id_field = load_roi_with_plots(roi_path, raster_src, layer)
    return merged_px
