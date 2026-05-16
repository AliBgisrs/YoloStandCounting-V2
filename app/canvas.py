"""Map canvas: QGraphicsView showing the raster overview with ROI drawing.

The overview is a downsampled image; ROI shapes drawn here are converted to
full-res pixel coords via `scale` (overview_px / full_res_px).
"""
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QImage, QPixmap, QPen, QColor, QBrush, QPainterPath, QPolygonF, QPainter
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsPolygonItem,
)
from shapely.geometry import Polygon, MultiPolygon


MODE_PAN = "pan"
MODE_RECT = "rect"
MODE_POLY = "poly"


class MapCanvas(QGraphicsView):
    roi_changed = Signal(object)  # shapely MultiPolygon in full-res pixel coords (or None)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)

        self._pix_item = None
        self._overview_size = (0, 0)
        self._scale = 1.0  # overview_px / full_res_px

        self._mode = MODE_PAN
        self._roi_item = None
        self._rect_start = None
        self._poly_pts = []
        self._poly_preview = None

    def clear_image(self):
        self._scene.clear()
        self._pix_item = None
        self._roi_item = None
        self._poly_preview = None
        self._poly_pts = []

    def set_overview(self, rgb_array, scale):
        """rgb_array: (H, W, 3) uint8. scale: overview_px / full_res_px."""
        self.clear_image()
        h, w, _ = rgb_array.shape
        img = QImage(rgb_array.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(img)
        self._pix_item = QGraphicsPixmapItem(pix)
        self._pix_item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._pix_item)
        self._scene.setSceneRect(QRectF(0, 0, w, h))
        self._overview_size = (w, h)
        self._scale = scale
        self.fitInView(self._pix_item, Qt.KeepAspectRatio)

    def set_mode(self, mode):
        self._mode = mode
        if mode == MODE_PAN:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)
        self._reset_partial()

    def _reset_partial(self):
        self._rect_start = None
        self._poly_pts = []
        if self._poly_preview is not None:
            self._scene.removeItem(self._poly_preview)
            self._poly_preview = None

    def clear_roi(self):
        if self._roi_item is not None:
            self._scene.removeItem(self._roi_item)
            self._roi_item = None
        self._reset_partial()
        self.roi_changed.emit(None)

    def wheelEvent(self, event):
        if self._pix_item is None:
            return
        zoom = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.scale(zoom, zoom)

    def mousePressEvent(self, event):
        if self._pix_item is None or self._mode == MODE_PAN:
            return super().mousePressEvent(event)
        pt = self.mapToScene(event.position().toPoint())
        if self._mode == MODE_RECT and event.button() == Qt.LeftButton:
            self._rect_start = pt
            self._set_roi_rect(QRectF(pt, pt))
        elif self._mode == MODE_POLY:
            if event.button() == Qt.LeftButton:
                self._poly_pts.append(pt)
                self._update_poly_preview()
            elif event.button() == Qt.RightButton:
                self._finish_polygon()

    def mouseMoveEvent(self, event):
        if self._pix_item is None:
            return super().mouseMoveEvent(event)
        if self._mode == MODE_RECT and self._rect_start is not None:
            pt = self.mapToScene(event.position().toPoint())
            self._set_roi_rect(QRectF(self._rect_start, pt).normalized())
        elif self._mode == MODE_POLY and self._poly_pts:
            self._update_poly_preview(self.mapToScene(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._mode == MODE_RECT and self._rect_start is not None and event.button() == Qt.LeftButton:
            pt = self.mapToScene(event.position().toPoint())
            self._set_roi_rect(QRectF(self._rect_start, pt).normalized())
            self._rect_start = None
            self._emit_roi()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._mode == MODE_POLY and len(self._poly_pts) >= 3:
            self._finish_polygon()
        else:
            super().mouseDoubleClickEvent(event)

    def _set_roi_rect(self, qrect):
        if self._roi_item is not None:
            self._scene.removeItem(self._roi_item)
        item = QGraphicsRectItem(qrect)
        item.setPen(QPen(QColor(255, 64, 64), 2))
        item.setBrush(QBrush(QColor(255, 64, 64, 48)))
        self._scene.addItem(item)
        self._roi_item = item

    def _update_poly_preview(self, hover_pt=None):
        if self._poly_preview is not None:
            self._scene.removeItem(self._poly_preview)
        pts = list(self._poly_pts)
        if hover_pt is not None:
            pts = pts + [hover_pt]
        if len(pts) < 2:
            self._poly_preview = None
            return
        poly = QPolygonF(pts)
        item = QGraphicsPolygonItem(poly)
        item.setPen(QPen(QColor(255, 64, 64), 2, Qt.DashLine))
        item.setBrush(QBrush(QColor(255, 64, 64, 32)))
        self._scene.addItem(item)
        self._poly_preview = item

    def _finish_polygon(self):
        if len(self._poly_pts) < 3:
            self._reset_partial()
            return
        if self._roi_item is not None:
            self._scene.removeItem(self._roi_item)
        if self._poly_preview is not None:
            self._scene.removeItem(self._poly_preview)
            self._poly_preview = None
        poly = QPolygonF(self._poly_pts)
        item = QGraphicsPolygonItem(poly)
        item.setPen(QPen(QColor(255, 64, 64), 2))
        item.setBrush(QBrush(QColor(255, 64, 64, 48)))
        self._scene.addItem(item)
        self._roi_item = item
        self._poly_pts = []
        self._emit_roi()

    def show_polygon_from_full_res(self, multipoly):
        """Display a polygon (in full-res pixels) on the overview."""
        if self._pix_item is None or multipoly is None or multipoly.is_empty:
            return
        if self._roi_item is not None:
            self._scene.removeItem(self._roi_item)
        path = QPainterPath()
        polys = multipoly.geoms if multipoly.geom_type == "MultiPolygon" else [multipoly]
        for poly in polys:
            pts = [QPointF(x * self._scale, y * self._scale)
                   for x, y in poly.exterior.coords]
            path.addPolygon(QPolygonF(pts))
        from PySide6.QtWidgets import QGraphicsPathItem
        item = QGraphicsPathItem(path)
        item.setPen(QPen(QColor(64, 160, 255), 2))
        item.setBrush(QBrush(QColor(64, 160, 255, 48)))
        self._scene.addItem(item)
        self._roi_item = item

    def _emit_roi(self):
        if self._roi_item is None or self._scale <= 0:
            self.roi_changed.emit(None)
            return
        inv = 1.0 / self._scale
        if isinstance(self._roi_item, QGraphicsRectItem):
            r = self._roi_item.rect()
            x1, y1, x2, y2 = r.left() * inv, r.top() * inv, r.right() * inv, r.bottom() * inv
            poly = Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)])
        elif isinstance(self._roi_item, QGraphicsPolygonItem):
            qpoly = self._roi_item.polygon()
            pts = [(p.x() * inv, p.y() * inv) for p in qpoly]
            if len(pts) < 3:
                self.roi_changed.emit(None)
                return
            poly = Polygon(pts)
        else:
            self.roi_changed.emit(None)
            return
        if not poly.is_valid:
            poly = poly.buffer(0)
        self.roi_changed.emit(MultiPolygon([poly]) if poly.geom_type == "Polygon" else poly)
