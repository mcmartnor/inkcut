"""Light or dark colors that follow the Qt application palette."""
import pyqtgraph as pg
from atom.api import Atom, Bool, Int, Value
from enaml.qt.QtCore import QObject, QEvent, Qt
from enaml.qt.QtGui import QColor, QPalette
from enaml.qt.QtWidgets import QApplication


def is_dark(palette):
    return palette.color(QPalette.Window).lightness() < 128


#: Pens drawn dashed
PEN_STYLES = {
    'media_padding': Qt.DashLine,
    'device': Qt.DashLine,
}

#: The sheet (material) is white paper in both palettes
MEDIA_FILL = '#ffffff'

PEN_ROLES = ('media', 'media_padding', 'device', 'up', 'offset', 'down')


def preview_colors(dark, base=None):
    if not dark:
        return {
            'background': 'w',
            'media': (128, 128, 128),
            'media_padding': (128, 128, 128),
            'device': (235, 194, 194),
            'up': {'hsv': (0.53, 1, 0.5, 0.5)},
            'offset': {'hsv': (0.43, 1, 0.5, 0.5)},
            'down': (128, 128, 128),
        }
    return {
        'background': QColor('#1e1e1e') if base is None else base,
        'media': (128, 128, 128),
        'media_padding': (128, 128, 128),
        'device': (235, 194, 194),
        'up': {'hsv': (0.53, 1, 0.5, 0.5)},
        'offset': {'hsv': (0.43, 1, 0.5, 0.5)},
        'down': (128, 128, 128),
    }


def make_pens(dark):
    colors = preview_colors(dark)
    pens = {}
    for role in PEN_ROLES:
        color = colors[role]
        kwargs = {}
        if role in PEN_STYLES:
            kwargs['style'] = PEN_STYLES[role]
        if isinstance(color, dict):
            pens[role] = pg.mkPen(**color, **kwargs)
        else:
            pens[role] = pg.mkPen(color, **kwargs)
    return pens


def overlay_colors(dark):
    """ Layout overlay frame colors: (problem, selected, normal) """
    if dark:
        return ('#ef5350', '#64b5f6', '#9e9e9e')
    return ('#d32f2f', '#1976d2', '#777777')


#: Dark panel colors (as in Photoshop): work area, panel body, panel
#: title bar, input fields, field border and field text
PANEL_COLORS = {
    'area': '#1e1e1e',
    'panel': '#2b2b2b',
    'title': '#323232',
    'field': '#1a1a1a',
    'border': '#3c3c3c',
    'text': '#dadada',
}


def effective_dock_style(style, dark):
    return 'system' if dark else style


_THEME = None


class _PaletteFilter(QObject):
    def __init__(self, theme, qapp):
        super().__init__()
        self.theme = theme
        self.qapp = qapp

    def eventFilter(self, obj, event):
        if obj is self.qapp and event.type() == QEvent.ApplicationPaletteChange:
            self.theme._read_palette()
        return False


class Theme(Atom):
    #: Whether the application palette is dark
    dark = Bool()

    #: Increased on every application palette change
    revision = Int()

    _qapp = Value()
    _filter = Value()

    @classmethod
    def instance(cls):
        """ The theme of the running QApplication, or None without one. """
        qapp = QApplication.instance()
        if qapp is None:
            return None
        global _THEME
        theme = _THEME
        if theme is None or theme._qapp is not qapp:
            theme = Theme(_qapp=qapp)
            theme.dark = is_dark(QApplication.palette())
            theme._filter = _PaletteFilter(theme, qapp)
            qapp.installEventFilter(theme._filter)
            _THEME = theme
        return theme

    def _read_palette(self):
        self.dark = is_dark(QApplication.palette())
        self.revision += 1
