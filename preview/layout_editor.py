"""Transactional editing of the first layout copy in the preview."""
from math import hypot

from enaml.qt.QtCore import QPointF, Qt
from enaml.qt.QtGui import QPainterPath, QTransform, QPen, QColor
from enaml.qt.QtWidgets import QMenu
from pyqtgraph.graphicsItems.GraphicsObject import GraphicsObject
from pyqtgraph.graphicsItems.ViewBox.ViewBox import ViewBox

from inkcut.job.layout import affine_from_points, invert_affine, hit_test, snap_rect


def point_tuple(point):
    return (point.x(), point.y()) if hasattr(point, 'x') else tuple(point)


def display_mapper(job, device):
    def map_point(point):
        x, y = point_tuple(point)
        path = QPainterPath()
        path.moveTo(x, -y)
        path.lineTo(x + 1, -y)
        path = job.layout_transform.map(path) if job.layout_transform else path
        if device is not None:
            path = device.transform(path)
        element = path.elementAt(0)
        return element.x, -element.y
    return map_point


class LayoutEditor:
    """Mouse-independent controller. Only end_drag commits a drag."""

    def __init__(self, job, to_display):
        self.job = job
        self.to_display = to_display
        self.selected_uid = None
        self.cancel_drag()
        self.refresh_transform()

    def refresh_transform(self):
        basis = [(0., 0.), (1., 0.), (0., 1.)]
        coefficients = affine_from_points(basis, [point_tuple(self.to_display(p)) for p in basis])
        self.transform = QTransform(*coefficients)
        inverse = invert_affine(coefficients)
        self.inverse = QTransform(*inverse) if inverse is not None else None
        self.inverse_scale = (max(hypot(inverse[0], inverse[1]),
                                  hypot(inverse[2], inverse[3])) if inverse else 0)

    def valid_items(self):
        return [item for item in self.job.items
                if self.job.item_bounds(item) is not None and not item.missing and not item._invalid]

    def item_rects_display(self):
        return [self.transform.mapRect(self.job.item_bounds(item)) for item in self.valid_items()]

    def item_at(self, display_pt, view_px=1.0):
        if self.inverse is None:
            return None
        rects = []
        for rect in self.item_rects_display():
            # A minimum four-pixel target, including lines and points.
            dx = max(0., 4 * view_px - rect.width()) / 2
            dy = max(0., 4 * view_px - rect.height()) / 2
            rects.append(rect.adjusted(-dx, -dy, dx, dy).getRect())
        index = hit_test(point_tuple(display_pt), rects)
        return self.valid_items()[index] if index is not None else None

    def begin_drag(self, display_pt, view_px=1.0):
        self.cancel_drag()
        item = self.item_at(display_pt, view_px)
        if item is None:
            return False
        self.item = item
        self.selected_uid = item.uid
        self.start_position = tuple(item.position)
        self.grip = self.inverse.map(QPointF(*point_tuple(display_pt)))
        self.pending_position = self.start_position
        return True

    def drag_to(self, display_pt, snap=True, view_px=1.0):
        if self.item is None:
            return
        point = self.inverse.map(QPointF(*point_tuple(display_pt)))
        x = self.start_position[0] + point.x() - self.grip.x()
        y = self.start_position[1] + point.y() - self.grip.y()
        if snap:
            rect = self.job.item_bounds(self.item)
            ox, oy = rect.x() - self.item.position[0], rect.y() - self.item.position[1]
            area = self.job.material.available_area
            edges_x, edges_y = [0., area.width()], [0., area.height()]
            for other in self.valid_items():
                if other is self.item:
                    continue
                r = self.job.item_bounds(other)
                edges_x.extend((r.left(), r.center().x(), r.right()))
                edges_y.extend((r.top(), r.center().y(), r.bottom()))
            x, y = snap_rect(x + ox, y + oy, rect.width(), rect.height(),
                             self.job.item_snap_grid, edges_x, edges_y,
                             8 * view_px * self.inverse_scale)
            x, y = x - ox, y - oy
        self.pending_position = (x, y)

    def end_drag(self):
        item, position = self.item, self.pending_position
        self.cancel_drag()
        if item is not None and item in self.job.items and tuple(item.position) != position:
            item.position = list(position)

    def cancel_drag(self):
        self.item = None
        self.start_position = self.grip = self.pending_position = None

    def toggle_weedline(self, item):
        item.weedline = not item.weedline

    def rotate_90(self, item):
        rect = self.job.item_bounds(item, outer=False)
        if rect is None:
            return
        rotation = (item.rotation + 90) % 360
        new = self.job.item_bounds(item, outer=False, rotation=rotation)
        position = [round(rect.center().x() - new.width() / 2, 9),
                    round(rect.center().y() - new.height() / 2, 9)]
        with self.job.batch_items():
            item.rotation = rotation
            if item.position != position:
                item.position = position

    def remove(self, item):
        self.job.items.remove(item)

    def arrange_all(self):
        self.job.arrange_items()


class LayoutEditorItem(GraphicsObject):
    """Thin pyqtgraph event/painting adapter; no model writes during motion."""
    skip_autorange = True

    def __init__(self, job, device=None):
        super().__init__()
        self.editor = LayoutEditor(job, display_mapper(job, device))
        self.device = device
        self.hover_uid = None
        self.ghost = None
        self._disposed = False
        self.setZValue(100)
        self.setFlag(self.ItemIsFocusable, True)
        for name in ('model', 'items', 'layout_problems'):
            job.observe(name, self._refresh)
        if device is not None:
            device.observe('config', self._device_config_changed)
            self._observe_config(True)

    def _observe_config(self, attach):
        method = self.device.config.observe if attach else self.device.config.unobserve
        for name in ('scale', 'rotation'):
            method(name, self._refresh)

    def _device_config_changed(self, change):
        old = change.get('oldvalue')
        if old is not None:
            for name in ('scale', 'rotation'):
                old.unobserve(name, self._refresh)
        self._observe_config(True)
        self._refresh(change)

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.editor.cancel_drag()
        for name in ('model', 'items', 'layout_problems'):
            self.editor.job.unobserve(name, self._refresh)
        if self.device is not None:
            self.device.unobserve('config', self._device_config_changed)
            self._observe_config(False)

    def _refresh(self, change):
        self.prepareGeometryChange()
        self.editor.cancel_drag()
        self.ghost = None
        self.editor.refresh_transform()
        if self.editor.selected_uid not in {it.uid for it in self.editor.job.items}:
            self.editor.selected_uid = None
        self.update()

    def view_px(self):
        vb = self.getViewBox()
        return max(abs(v) for v in vb.viewPixelSize()) if vb is not None else 1.0

    def shape(self):
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        px = self.view_px()
        for rect in self.editor.item_rects_display():
            dx = max(0., 4 * px - rect.width()) / 2
            dy = max(0., 4 * px - rect.height()) / 2
            path.addRect(rect.adjusted(-dx, -dy, dx, dy))
        return path

    def boundingRect(self):
        rect = self.shape().boundingRect()
        if self.editor.item is not None:
            rect = rect.united(self._pending_rect())
        return rect

    def _delta(self):
        e = self.editor
        return (e.transform.map(QPointF(*e.pending_position)) -
                e.transform.map(QPointF(*e.start_position)))

    def _pending_rect(self):
        return self.editor.transform.mapRect(
            self.editor.job.item_bounds(self.editor.item)).translated(self._delta())

    def paint(self, painter, option, widget=None):
        e = self.editor
        problems = {uid for uid, _ in e.job.layout_problems}
        painter.setBrush(Qt.NoBrush)
        for item, rect in zip(e.valid_items(), e.item_rects_display()):
            color = ('#d32f2f' if item.uid in problems else
                     '#1976d2' if item.uid in (e.selected_uid, self.hover_uid) else '#777777')
            pen = QPen(QColor(color))
            pen.setCosmetic(True)
            pen.setWidth(2 if item.uid == e.selected_uid else 1)
            painter.setPen(pen)
            painter.drawRect(rect)
            if item is e.item:
                pen.setStyle(Qt.DashLine)
                painter.setPen(pen)
                painter.drawRect(self._pending_rect())
                if self.ghost is not None:
                    painter.drawPath(self.ghost.translated(self._delta()))

    def _start_ghost(self):
        e = self.editor
        path = e.job.item_cut_path(e.item)
        if self.device is not None:
            path = self.device.transform(path)
        self.ghost = QTransform.fromScale(1, -1).map(path)

    def mouseDragEvent(self, ev):
        vb = self.getViewBox()
        if ev.button() != Qt.LeftButton or (vb is not None and vb.state['mouseMode'] == ViewBox.RectMode):
            ev.ignore()
            return
        e = self.editor
        if ev.isStart():
            if not e.begin_drag(ev.buttonDownPos(), self.view_px()):
                ev.ignore()
                return
            self.prepareGeometryChange()
            self._start_ghost()
            self.setFocus(Qt.MouseFocusReason)
        if e.item is None:
            ev.ignore()
            return
        ev.accept()
        self.prepareGeometryChange()
        e.drag_to(ev.pos(), snap=not bool(ev.modifiers() & Qt.AltModifier), view_px=self.view_px())
        if ev.isFinish():
            e.end_drag()
            self.ghost = None
        self.update()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape and self.editor.item is not None:
            self.prepareGeometryChange()
            self.editor.cancel_drag()
            self.ghost = None
            self.update()
            ev.accept()
        else:
            ev.ignore()

    def hoverEvent(self, ev):
        item = None if ev.isExit() else self.editor.item_at(ev.pos(), self.view_px())
        self.hover_uid = item.uid if item else None
        self.update()

    def mouseClickEvent(self, ev):
        item = self.editor.item_at(ev.pos(), self.view_px())
        if item is None or ev.button() not in (Qt.LeftButton, Qt.RightButton):
            ev.ignore()
            return
        ev.accept()
        self.editor.selected_uid = item.uid
        self.update()
        if ev.button() == Qt.RightButton:
            self.show_menu(item, ev.screenPos())

    def show_menu(self, item, screen_pos):
        menu = QMenu()
        weedline = menu.addAction('Weedline around this graphic')
        weedline.setCheckable(True)
        weedline.setChecked(item.weedline)
        weedline.triggered.connect(lambda: self.editor.toggle_weedline(item))
        menu.addAction('Rotate 90°', lambda: self.editor.rotate_90(item))
        menu.addAction('Remove', lambda: self.editor.remove(item))
        menu.addSeparator()
        menu.addAction('Arrange all automatically', self.editor.arrange_all)
        menu.exec_(QPointF(*point_tuple(screen_pos)).toPoint())
