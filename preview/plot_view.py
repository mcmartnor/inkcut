# -*- coding: utf-8 -*-
"""
Copyright (c) 2017, Jairus Martin.

Distributed under the terms of the GPL v3 License.

The full license is in the file LICENSE, distributed with this software.

Created on Jun 11, 2015

@author: jrm
"""
from atom.atom import set_default
from atom.api import (
    Callable, Tuple, Float, List, ContainerList, Bool, FloatRange, Str,
    Dict, Typed, ForwardTyped, Enum, observe
)
from enaml.core.declarative import d_
from enaml.qt.qt_application import QtApplication
from enaml.qt.qt_control import QtControl
from enaml.qt import QtGui
from enaml.qt.QtCore import QEvent
from enaml.widgets.control import Control, ProxyControl
from pyqtgraph.widgets.PlotWidget import PlotWidget
from pyqtgraph.graphicsItems.PlotCurveItem import PlotCurveItem
from pyqtgraph.graphicsItems.ViewBox.ViewBox import ViewBox
from pyqtgraph.graphicsItems.AxisItem import AxisItem
from pyqtgraph.graphicsItems.GraphicsObject import GraphicsObject


def zoom_percent_for_span(base_width, x_span):
    try:
        base_width = float(base_width)
        x_span = float(x_span)
    except (TypeError, ValueError):
        return 0.0
    if base_width <= 0 or x_span <= 0:
        return 0.0
    return 100.0 * base_width / x_span


def x_range_for_percent(base_width, percent, center):
    try:
        base_width = float(base_width)
        percent = float(percent)
        center = float(center)
    except (TypeError, ValueError):
        return None
    if base_width <= 0 or percent <= 0:
        return None
    span = base_width * 100.0 / percent
    return center - span / 2.0, center + span / 2.0


class PainterPathPlotItem(PlotCurveItem):

    def updateData(self, path, **kargs):
        # Invert for display
        self.path = QtGui.QTransform.fromScale(1, -1).map(path)

        # Trick the checks so it still paints
        bbox = self.path.boundingRect()
        self.xData = [bbox.left(), bbox.right()]
        self.yData = [bbox.bottom(), bbox.top()]

        if 'skip_autorange' in kargs:
            skip = (True, True)
            if isinstance(kargs['skip_autorange'], (list, tuple)):
                skip = kargs['skip_autorange']
            if skip[0]:
                if isinstance(skip[0], (list, tuple)):
                    self.xData = skip[0]
                else:
                    self.xData = [0]
            if skip[1]:
                if isinstance(skip[1], (list, tuple)):
                    self.yData = skip[1]
                else:
                    self.yData = [0]

        ##    Test this bug with test_PlotWidget and zoom in on the animated plot
        self.invalidateBounds()
        self.prepareGeometryChange()
        self.informViewBoundsChanged()

        self.fillPath = None
        self._mouseShape = None

        if 'name' in kargs:
            self.opts['name'] = kargs['name']
        if 'connect' in kargs:
            self.opts['connect'] = kargs['connect']
        if 'pen' in kargs:
            self.setPen(kargs['pen'])
        if 'shadowPen' in kargs:
            self.setShadowPen(kargs['shadowPen'])
        if 'fillLevel' in kargs:
            self.setFillLevel(kargs['fillLevel'])
        if 'brush' in kargs:
            self.setBrush(kargs['brush'])
        if 'antialias' in kargs:
            self.opts['antialias'] = kargs['antialias']

        self.update()
        self.sigPlotChanged.emit(self)

    def boundingRect(self):
        return self.path.boundingRect()

    def getPath(self):
        return self.path


class ProxyPlotView(ProxyControl):
    declaration = ForwardTyped(lambda: PlotView)

    def set_data(self, data):
        raise NotImplementedError

    def set_overlay(self, overlay):
        raise NotImplementedError

    def set_antialiasing(self, enabled):
        raise NotImplementedError

    def set_zoom(self, percent):
        raise NotImplementedError

    def set_mouse_mode(self, mode):
        raise NotImplementedError


class PlotView(Control):
    hug_width = set_default('ignore')
    hug_height = set_default('ignore')
    proxy = Typed(ProxyPlotView)
    data = d_(ContainerList())
    overlay = d_(Typed(object))
    setup = d_(Callable(lambda graph: None))

    title = d_(Str())
    labels = d_(Dict(Str(), Str()))

    axis_scales = d_(Dict(Str(), Float()))

    #background_color = d_(Str())
    #foreground = d_(Str())

    antialiasing = d_(Bool(True))
    aspect_locked = d_(Bool(True))

    #: Nåværende zoom i prosent av materialbredden. Skrives av proxyen
    #: ved hver range-endring; kun for visning. IKKE i _update_proxy-lista.
    zoom_percent = d_(Float(0.0))
    #: Leverandør av 100 %-grunnlaget (materialbredde i px). Settes av view.enaml.
    zoom_base = d_(Callable(lambda: 0.0))
    mouse_mode = d_(Enum('pan', 'rect'))

    grid = d_(Tuple(item=Bool(), default=(False, False)))
    grid_alpha = d_(FloatRange(low=0.0, high=1.0, value=0.5))

    multi_axis = d_(Bool(True))

    @observe('data', 'title', 'labels', 'multi_axis', 'antialiasing',
             'axis_scales', 'grid', 'grid_alpha', 'mouse_mode', 'overlay')
    def _update_proxy(self, change):
        """ An observer which sends state change to the proxy.
        """
        if change['name'] == 'overlay' and not self.proxy_is_active:
            old, new = change.get('oldvalue'), change.get('value')
            if old is not None and old is not new:
                old.dispose()
        super(PlotView, self)._update_proxy(change)


class LayoutPlotWidget(PlotWidget):
    """Suppress QWidget's separate context menu only over editable graphics."""
    layout_overlay = None

    def _item_context_event(self, event, viewport=False):
        overlay = self.layout_overlay
        if event.type() != QEvent.ContextMenu or overlay is None:
            return False
        pos = event.pos() if viewport else self.viewport().mapFrom(self, event.pos())
        point = overlay.mapFromScene(self.mapToScene(pos))
        if overlay.editor.item_at(point, overlay.view_px()) is None:
            return False
        event.accept()
        return True

    def event(self, event):
        if self._item_context_event(event):
            return True
        return super().event(event)

    def viewportEvent(self, event):
        if self._item_context_event(event, viewport=True):
            return True
        return super().viewportEvent(event)


class QtPlotView(QtControl, ProxyPlotView):
    __weakref__ = None
    widget = Typed(PlotWidget)
    _views = List()
    _colors = List(default=['r', 'g', 'b'])

    def create_widget(self):
        self.widget = LayoutPlotWidget(self.parent_widget(), background='w')

    def destroy(self):
        if self.widget is not None:
            self.set_overlay(None)
        elif self.declaration.overlay is not None:
            self.declaration.overlay.dispose()
        super().destroy()

    def set_overlay(self, overlay):
        old = self.widget.layout_overlay
        if old is not None and old is not overlay:
            self.widget.removeItem(old)
            old.dispose()
        self.widget.layout_overlay = overlay
        if overlay is not None and overlay not in self.widget.plotItem.items:
            self.widget.addItem(overlay, ignoreBounds=overlay.skip_autorange)

    def init_widget(self):
        super(QtPlotView, self).init_widget()
        d = self.declaration
        #self.widget.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        self.set_data(d.data)
        self.set_antialiasing(d.antialiasing)
        self.set_mouse_mode(d.mouse_mode)
        self.set_aspect_locked(d.aspect_locked)
        self.set_axis_scales(d.axis_scales)
        self.set_labels(d.labels)
        self.widget.showGrid(d.grid[0], d.grid[1], d.grid_alpha)

        d.setup(self.widget)
        self.widget.plotItem.vb.sigRangeChanged.connect(
            self._on_range_changed)
        self._on_range_changed()

    def _on_range_changed(self, *args):
        d = self.declaration
        if d is None:
            return
        x0, x1 = self.widget.plotItem.vb.viewRange()[0]
        try:
            base = d.zoom_base()
        except Exception:
            return
        d.zoom_percent = zoom_percent_for_span(base, x1 - x0)

    def set_zoom(self, percent):
        d = self.declaration
        base = d.zoom_base()
        percent = max(1.0, min(6400.0, percent))
        vb = self.widget.plotItem.vb
        x0, x1 = vb.viewRange()[0]
        rng = x_range_for_percent(base, percent, (x0 + x1) / 2.0)
        if rng is None:
            return
        vb.setRange(xRange=rng, padding=0)

    def set_mouse_mode(self, mode):
        vb = self.widget.plotItem.vb
        vb.setMouseMode(ViewBox.RectMode if mode == 'rect' else ViewBox.PanMode)

    def set_title(self, title):
        self.set_labels(self.declaration.labels)

    def set_labels(self, labels):
        if self.declaration.title:
            labels['title'] = self.declaration.title
        self.widget.setLabels(**labels)

    def set_antialiasing(self, enabled):
        self.widget.setAntialiasing(enabled)

    def set_aspect_locked(self, locked):
        self.widget.setAspectLocked(locked)

    def set_axis_scales(self, scales):
        if not scales:
            return
        for k, v in scales.items():
            if k in self.widget.plotItem.axes:
                self.widget.plotItem.axes[k]['item'].setScale(v)

    def set_grid(self, grid):
        d = self.declaration
        self.widget.showGrid(grid[0], grid[1], d.grid_alpha)

    def set_grid_alpha(self, alpha):
        d = self.declaration
        self.widget.showGrid(d.grid[0], d.grid[1], alpha)

    def set_data(self, data):
        self.widget.clear()
        if data:
            if isinstance(data, (list, tuple)) and \
                    isinstance(data[0], GraphicsObject):
                self._set_graphic_items(data)
            else:
                self._set_numeric_data(data)
        self.set_overlay(self.declaration.overlay)

    def _set_graphic_items(self, items):
        self.widget.clear()
        for item in items:
            self.widget.addItem(item)

    def _set_numeric_data(self, data):
        self.widget.plotItem.clear()
        if self._views:
            for view in self._views:
                view.clear()

        views = []
        i = 0
        if self.declaration.multi_axis:
            for i, plot in enumerate(data):
                if i > 3:
                    break
                if 'pen' not in plot:
                    plot['pen'] = self._colors[i]
                if i > 0:
                    view = ViewBox()
                    views.append(view)
                    self.widget.plotItem.scene().addItem(view)
                    if i == 1:
                        axis = self.widget.plotItem.getAxis('right')
                    elif i > 1:
                        axis = AxisItem('right')
                        axis.setZValue(-10000)
                        self.widget.plotItem.layout.addItem(axis, 2, 3)
                    axis.linkToView(view)
                    view.setXLink(self.widget.plotItem)
                    view.addItem(PlotCurveItem(**plot))
                else:
                    self.widget.plot(**plot)
        if i > 0:
            def syncViews():
                for v in views:
                    v.setGeometry(self.widget.plotItem.vb.sceneBoundingRect())
                    v.linkedViewChanged(self.widget.plotItem.vb, v.XAxis)
            syncViews()
            self.widget.plotItem.vb.sigResized.connect(syncViews)
        self._views = views


def plot_view_factory():
    return QtPlotView


# Inject the factory
QtApplication.instance().resolver.factories.update({
    'PlotView': plot_view_factory
})
