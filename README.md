# YOLO Stand Counting — Native Desktop App

A native Windows desktop application for counting plant stands in
**multi-gigabyte UAV/satellite orthomosaics** using a YOLO object-detection
model. Built with PySide6 + rasterio + Ultralytics. No web browser, no Flask
server — a single offline `.exe` you can ship to field collaborators.

This is a rewrite of the original Flask web app
(see [`source/YoloStandCounting-main/`](source/YoloStandCounting-main/))
that struggled with large orthomosaics because it tried to load the entire
raster into RAM.

---

## Why this rewrite?

The original Flask app crashed on real orthomosaics because:

1. `cv2.imread(...)` loads the **entire image** into RAM. A 30 000 × 30 000 px
   3-band orthomosaic is ~2.5 GB just decoded — that's before YOLO ever runs.
2. The browser-based Leaflet preview couldn't display huge GeoTIFFs.
3. Uploading multi-gigabyte rasters through a Flask `request.files` form is
   slow and memory-hungry.

This native app fixes all three:

- **Rasterio windowed reads** — only the tile being processed lives in RAM.
- **Decimated overview** for the on-screen map (≤ 4096 px on the long side,
  generated once per image open).
- **Direct file access** — no upload step. Point the app at a file path.

---

## Features

- Opens **any GDAL-supported raster** — GeoTIFF, BigTIFF, COG, JP2, PNG/JPG
  with worldfile (`.tfw` / `.pgw` / `.jgw` + optional `.prj`).
- **Pan / zoom map canvas** with a decimated overview of the full orthomosaic.
- **Three ways to define the ROI:**
  1. Draw a **rectangle** with the mouse.
  2. Draw a **polygon** (left-click vertices, right-click or double-click to
     close).
  3. **Load an ROI from a Shapefile (`.shp`) or File Geodatabase (`.gdb`)** —
     features are auto-reprojected from the ROI's CRS to the raster's CRS.
- **Tile-based YOLO inference** with class-wise NMS and a configurable
  core-margin filter, matching the original notebook/Flask behaviour.
- **Confidence threshold slider** (1 % – 99 %).
- Runs inference on a **background thread** with a tile-by-tile progress bar
  — the UI stays responsive.
- **Outputs:**
  - `*.geojson` — polygons in the source CRS (or image pixels if the raster
    has no CRS).
  - `*_utm<EPSG>.geojson` + `*_utm<EPSG>.shp` — auto-projected to the
    appropriate UTM zone for GIS use.
  - `*_centroids.csv` — one row per detected box (source CRS + UTM versions).
  - `*.wkt` — CRS WKT sidecar.
  - `*_preview.jpg` — annotated downsampled preview of the ROI.

---

## Requirements

- **Windows 10/11** (the `.bat` launchers target Windows; the Python code
  itself is cross-platform).
- **Python 3.12** (recommended) on `PATH` (for dev; the built `.exe`
  bundles its own runtime). Python 3.13 is too new — some scientific
  packages (`rasterio`, `fiona`, `torch`) don't yet have Python 3.13
  wheels on PyPI and pip will try (and fail) to build them from source.
  The launcher auto-prefers `py -3.12` if it's installed alongside other
  versions.
- ~3 GB free disk for the venv on first install (PyTorch CPU + Qt + GDAL).
- The trained model file `models/best.pt` (already included in the repo).

CPU-only by default. The bundled exe doesn't require a GPU.

---

## Quick start (development)

```cmd
git clone https://github.com/AliBgisrs/YoloStandCounting-V2.git
cd YoloStandCounting-V2
run.bat
```

The first launch:

1. Creates a virtual environment in `.venv\`.
2. `pip install -r requirements.txt` (this is the slow part — ~5–10 min,
   downloads ~1.5 GB of wheels).
3. Launches the native window.

Subsequent launches start in < 5 s.

To force a fresh dependency install, delete `.venv\.installed`.

---

## Building a redistributable EXE

```cmd
build.bat
```

This runs PyInstaller in `--onedir` mode and produces:

```
dist\
  StandCounting\
    StandCounting.exe
    models\best.pt
    _internal\          (Qt, GDAL, PyTorch, Ultralytics binaries)
    ...
```

Ship the **entire `dist\StandCounting\` folder** (zip it). End users just
double-click `StandCounting.exe` — no Python install required.

Expect a bundle size of **1.5 – 2 GB**. This is normal for any app that
combines PyTorch + GDAL + Qt; it's not something the build script can shrink
meaningfully without sacrificing features. Single-file mode (`--onefile`) is
intentionally avoided because rasterio/fiona's GDAL data files are fragile
inside the self-extracting wrapper.

---

## How to use the app

1. **File → Open Orthomosaic…** Pick your `.tif` / `.tiff` / `.png` / etc.
   The status bar will show pixel dimensions, band count, and CRS.
2. Pick an ROI:
   - **Toolbar → Rect ROI**, then click-drag a rectangle on the overview, **or**
   - **Toolbar → Polygon ROI**, then left-click each vertex and
     right-click (or double-click) to close, **or**
   - **File → Open ROI from Shapefile/GDB…** Select a `.shp` file or a
     `.gdb` folder. For a `.gdb`, you'll be prompted to pick a layer. The
     polygon(s) are reprojected to the raster's CRS automatically and shown
     in blue on the map.
3. Set the **Confidence threshold** slider (default 20 %).
4. (Optional) **Choose** a different **Output directory** in the right-hand
   dock. Default is `outputs\` next to `run.bat` / the exe.
5. Click **Analyze ROI**. Watch the progress bar — each step is one
   896 × 896 tile read from disk and pushed through YOLO.
6. When done, the **Results** table shows boxes / plants per class and the
   total. The **Exports** label lists every file that was written.

---

## Output file layout

```
outputs\
  <image>_roi_<x1>_<y1>_<x2>_<y2>_<timestamp>.geojson
  <image>_roi_..._centroids.csv
  <image>_roi_....wkt                              (if CRS has no EPSG)
  <image>_roi_..._utm<EPSG>.geojson                (if raster is georeferenced)
  <image>_roi_..._utm<EPSG>.shp + .shx + .dbf + .prj
  <image>_roi_..._utm<EPSG>_centroids.csv
  <image>_roi_..._preview.jpg
```

The annotated preview JPG is downsampled to ≤ 2400 px on the long side so
even a 50 000 × 50 000 px ROI produces a viewable image, not a 4 GB JPG.

---

## Project structure

```
StandCountingV2\
  app\
    main.py            — Qt entry point
    main_window.py     — menus, toolbar, dock panel, threaded analysis
    canvas.py          — QGraphicsView; pan/zoom, rectangle + polygon ROI
    raster_io.py       — rasterio wrapper: overview + windowed reads
    inference.py       — tile-based YOLO; class-wise NMS; core-margin filter
    export.py          — GeoJSON, Shapefile, CSV, annotated preview JPG
    roi_loader.py      — Shapefile / GDB → polygon in pixel coords
    georef.py          — worldfile parsing, UTM helpers, map ↔ pixel math
  models\
    best.pt            — trained YOLO weights (10 MB)
  source\
    YoloStandCounting-main\   — original Flask app, preserved for reference
  requirements.txt
  run.bat              — development launcher (creates venv, installs, runs)
  build.bat            — PyInstaller build → dist\StandCounting\
  .gitignore
  README.md
```

---

## Model details

- 3 classes encoded as **plants-per-box** (default mapping in
  [`app/inference.py`](app/inference.py)):

  | Class ID | Plants/box | Default colour |
  |---------:|-----------:|----------------|
  | 0        | 1          | yellow         |
  | 1        | 2          | navy           |
  | 2        | 3          | white          |

- **Tiling defaults** (also in `inference.py`): 896 px tiles with 224 px
  overlap, 32 px core margin to suppress duplicates at tile seams, IoU
  threshold 0.65 for class-wise NMS.

If you retrain the model with a different class scheme, update
`PLANTS_PER_CLASS` and `CLASS_COLORS` at the top of
[`app/inference.py`](app/inference.py).

---

## Notes & known limitations

- **`.gdb` is read-only.** GDAL's `OpenFileGDB` driver can read modern File
  Geodatabases but writing requires Esri's proprietary `FileGDB` SDK, which
  isn't bundled. Results are exported as Shapefile + GeoJSON instead.
- **Georeferencing without an EPSG code:** if your raster has a CRS WKT but
  no EPSG match (e.g. some custom local projections), the source-CRS
  GeoJSON is still written and a `.wkt` sidecar is saved, but the UTM
  reprojection is skipped.
- **No-CRS rasters:** if the raster has no CRS at all (plain PNG without a
  worldfile), outputs are in **image pixel coordinates** and the ROI from
  a Shapefile / GDB will be treated as pixel coords too (probably wrong —
  reproject your raster or ROI first).
- **GPU inference** is not enabled in the default `requirements.txt` /
  `build.bat`. To enable CUDA, install the matching PyTorch wheel manually
  and rebuild. This will roughly double the bundle size.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|---------------------|
| `run.bat` says "Python is not on PATH" | Install Python 3.10+ and tick "Add Python to PATH" during setup. |
| First launch hangs on "Installing requirements" | Normal — large wheels (`torch`, `ultralytics`, `rasterio`) are downloading. Watch the console. |
| App opens but the map is blank | Open menu **File → Open Orthomosaic…** first. |
| "Could not open raster" | The file is not a GDAL-recognised format, or it's locked by another program. |
| `models\best.pt` missing | Pull the repo with LFS / re-download — the file is tracked normally and should be ~10 MB. |
| PyInstaller exe crashes on launch with a `rasterio._shim` import error | Re-run `build.bat`. If it persists, edit `build.bat` and add the missing module name to the `--hidden-import` list. |
| Analysis runs but finds 0 detections | Lower the confidence slider; verify you opened the correct image; verify the ROI is over actual plant rows. |

---

## License

The application code in this repo is published under the same terms as the
upstream project. The bundled YOLO weights (`models/best.pt`) are the
author's own trained model — please do not redistribute outside this repo
without permission.

---

## Acknowledgements

- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) for the
  detection engine.
- [Rasterio](https://rasterio.readthedocs.io/) and
  [Fiona](https://fiona.readthedocs.io/) for the GDAL bindings that make
  windowed reads and Shapefile/GDB I/O painless.
- [PySide6](https://doc.qt.io/qtforpython/) for the native UI.
