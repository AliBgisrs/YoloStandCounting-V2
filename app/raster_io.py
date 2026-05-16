"""Rasterio wrapper for large orthomosaics.

Opens any raster format supported by GDAL (GeoTIFF, BigTIFF, PNG+worldfile, JPG,
etc.) and exposes overview generation + windowed full-res reads without ever
loading the whole image into memory.
"""
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.enums import Resampling


class RasterSource:
    """Lazy wrapper around a rasterio dataset."""

    def __init__(self, path):
        self.path = path
        self._ds = rasterio.open(path)
        self.width = self._ds.width
        self.height = self._ds.height
        self.count = self._ds.count
        self.transform = self._ds.transform
        self.crs = self._ds.crs

    def close(self):
        try:
            self._ds.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    @property
    def gdal_geotransform(self):
        t = self.transform
        return [t.c, t.a, t.b, t.f, t.d, t.e]

    @property
    def crs_wkt(self):
        try:
            return self._ds.crs.to_wkt() if self._ds.crs else None
        except Exception:
            return None

    @property
    def epsg(self):
        try:
            return self._ds.crs.to_epsg() if self._ds.crs else None
        except Exception:
            return None

    def overview(self, max_side=8192):
        """Decimated RGB array (H, W, 3) uint8 + scale (overview_px / full_px)."""
        long_side = max(self.width, self.height)
        scale = 1.0
        if long_side > max_side:
            scale = max_side / long_side
        out_w = max(1, int(round(self.width * scale)))
        out_h = max(1, int(round(self.height * scale)))

        bands = min(3, self.count)
        idx = list(range(1, bands + 1))
        data = self._ds.read(
            indexes=idx,
            out_shape=(bands, out_h, out_w),
            resampling=Resampling.average,
        )
        rgb = self._bands_to_rgb(data)
        return rgb, scale, (out_w, out_h)

    def read_window_rgb(self, x, y, w, h):
        """Full-res RGB tile (uint8) inside the image bounds; clipped/padded as needed."""
        x = max(0, int(x))
        y = max(0, int(y))
        x2 = min(self.width, int(x + w))
        y2 = min(self.height, int(y + h))
        rw = max(0, x2 - x)
        rh = max(0, y2 - y)
        if rw == 0 or rh == 0:
            return np.zeros((int(h), int(w), 3), dtype=np.uint8)

        bands = min(3, self.count)
        idx = list(range(1, bands + 1))
        data = self._ds.read(indexes=idx, window=Window(x, y, rw, rh))
        rgb = self._bands_to_rgb(data)

        if rw != w or rh != h:
            padded = np.zeros((int(h), int(w), 3), dtype=np.uint8)
            padded[:rh, :rw] = rgb
            return padded
        return rgb

    @staticmethod
    def _bands_to_rgb(data):
        """(bands, H, W) → (H, W, 3) uint8."""
        if data.dtype != np.uint8:
            mn, mx = float(data.min()), float(data.max())
            rng = mx - mn if mx > mn else 1.0
            data = ((data.astype(np.float32) - mn) / rng * 255.0).clip(0, 255).astype(np.uint8)
        if data.shape[0] == 1:
            rgb = np.repeat(data, 3, axis=0)
        elif data.shape[0] == 2:
            rgb = np.concatenate([data, data[:1]], axis=0)
        else:
            rgb = data[:3]
        return np.transpose(rgb, (1, 2, 0)).copy()
