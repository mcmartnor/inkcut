"""
Copyright (c) 2017, Jairus Martin.

Distributed under the terms of the GPL v3 License.

The full license is in the file LICENSE, distributed with this software.

Created on Jul 12, 2015

@author: jrm
"""
import pyqtgraph as pg
from atom.api import List, Instance, Enum, Bool, Range
from enaml.qt import QtCore, QtGui
from inkcut.core.api import Plugin, Model, unit_conversions, log
from inkcut.core.theme import Theme, make_pens, PEN_ROLES, MEDIA_FILL
from .plot_view import PainterPathPlotItem


QPen = QtGui.QPen


def theme_pen(role):
    theme = Theme.instance()
    return make_pens(theme is not None and theme.dark)[role]


class PreviewModel(Model):
    #: List of plot items to display
    plot = List()

    #: Internal paths for drawing
    paths = List(QtGui.QPainterPath)

    #: Colors
    pen_media = Instance(QPen)
    pen_media_padding = Instance(QPen)
    pen_up = Instance(QPen)
    pen_offset = Instance(QPen)
    pen_down = Instance(QPen)
    pen_device = Instance(QPen)

    #: The sheet (material), drawn under the lines
    brush_media = Instance(QtGui.QBrush)

    #: (curve, role) of the plotted curves drawn with one of the pens above
    pen_roles = List()

    def _default_pen_media(self):
        return theme_pen('media')

    def _default_pen_media_padding(self):
        return theme_pen('media_padding')

    def _default_pen_device(self):
        return theme_pen('device')

    def _default_pen_up(self):
        return theme_pen('up')

    def _default_pen_offset(self):
        return theme_pen('offset')

    def _default_pen_down(self):
        return theme_pen('down')

    def _default_brush_media(self):
        return QtGui.QBrush(QtGui.QColor(MEDIA_FILL))

    def roles_of(self, curves, items):
        """ The (curve, role) pairs of curves made with one of the pens """
        roles = []
        for curve, kwargs in zip(curves, items):
            pen = kwargs.get('pen')
            for role in PEN_ROLES:
                if pen is not None and pen is getattr(self, 'pen_' + role):
                    roles.append((curve, role))
                    break
        return roles

    def init(self, view_items):
        default_items = []
        self.paths = [QtGui.QPainterPath(), QtGui.QPainterPath()]

        default_items.append(PainterPathPlotItem(
            self.paths[0], pen=self.pen_down))
        default_items.append(PainterPathPlotItem(
            self.paths[1], pen=self.pen_up))
        self.pen_roles = [(default_items[0], 'down'), (default_items[1], 'up')]
        self.plot = default_items + view_items

    def update(self, position):
        """ Watch the position of the device as it changes. """
        if not self.paths:
            return
        x, y, z = position
        if z:
            self.paths[0].lineTo(x, -y)
            self.paths[1].moveTo(x, -y)
            self.plot[0].updateData(self.paths[0])
        else:
            self.paths[0].moveTo(x, -y)
            self.paths[1].lineTo(x, -y)
            self.plot[1].updateData(self.paths[1])


class PreviewPlugin(Plugin):

    #: Set's the plot that is drawn in the preview
    preview = Instance(PreviewModel, ())

    #: Plot for showing live status
    live_preview = Instance(PreviewModel, ())

    #: Transform applied to all view items
    transform = Instance(QtGui.QTransform)

    show_grid_x = Bool().tag(config=True)
    show_grid_y = Bool().tag(config=True)
    grid_alpha = Range(value=30, low=1, high=100).tag(config=True)

    _theme = Instance(Theme)

    def start(self):
        super(PreviewPlugin, self).start()
        self._theme = Theme.instance()
        if self._theme is not None:
            self._theme.observe('revision', self._theme_changed)

    def stop(self):
        if self._theme is not None:
            self._theme.unobserve('revision', self._theme_changed)
            self._theme = None
        super(PreviewPlugin, self).stop()

    def _theme_changed(self, change):
        """ Give the models and their plotted curves the pens of the
        new palette, without rebuilding the plot.

        """
        pens = make_pens(self._theme.dark)
        for model in (self.preview, self.live_preview):
            for role in PEN_ROLES:
                setattr(model, 'pen_' + role, pens[role])
            for curve, role in model.pen_roles:
                try:
                    curve.setPen(getattr(model, 'pen_' + role))
                except RuntimeError:
                    pass  # Qt already deleted it with a closed plot

    def _default_transform(self):
        """ Qt displays top to bottom so this can be used to flip it.

        """
        return QtGui.QTransform.fromScale(1, -1)

    def set_preview(self, *items):
        """ Sets the items that will be displayed in the plot

        Parameters
        ----------
        items: list of kwargs
            A list of kwargs to to pass to each plot item

        """
        t = self.transform
        view_items = [
            PainterPathPlotItem(kwargs.pop('path'), **kwargs)
            for kwargs in items
        ]
        self.preview.pen_roles = self.preview.roles_of(view_items, items)
        self.preview.plot = view_items

    def set_live_preview(self, *items):
        """ Set the items that will be displayed in the live plot preview.
        After set, use live_preview.update(position) to update it.

        Parameters
        ----------
        items: list of kwargs
            A list of kwargs to to pass to each plot item


        """
        view_items = [
            PainterPathPlotItem(kwargs.pop('path'), **kwargs)
            for kwargs in items
        ]
        roles = self.live_preview.roles_of(view_items, items)
        self.live_preview.init(view_items)
        self.live_preview.pen_roles += roles

