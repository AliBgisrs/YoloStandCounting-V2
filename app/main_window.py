"""Main application window."""
import os
import sys
import traceback

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QUrl
from PySide6.QtGui import QAction, QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QMessageBox, QToolBar, QDockWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, QTableWidget,
    QTableWidgetItem, QProgressBar, QHeaderView, QCheckBox, QComboBox,
    QScrollArea, QFrame,
)

from .canvas import MapCanvas, MODE_PAN, MODE_RECT, MODE_POLY
from .raster_io import RasterSource
from .roi_loader import load_roi_with_plots, list_layers
from .inference import analyze, count, count_per_plot, PLANTS_PER_CLASS
from .export import export_all, save_plot_counts_csv


def _default_model_path():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "models", "best.pt"),
        os.path.join(os.getcwd(), "models", "best.pt"),
    ]
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.join(os.path.dirname(sys.executable), "models", "best.pt"))
    for p in candidates:
        p = os.path.abspath(p)
        if os.path.exists(p):
            return p
    return os.path.abspath(candidates[0])


class AnalyzeWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(object, object, str)  # detections, paths, error

    def __init__(self, raster_path, roi_poly, model, conf_min, out_dir):
        super().__init__()
        self.raster_path = raster_path
        self.roi_poly = roi_poly
        self.model = model
        self.conf_min = conf_min
        self.out_dir = out_dir

    @Slot()
    def run(self):
        try:
            with RasterSource(self.raster_path) as rs:
                dets = analyze(rs, self.roi_poly, self.model, conf_min=self.conf_min,
                               progress_cb=lambda i, n: self.progress.emit(i, n))
                names = getattr(self.model, "names", {}) or {}
                bbox = self.roi_poly.bounds
                paths = export_all(dets, rs, names, bbox, self.out_dir)
            self.finished.emit(dets, paths, "")
        except Exception:
            self.finished.emit([], {}, traceback.format_exc())


APP_AUTHOR = "Ali Bazrafkan"
APP_EMAIL = "bazrafka@msu.edu"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"YOLO Stand Counting (Native) - by {APP_AUTHOR}")
        self.resize(1400, 900)

        self.raster_path = None
        self.roi_poly = None
        self.plots = None        # list of {'plot_id', 'polygon_px'} when ROI came from a shapefile/GDB
        self.id_field = None     # name of the field used as plot id (None if auto-numbered)
        self.model = None
        self._thread = None
        self._worker = None
        self._last_out_dir = None

        self.canvas = MapCanvas(self)
        self.setCentralWidget(self.canvas)
        self.canvas.roi_changed.connect(self.on_roi_changed)

        self._build_menu()
        self._build_toolbar()
        self._build_dock()
        self._build_statusbar()

    # ---------- UI construction ----------
    def _build_menu(self):
        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        act_open = QAction("&Open Orthomosaic...", self)
        act_open.triggered.connect(self.action_open_image)
        m_file.addAction(act_open)
        act_roi = QAction("Open ROI from &Shapefile/GDB...", self)
        act_roi.triggered.connect(self.action_open_roi)
        m_file.addAction(act_roi)
        m_file.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_tools = mb.addMenu("&Tools")
        for label, mode in [("Pan", MODE_PAN), ("Draw Rectangle", MODE_RECT), ("Draw Polygon", MODE_POLY)]:
            a = QAction(label, self)
            a.triggered.connect(lambda _=False, m=mode: self.canvas.set_mode(m))
            m_tools.addAction(a)
        m_tools.addSeparator()
        a_clear = QAction("Clear ROI", self)
        a_clear.triggered.connect(self.canvas.clear_roi)
        m_tools.addAction(a_clear)

        m_help = mb.addMenu("&Help")
        a_about = QAction("&About", self)
        a_about.triggered.connect(self.action_about)
        m_help.addAction(a_about)

    def _build_toolbar(self):
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction("Open Image", self.action_open_image)
        tb.addAction("Open ROI (SHP/GDB)", self.action_open_roi)
        tb.addSeparator()
        tb.addAction("Pan", lambda: self.canvas.set_mode(MODE_PAN))
        tb.addAction("Rect ROI", lambda: self.canvas.set_mode(MODE_RECT))
        tb.addAction("Polygon ROI", lambda: self.canvas.set_mode(MODE_POLY))
        tb.addAction("Clear ROI", self.canvas.clear_roi)
        tb.addSeparator()
        self.act_analyze = tb.addAction("Analyze ROI", self.action_analyze)
        self.act_analyze.setEnabled(False)

    def _build_dock(self):
        dock = QDockWidget("Settings & Results", self)
        dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        w = QWidget(dock)
        v = QVBoxLayout(w)

        # confidence
        v.addWidget(QLabel("Confidence threshold"))
        h = QHBoxLayout()
        self.conf_slider = QSlider(Qt.Horizontal)
        self.conf_slider.setRange(1, 99)
        self.conf_slider.setValue(20)
        self.conf_label = QLabel("20%")
        self.conf_slider.valueChanged.connect(lambda v_: self.conf_label.setText(f"{v_}%"))
        h.addWidget(self.conf_slider); h.addWidget(self.conf_label)
        v.addLayout(h)

        # output dir
        v.addWidget(QLabel("Output directory"))
        h2 = QHBoxLayout()
        self.out_dir_label = QLabel(self._default_out_dir())
        self.out_dir_label.setWordWrap(True)
        btn_out = QPushButton("Choose...")
        btn_out.clicked.connect(self.action_pick_out_dir)
        h2.addWidget(self.out_dir_label, 1); h2.addWidget(btn_out)
        v.addLayout(h2)

        # analyze button
        self.btn_analyze = QPushButton("Analyze ROI")
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.clicked.connect(self.action_analyze)
        v.addWidget(self.btn_analyze)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        v.addWidget(self.progress)

        # Class results table — sized to fit all 3 rows + header without scrolling.
        v.addWidget(QLabel("Results - by class"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Class", "Name", "Boxes", "Plants/Box", "Plants"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        # 3 class rows + header ≈ 4 * 28 px + small padding
        self.table.setFixedHeight(28 * 4 + 8)
        v.addWidget(self.table)

        self.total_label = QLabel("Total plants: -")
        f = self.total_label.font(); f.setPointSize(f.pointSize() + 1); f.setBold(True)
        self.total_label.setFont(f)
        v.addWidget(self.total_label)

        # Per-plot table — populated only when an ROI came from a shapefile/GDB.
        self.plot_header_label = QLabel("Results - per plot")
        v.addWidget(self.plot_header_label)
        self.plot_table = QTableWidget(0, 0)
        self.plot_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.plot_table.verticalHeader().setVisible(False)
        self.plot_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.plot_table.setAlternatingRowColors(True)
        self.plot_table.setMinimumHeight(140)
        v.addWidget(self.plot_table, 1)
        # hidden until a shapefile ROI is loaded
        self.plot_header_label.setVisible(False)
        self.plot_table.setVisible(False)

        # annotated preview image
        v.addWidget(QLabel("Annotated preview"))
        self.preview_label = QLabel("(run analysis to see preview)")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(220)
        self.preview_label.setFrameShape(QFrame.StyledPanel)
        self.preview_label.setStyleSheet("background:#222; color:#aaa;")
        self.preview_label.setCursor(Qt.PointingHandCursor)
        self.preview_label.mousePressEvent = self._open_preview_externally
        v.addWidget(self.preview_label, 2)
        self._preview_pixmap = None
        self._preview_path = None

        # Compact outputs row: 1-line summary + button.
        out_row = QHBoxLayout()
        self.export_summary = QLabel("Outputs: (none yet)")
        self.export_summary.setStyleSheet("color:#555;")
        out_row.addWidget(self.export_summary, 1)
        btn_open_dir = QPushButton("Open outputs folder")
        btn_open_dir.clicked.connect(self._open_output_dir)
        out_row.addWidget(btn_open_dir)
        v.addLayout(out_row)

        # footer attribution
        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setFrameShadow(QFrame.Sunken)
        v.addWidget(sep)
        footer = QLabel(f"Developed by {APP_AUTHOR} - <a href='mailto:{APP_EMAIL}'>{APP_EMAIL}</a>")
        footer.setOpenExternalLinks(True)
        footer.setTextFormat(Qt.RichText)
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color:#666; font-size: 10px;")
        v.addWidget(footer)

        dock.setWidget(w)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.setMinimumWidth(380)

    def _build_statusbar(self):
        self.statusBar().showMessage("Ready. Open an orthomosaic to begin.")
        # permanent right-side attribution
        attrib = QLabel(f"Developed by {APP_AUTHOR} ({APP_EMAIL})")
        attrib.setStyleSheet("color:#666; padding:0 8px;")
        self.statusBar().addPermanentWidget(attrib)

    def action_about(self):
        QMessageBox.about(
            self,
            "About YOLO Stand Counting",
            f"<h3>YOLO Stand Counting (Native)</h3>"
            f"<p>Native desktop app for counting plant stands in large UAV/satellite "
            f"orthomosaics using a YOLO detection model.</p>"
            f"<p><b>Developed by {APP_AUTHOR}</b><br>"
            f"<a href='mailto:{APP_EMAIL}'>{APP_EMAIL}</a></p>"
            f"<p style='color:#666;'>Michigan State University</p>"
        )

    def _open_preview_externally(self, *_):
        if not self._preview_path or not os.path.exists(self._preview_path):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._preview_path))

    def _open_output_dir(self):
        d = self._last_out_dir or self.out_dir_label.text()
        if d and os.path.exists(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_preview()

    def _rescale_preview(self):
        if self._preview_pixmap is None:
            return
        w = max(100, self.preview_label.width() - 8)
        h = max(100, self.preview_label.height() - 8)
        self.preview_label.setPixmap(
            self._preview_pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _default_out_dir(self):
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        out = os.path.join(base, "outputs")
        os.makedirs(out, exist_ok=True)
        return out

    # ---------- Actions ----------
    def action_open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open orthomosaic",
            filter="Rasters (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All files (*.*)",
        )
        if not path:
            return
        try:
            self.statusBar().showMessage(f"Opening {os.path.basename(path)} ...")
            with RasterSource(path) as rs:
                rgb, scale, (ow, oh) = rs.overview(max_side=4096)
                crs_text = f"EPSG:{rs.epsg}" if rs.epsg else ("WKT" if rs.crs_wkt else "no CRS")
                self.statusBar().showMessage(
                    f"{os.path.basename(path)} — {rs.width}×{rs.height} px, {rs.count} bands, {crs_text}. "
                    f"Overview {ow}×{oh}."
                )
            self.canvas.set_overview(rgb, scale)
            self.raster_path = path
            self.roi_poly = None
            self._refresh_analyze_enabled()
            self.canvas.set_mode(MODE_RECT)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", f"Could not open raster:\n{e}")

    def action_open_roi(self):
        if not self.raster_path:
            QMessageBox.information(self, "Load image first", "Open an orthomosaic before loading an ROI.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ROI",
            filter="Vector ROI (*.shp *.gdb);;Shapefile (*.shp);;Geodatabase (*.gdb);;All files (*.*)",
        )
        if not path:
            return
        layer = None
        if path.lower().endswith(".gdb") or os.path.isdir(path) and path.endswith(".gdb"):
            try:
                layers = list_layers(path)
            except Exception as e:
                QMessageBox.critical(self, "GDB error", f"Could not list layers:\n{e}")
                return
            if not layers:
                QMessageBox.critical(self, "GDB error", "No layers found.")
                return
            layer = self._pick_layer(layers)
            if layer is None:
                return
        try:
            with RasterSource(self.raster_path) as rs:
                if rs.crs is None:
                    QMessageBox.warning(
                        self, "No raster CRS",
                        "The raster has no CRS — ROI coordinates will be treated as if they were already in pixel space, which is probably wrong. Reproject your raster or ROI first.",
                    )
                poly, plots, id_field = load_roi_with_plots(path, rs, layer=layer)
            self.canvas.show_polygon_from_full_res(poly)
            self.roi_poly = poly
            self.plots = plots
            self.id_field = id_field
            # Show plot table only when we have >1 plot.
            multi = len(plots) > 1
            self.plot_header_label.setVisible(multi)
            self.plot_table.setVisible(multi)
            if multi:
                src = id_field or "auto-numbered"
                self.plot_header_label.setText(f"Results - per plot ({len(plots)} plots, ID field: {src})")
            self._refresh_analyze_enabled()
            self.statusBar().showMessage(
                f"ROI loaded from {os.path.basename(path)} - {len(plots)} feature(s)."
                + (f"  Plot ID field: {id_field}." if id_field else "  No plot-ID field found; auto-numbered.")
            )
        except Exception as e:
            QMessageBox.critical(self, "ROI load failed", f"{e}\n\n{traceback.format_exc()}")

    def _pick_layer(self, layers):
        from PySide6.QtWidgets import QInputDialog
        item, ok = QInputDialog.getItem(self, "Select GDB layer", "Layer:", layers, 0, False)
        return item if ok else None

    def action_pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output directory", self.out_dir_label.text())
        if d:
            self.out_dir_label.setText(d)

    @Slot(object)
    def on_roi_changed(self, poly):
        self.roi_poly = poly
        # User drew an ROI by hand — drop any per-plot context from a previous shapefile load.
        self.plots = None
        self.id_field = None
        self.plot_header_label.setVisible(False)
        self.plot_table.setVisible(False)
        self._refresh_analyze_enabled()

    def _refresh_analyze_enabled(self):
        ok = self.raster_path is not None and self.roi_poly is not None and not self.roi_poly.is_empty
        self.btn_analyze.setEnabled(ok)
        self.act_analyze.setEnabled(ok)

    # ---------- Inference ----------
    def _ensure_model(self):
        if self.model is not None:
            return self.model
        from ultralytics import YOLO
        mp = _default_model_path()
        if not os.path.exists(mp):
            QMessageBox.critical(self, "Model missing", f"best.pt not found at:\n{mp}")
            return None
        self.statusBar().showMessage(f"Loading model from {mp} ...")
        self.model = YOLO(mp)
        return self.model

    def action_analyze(self):
        if not self.raster_path or self.roi_poly is None:
            return
        model = self._ensure_model()
        if model is None:
            return

        conf = self.conf_slider.value() / 100.0
        out_dir = self.out_dir_label.text()
        self._last_out_dir = out_dir
        self.btn_analyze.setEnabled(False)
        self.act_analyze.setEnabled(False)
        self.progress.setRange(0, 0)
        self.statusBar().showMessage("Analyzing... (reading tiles directly from disk)")

        self._thread = QThread(self)
        self._worker = AnalyzeWorker(self.raster_path, self.roi_poly, model, conf, out_dir)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @Slot(int, int)
    def _on_progress(self, i, n):
        if self.progress.maximum() != n:
            self.progress.setRange(0, n)
        self.progress.setValue(i)
        self.statusBar().showMessage(f"Tile {i}/{n}")

    @Slot(object, object, str)
    def _on_finished(self, detections, paths, error):
        self.progress.setRange(0, 1); self.progress.setValue(1)
        self._refresh_analyze_enabled()
        if error:
            QMessageBox.critical(self, "Analysis failed", error)
            self.statusBar().showMessage("Analysis failed.")
            return

        names = getattr(self.model, "names", {}) or {}
        box_counts, plants_per_class, total = count(detections)

        self.table.setRowCount(0)
        for cls_id in sorted(PLANTS_PER_CLASS.keys()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(cls_id)))
            self.table.setItem(row, 1, QTableWidgetItem(str(names.get(cls_id, f"class_{cls_id}"))))
            self.table.setItem(row, 2, QTableWidgetItem(str(box_counts.get(cls_id, 0))))
            self.table.setItem(row, 3, QTableWidgetItem(str(PLANTS_PER_CLASS[cls_id])))
            self.table.setItem(row, 4, QTableWidgetItem(str(plants_per_class.get(cls_id, 0))))
        self.total_label.setText(f"Total plants: {total}")

        # Per-plot counts (only if ROI came from a shapefile/GDB with >1 plot).
        plot_csv_path = None
        plot_extra_status = ""
        if self.plots and len(self.plots) > 1:
            per_plot, unmatched = count_per_plot(detections, self.plots)
            self._populate_plot_table(per_plot, names)
            try:
                plot_csv_path = save_plot_counts_csv(
                    per_plot,
                    os.path.join(self._last_out_dir, f"plot_counts_{os.path.basename(self.raster_path)}.csv"),
                    model_names=names,
                )
            except Exception as e:
                plot_csv_path = None
                plot_extra_status = f" (plot CSV failed: {e})"
            if unmatched:
                plot_extra_status += f"  {unmatched} detection(s) fell outside all plots."
        else:
            self._populate_plot_table([], names)

        # Compact outputs summary.
        n_files = sum(1 for v in paths.values() if v)
        if plot_csv_path:
            n_files += 1
        out_dir = self._last_out_dir or "(unknown)"
        self.export_summary.setText(f"{n_files} file(s) written to: {out_dir}")
        self.export_summary.setToolTip(out_dir)

        # load annotated preview into dock
        preview = paths.get("preview_jpg")
        if preview and os.path.exists(preview):
            self._preview_path = preview
            self._preview_pixmap = QPixmap(preview)
            self._rescale_preview()
            self.preview_label.setToolTip(preview)
        else:
            self._preview_path = None
            self._preview_pixmap = None
            self.preview_label.setText("(no preview produced)")

        self.statusBar().showMessage(
            f"Done. {len(detections)} detections kept. {total} plants." + plot_extra_status
        )

    def _populate_plot_table(self, per_plot, names):
        classes = sorted(PLANTS_PER_CLASS.keys())
        headers = ["Plot ID"] + [str(names.get(c, f"class_{c}")) for c in classes] + ["Total"]
        self.plot_table.setColumnCount(len(headers))
        self.plot_table.setHorizontalHeaderLabels(headers)
        self.plot_table.setRowCount(len(per_plot))
        for row, r in enumerate(per_plot):
            self.plot_table.setItem(row, 0, QTableWidgetItem(str(r["plot_id"])))
            for i, c in enumerate(classes, start=1):
                self.plot_table.setItem(row, i, QTableWidgetItem(str(r["per_class_plants"].get(c, 0))))
            self.plot_table.setItem(row, len(classes) + 1, QTableWidgetItem(str(r["total_plants"])))
